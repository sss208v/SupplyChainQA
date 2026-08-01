"""
SupplyChainRAG - 知识库API路由
============================================================
1. 知识库管理的核心流程：
   上传文档 → 解析(PDF/Word/TXT) → 切片(Chunking) → 嵌入(Embedding) → 存入Milvus

2. 文档切片策略：
   - 按语义边界切片（段落/标题/句子）
   - 保留完整的段落/章节，不切断句子
   - 附加元数据：section_title、paragraph_index

3. 切片Overlap的意义：
   相邻切片之间有重叠部分，避免关键信息被截断。
   例如Chunk Size=512, Overlap=64，意味着每个切片的前64字与上一个切片的后64字重复。
============================================================
"""
import asyncio
import logging
import os
import re
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from pydantic import BaseModel

from app.config import get_settings
from app.core.auth import (
    check_level,
    check_role,
    get_current_user_full,
)
from app.core.data_filter import PIIFilter
from app.core.milvus_client import milvus_manager
from app.core.rag_engine import rag_engine
from app.models.user import UserLevel, UserRole

# PII脱敏过滤器实例（模块级单例，避免重复创建）
_pii_filter = PIIFilter()

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge", tags=["知识库"])

# 上传文件存储目录
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ---- 请求/响应模型 ----

class KnowledgeBaseResponse(BaseModel):
    """知识库响应"""
    doc_id: str
    filename: str
    chunk_count: int = 0
    status: str = "indexed"
    security_group: list = ["admin"]


class KnowledgeListResponse(BaseModel):
    """知识库列表响应"""
    total: int
    documents: list[KnowledgeBaseResponse]


# ---- API接口 ----

# security_group 合法角色白名单：部门角色枚举 + public 通配
_VALID_SECURITY_GROUPS = {r.value for r in UserRole} | {"public"}


def _resolve_security_groups(requested: str, user_role: str) -> list[str]:
    """根据当前用户角色解析合法的文档权限组（防止 RBAC 越权）

    规则：
    - admin：可自由指定，但每项必须在白名单内（部门角色 + public），非法值拒绝
    - 非 admin：权限组强制为自身角色；仅当 ALLOW_PUBLIC_UPLOAD=True 时允许附加 public，
      其余传入值一律忽略（不能把文档挂到其他部门/admin 名下）
    """
    requested_groups = [g.strip() for g in (requested or "").split(",") if g.strip()]

    if user_role == UserRole.ADMIN.value:
        groups = requested_groups or ["admin"]
        invalid = [g for g in groups if g not in _VALID_SECURITY_GROUPS]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"非法权限角色: {invalid}，允许值: {sorted(_VALID_SECURITY_GROUPS)}",
            )
        return groups

    # 非 admin：无视前端传入的其他角色，强制降级为自身角色
    groups = [user_role]
    settings = get_settings()
    if "public" in requested_groups and settings.ALLOW_PUBLIC_UPLOAD:
        groups.append("public")
    dropped = [g for g in requested_groups if g not in groups]
    if dropped:
        logger.warning(
            f"[RBAC] 用户角色={user_role} 上传时请求权限组 {requested_groups}，"
            f"已降级为 {groups}（丢弃: {dropped}）"
        )
    return groups


@router.post("/upload", response_model=KnowledgeBaseResponse)
async def upload_document(
    file: UploadFile = File(..., description="上传的文档文件"),
    security_group: str = Form("", description="权限角色，逗号分隔；非admin用户强制为自身角色"),
    request: Request = None,
):
    """
    上传文档到知识库
    支持格式：PDF、TXT、Markdown、DOCX
    security_group: 文档可见的角色列表（由当前用户角色派生，防越权）
    """
    # RBAC：部门经理及以上可上传（employee 只读），权限组由自身角色派生
    current_user = await get_current_user_full(request)
    user_role = current_user.get("role", "")
    check_level(current_user, UserLevel.MANAGER.value)

    # 解析 security_group（服务端权威裁决，不信任前端）
    groups = _resolve_security_groups(security_group, user_role)

    # 验证文件类型
    allowed_types = [".pdf", ".txt", ".md", ".markdown", ".docx", ".doc"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {file_ext}，支持: {allowed_types}",
        )

    # 文件大小限制：分块读取累计，超限立即中断（避免大文件一次性读入内存 OOM）
    settings = get_settings()
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    chunks_buf = []
    total_size = 0
    while True:
        piece = await file.read(1024 * 1024)  # 1MB 分块
        if not piece:
            break
        total_size += len(piece)
        if total_size > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过大小限制 {settings.MAX_UPLOAD_MB}MB",
            )
        chunks_buf.append(piece)
    content = b"".join(chunks_buf)

    # 生成文档ID
    doc_id = str(uuid.uuid4())[:12]

    # 保存文件
    file_path = os.path.join(UPLOAD_DIR, f"{doc_id}_{file.filename}")
    with open(file_path, "wb") as f:
        f.write(content)

    logger.info(f"文件上传成功: {file.filename}, doc_id={doc_id}, size={total_size}B")

    try:
        # 解析文档 → 切片 → 嵌入入库：均为 CPU/IO 密集型同步操作，
        # 用 to_thread 隔离，避免阻塞事件循环（embedding 计算可达数秒）
        chunks = await asyncio.to_thread(_parse_and_chunk, file_path, file.filename, doc_id)
        result = await asyncio.to_thread(
            rag_engine.index_document, doc_id, chunks, security_group=groups
        )

        # 知识库变更 → 失效检索缓存（L1 全清 + L2 语义缓存清空），避免脏检索结果
        await _invalidate_retrieval_caches()

        return KnowledgeBaseResponse(
            doc_id=doc_id,
            filename=file.filename,
            chunk_count=result.get("chunk_count", 0),
            status="indexed",
            security_group=groups,
        )

    except Exception as e:
        logger.error(f"文档处理失败: {e}")
        raise HTTPException(status_code=500, detail="文档处理失败，请检查文件格式或稍后重试")


async def _invalidate_retrieval_caches() -> None:
    """知识库变更（上传/删除）后清理检索相关缓存，保证一致性"""
    try:
        from app.core.cache_manager import cache_manager
        cleared = cache_manager.l1_clear()
        await cache_manager.l2_invalidate()
        logger.info(f"[Knowledge] 检索缓存已失效（L1 清除 {cleared} 条 + L2 版本号递增）")
    except Exception as e:
        logger.warning(f"[Knowledge] 检索缓存失效失败（不影响入库结果）: {e}")


@router.post("/router/reload")
async def reload_intent_routes(request: Request = None):
    """重载意图路由配置（intent_routes.json）并重建语义路由 embedding

    使用场景：修改关键词/语义样本后手动触发（关键词部分本身支持 mtime 热加载，
    但语义样本变更需重新计算 embedding，必须走本端点）。
    """
    # RBAC：只有 admin 可以重载路由配置
    current_user = await get_current_user_full(request)
    check_role(current_user, [UserRole.ADMIN.value])

    from app.core.intent_routes import get_intent_routes_manager
    from app.core.semantic_router import get_semantic_router

    cfg = get_intent_routes_manager().reload()

    # 重建语义路由 embedding（embedding 计算是同步阻塞操作，放线程池）
    semantic_rebuilt = False
    try:
        sr = get_semantic_router()
        semantic_rebuilt = await asyncio.to_thread(sr.reload, rag_engine.embedding)
    except Exception as e:
        logger.warning(f"[Knowledge] 语义路由重建失败（规则层配置已生效）: {e}")

    return {
        "message": "意图路由配置已重载",
        "config_version": cfg.version,
        "tool_commands": len(cfg.tool_commands),
        "entity_rules": len(cfg.entity_rules),
        "semantic_utterances": sum(
            len(v.get("utterances", [])) for v in cfg.semantic_routes.values()
        ),
        "semantic_routes_rebuilt": semantic_rebuilt,
    }


@router.get("/list", response_model=KnowledgeListResponse)
async def list_documents(request: Request):
    """获取知识库文档列表 - 按用户角色过滤（行级权限）"""
    current_user = await get_current_user_full(request)
    role = current_user.get("role", "purchase") if current_user else "purchase"

    # admin 看全部，其他角色用 array_contains 过滤
    docs = milvus_manager.list_documents(role=role)

    return KnowledgeListResponse(
        total=len(docs),
        documents=[
            KnowledgeBaseResponse(
                doc_id=d["doc_id"],
                filename=d.get("source", "unknown"),
                chunk_count=d["chunk_count"],
                status="indexed",
                security_group=d.get("security_group", ["admin"]),
            )
            for d in docs
        ],
    )


@router.get("/stats")
async def get_knowledge_stats():
    """获取知识库统计信息 - 从配置读取embedding参数"""
    settings = get_settings()
    stats = milvus_manager.get_collection_stats()
    return {
        "collection_name": stats.get("collection_name", ""),
        "total_chunks": stats.get("num_entities", 0),
        "embedding_model": settings.EMBEDDING_MODEL,
        "embedding_dimension": settings.EMBEDDING_DIMENSION,
    }


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, request: Request = None):
    """从知识库删除文档（manager 及以上，且仅限本部门文档）"""
    # RBAC：manager+ 可删除；非 admin 需校验文档归属本部门（防跨部门越权删除）
    current_user = await get_current_user_full(request)
    user_role = current_user.get("role", "")
    check_level(current_user, UserLevel.MANAGER.value)

    if user_role != UserRole.ADMIN.value:
        docs = milvus_manager.list_documents(role=user_role)
        target = next((d for d in docs if d.get("doc_id") == doc_id), None)
        sg = (target or {}).get("security_group") or []
        if user_role not in sg:
            raise HTTPException(
                status_code=403,
                detail="无权删除该文档：仅限删除本部门文档，请联系管理员",
            )

    try:
        milvus_manager.delete_by_doc_id(doc_id)
        rag_engine.bm25.remove_doc(doc_id)
        # 知识库变更 → 失效检索缓存，避免已删文档仍被缓存命中
        await _invalidate_retrieval_caches()
        return {"message": f"文档{doc_id}已删除", "doc_id": doc_id}
    except Exception as e:
        logger.error(f"[Knowledge] 文档删除失败 doc_id={doc_id}: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="文档删除失败，请稍后重试")


# ---- 辅助函数 ----

def _parse_and_chunk(file_path: str, filename: str, doc_id: str) -> list[dict]:
    """
    解析文档并切片

    1. 文档解析是RAG的第一步，不同格式需要不同的解析器：
       - PDF: PyMuPDF / pdfplumber
       - Word: python-docx
       - TXT/MD: 直接读取
    2. 切片(Chunking)的质量直接影响检索效果：
       - 太小：语义不完整
       - 太大：噪声太多
       - 推荐值：256-1024字符，Overlap 10-20%
    """
    from app.config import get_settings
    settings = get_settings()

    # 读取文件内容
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        text = _read_pdf(file_path)
    elif ext in (".txt", ".md", ".markdown"):
        text = _read_text(file_path)
    elif ext in (".docx", ".doc"):
        text = _read_docx(file_path)
    else:
        raise ValueError(f"不支持的文件类型: {ext}")

    if not text.strip():
        raise ValueError("文档内容为空")

    # PII脱敏：在切片前过滤敏感信息（如身份证号、手机号、邮箱等），
    # 确保存入向量数据库的切片不包含用户PII，防止后续检索时泄露隐私
    text = _pii_filter.filter_text(text)

    # 语义切片
    semantic_chunks = _chunk_text(
        text=text,
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
    )

    # 格式化为索引记录（保留语义元数据）
    records = []
    for i, schunk in enumerate(semantic_chunks):
        records.append({
            "chunk_id": f"{doc_id}_chunk_{i}",
            "content": schunk["content"],
            "source": filename,
            "page_num": 0,  # 简化实现，PDF可提取页码
            "section_title": schunk.get("section_title", ""),
            "paragraph_index": schunk.get("paragraph_index", i),
        })

    logger.info(f"文档切片完成: {filename}, 切片数={len(records)}")
    return records


def _read_pdf(file_path: str) -> str:
    """读取PDF文件内容（三阶回退：opendataloader → pymupdf4llm → pdfplumber）

    1. opendataloader-pdf（主解析器）：benchmark #1 综合精度 0.907
       - 结构化 Markdown/JSON 输出，自动过滤页眉页脚/水印
       - 多栏布局确定性阅读顺序、表格精度 0.93
       - 支持扫描件（Hybrid 模式 + AI 引擎）、图片提取
       - 需 Java 11+（不可用时自动降级）

    2. pymupdf4llm（第一 fallback）：纯 Python，不需 Java
       - Markdown 输出，保留标题层级和表格
       - 成熟稳定，覆盖绝大多数普通 PDF

    3. pdfplumber（最终兜底）：表格 → Markdown 手动转换
    """
    # === Tier 1: opendataloader-pdf（精度最高）===
    _java_ok = _check_java()
    if _java_ok:
        try:
            import tempfile

            import opendataloader_pdf
            with tempfile.TemporaryDirectory() as tmp_dir:
                # 使用 convert() 替代已废弃的 run()
                opendataloader_pdf.convert(
                    input_path=file_path,
                    output_dir=tmp_dir,
                    format="markdown",
                    quiet=True,
                )
                md_files = [f for f in os.listdir(tmp_dir) if f.endswith('.md')]
                if md_files:
                    md_path = os.path.join(tmp_dir, md_files[0])
                    with open(md_path, 'r', encoding='utf-8') as f:
                        md_text = f.read()
                    if md_text.strip():
                        logger.info(f"[opendataloader] PDF解析成功: {len(md_text)} 字符")
                        return md_text
                raise ValueError("OpenDataLoader未生成可读文件")
        except ImportError:
            logger.warning("[opendataloader] 未安装，回退到pymupdf4llm")
        except Exception as e:
            logger.warning(f"[opendataloader] 解析失败: {e}，回退到pymupdf4llm")
    else:
        logger.info("[opendataloader] Java未检测到，跳过（需Java 11+），回退到pymupdf4llm")

    # === Tier 2: pymupdf4llm（不需要Java）===
    try:
        import pymupdf4llm
        md_text = pymupdf4llm.to_markdown(file_path)
        if md_text.strip():
            logger.info(f"[pymupdf4llm] PDF解析成功: {len(md_text)} 字符")
            return md_text
    except Exception as e:
        logger.warning(f"[pymupdf4llm] 解析失败: {e}，回退到pdfplumber")

    # === Tier 3: pdfplumber（最终兜底）===
    import pdfplumber
    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            # 提取表格 → Markdown（优先）
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                md_rows = []
                for row_idx, row in enumerate(table):
                    cells = [c.strip() if c else "" for c in row]
                    md_rows.append("| " + " | ".join(cells) + " |")
                    if row_idx == 0:
                        md_rows.append("| " + " | ".join(["---"] * len(cells)) + " |")
                text_parts.append("\n".join(md_rows))

            # 提取段落文本
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n\n".join(text_parts)


_JAVA_AVAILABLE: bool | None = None  # 模块级缓存，避免每次 PDF 上传都探测


def _check_java() -> bool:
    """检测 Java 11+ 是否可用（opendataloader-pdf 需要）

    性能优化：先查 PATH（毫秒级），不存在直接返回 False。
    缓存结果到模块级 _JAVA_AVAILABLE，避免每次 PDF 上传都触发 subprocess。

    注：原实现每次调 subprocess.run，JAVA_HOME 存在但 PATH 缺失时会阻塞 10s。
    """
    global _JAVA_AVAILABLE
    if _JAVA_AVAILABLE is not None:
        return _JAVA_AVAILABLE

    import shutil
    import subprocess
    # 1. 先用 shutil.which 检查 PATH（毫秒级）
    if not shutil.which("java"):
        logger.info("[Java] PATH 中未找到 java，跳过版本检测")
        _JAVA_AVAILABLE = False
        return False

    # 2. java 在 PATH 中，再检查版本（带超时）
    try:
        result = subprocess.run(
            ["java", "-version"],
            capture_output=True, text=True, timeout=2  # 从 10s 减到 2s
        )
        # java -version 输出到 stderr
        output = result.stderr or result.stdout
        if "version" in output.lower():
            # 提取主版本号（如 "1.8.0" → 8, "11.0.2" → 11）
            import re
            m = re.search(r'version "(\d+)', output)
            if not m:
                m = re.search(r'version "1\.(\d+)', output)
            if m:
                ver = int(m.group(1))
                ok = ver >= 11
                logger.debug(f"[Java] 检测到版本 {ver}, 可用={ok}")
                _JAVA_AVAILABLE = ok  # 缓存结果
                return ok
        _JAVA_AVAILABLE = False  # 缓存结果
        return False
    except FileNotFoundError:
        _JAVA_AVAILABLE = False
        return False
    except Exception:
        _JAVA_AVAILABLE = False
        return False


def _read_text(file_path: str) -> str:
    """读取文本文件内容"""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def _read_docx(file_path: str) -> str:
    """读取DOCX文件内容（使用python-docx）

    提取段落文本和表格内容，输出结构化文本。
    """
    try:
        from docx import Document
        doc = Document(file_path)
        parts = []

        for element in doc.element.body:
            tag = element.tag.split('}')[-1] if '}' in element.tag else element.tag
            if tag == 'p':
                # 段落 — 遍历所有 w:t 元素提取文本（lxml element.text 不穿透子元素）
                ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
                text = ''.join(t.text or '' for t in element.iter(f'{ns}t'))
                if text.strip():
                    parts.append(text.strip())
            elif tag == 'tbl':
                # 表格 → 转为文本格式
                table = element
                rows = table.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tr')
                for row in rows:
                    cells = row.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tc')
                    cell_texts = []
                    for cell in cells:
                        cell_text = ''.join(t.text or '' for t in cell.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
                        cell_texts.append(cell_text.strip())
                    if any(cell_texts):
                        parts.append(' | '.join(cell_texts))

        return '\n\n'.join(parts)
    except ImportError:
        logger.warning("[python-docx] 未安装，尝试pymupdf")
        try:
            import pymupdf
            doc = pymupdf.open(file_path)
            text_parts = []
            for page in doc:
                text_parts.append(page.get_text())
            return '\n\n'.join(text_parts)
        except Exception as e:
            raise ValueError(f"DOCX解析失败: {e}")


def _chunk_text(text: str, chunk_size: int = 512, chunk_overlap: int = 64) -> list[dict]:
    """
    语义切片 — 按段落/标题/句子边界切分，保留完整语义单元

    切分优先级：段落（\\n\\n） > 标题行（# 开头） > 句子（。！？.!?）
    - 每个 chunk 尽量保留完整的段落/章节，不切断句子
    - 单个段落超过 chunk_size 时，按句子边界二次切分
    - 相邻 chunk 有 overlap：取前一个 chunk 的最后 1-2 个句子作为上下文
    - 每个 chunk 附加元数据：section_title, paragraph_index

    Returns:
        list[dict] — [{"content": str, "section_title": str, "paragraph_index": int}, ...]
    """

    # --- 辅助函数 ---

    # 句子分割正则：匹配中英文句末标点（保留标点在句子上）
    _SENT_RE = re.compile(r'(?<=[。！？.!?])\s*')

    def _split_sentences(text_block: str) -> list[str]:
        """将文本块按句子边界切分（保留标点）。"""
        sents = _SENT_RE.split(text_block.strip())
        return [s for s in sents if s.strip()]

    def _split_paragraphs(full_text: str) -> list[dict]:
        """
        将全文切分为段落列表，每个段落携带 section_title 元数据。
        切分逻辑：
          1. 先按 \\n\\n 切分大段
          2. 每段内部再按标题行（# 开头）细分
        """
        # 按双换行切分
        raw_blocks = re.split(r'\n{2,}', full_text)
        paragraphs = []
        current_title = ""
        para_index = 0

        for block in raw_blocks:
            block = block.strip()
            if not block:
                continue

            lines = block.split('\n')
            sub_blocks = []
            current_lines = []

            for line in lines:
                # 检测 Markdown 标题行
                if re.match(r'^#{1,6}\s+', line):
                    # 先保存之前积累的行
                    if current_lines:
                        sub_blocks.append(('\n'.join(current_lines), current_title))
                        current_lines = []
                    current_title = line.strip()
                    # 标题行本身不单独成段，作为下一段的 title
                else:
                    current_lines.append(line)

            if current_lines:
                sub_blocks.append(('\n'.join(current_lines), current_title))

            for sub_text, title in sub_blocks:
                sub_text = sub_text.strip()
                if sub_text:
                    paragraphs.append({
                        "text": sub_text,
                        "section_title": title,
                        "paragraph_index": para_index,
                    })
                    para_index += 1

        return paragraphs

    def _overlap_sentences(prev_chunk_text: str, max_chars: int) -> str:
        """取前一个 chunk 的最后 1-2 个句子作为 overlap 上下文。"""
        sents = _split_sentences(prev_chunk_text)
        if not sents:
            return ""
        # 从末尾取句子，总长度不超过 max_chars
        overlap_parts = []
        total = 0
        for sent in reversed(sents):
            if total + len(sent) > max_chars and overlap_parts:
                break
            overlap_parts.append(sent)
            total += len(sent)
        overlap_parts.reverse()
        return "".join(overlap_parts)

    # --- 主逻辑 ---

    # Step 1: 全文按段落切分
    paragraphs = _split_paragraphs(text)

    if not paragraphs:
        # 兜底：纯文本无段落结构
        return [{"content": text.strip(), "section_title": "", "paragraph_index": 0}]

    # Step 2: 对每个段落，决定是否需要二次切分
    #         将相邻小段落合并到 chunk_size 以内
    chunks = []
    current_texts = []        # 当前 chunk 收集的段落文本
    current_title = ""        # 当前 chunk 的 section_title
    current_para_idx = 0      # 当前 chunk 起始 paragraph_index
    current_len = 0

    def _flush_current():
        """将当前积累的段落打包为一个 chunk。"""
        nonlocal current_texts, current_title, current_para_idx, current_len
        if current_texts:
            combined = "\n\n".join(current_texts)
            chunks.append({
                "content": combined.strip(),
                "section_title": current_title,
                "paragraph_index": current_para_idx,
            })
            current_texts = []
            current_len = 0

    for para in paragraphs:
        para_text = para["text"]
        para_title = para["section_title"]
        para_idx = para["paragraph_index"]

        # 如果单个段落就超过 chunk_size，需要先 flush，然后对该段落按句子二次切分
        if len(para_text) > chunk_size:
            _flush_current()
            current_title = para_title
            current_para_idx = para_idx

            # 按句子切分这个大段落
            sentences = _split_sentences(para_text)
            sub_chunk = ""
            sub_chunk_start_idx = para_idx

            for sent in sentences:
                if len(sub_chunk) + len(sent) > chunk_size and sub_chunk:
                    # 当前 sub_chunk 已满，保存
                    chunks.append({
                        "content": sub_chunk.strip(),
                        "section_title": para_title,
                        "paragraph_index": sub_chunk_start_idx,
                    })
                    # overlap: 取当前 sub_chunk 的最后 1-2 个句子
                    overlap_prefix = _overlap_sentences(sub_chunk, chunk_overlap)
                    sub_chunk = overlap_prefix + sent
                    sub_chunk_start_idx = para_idx
                else:
                    sub_chunk += sent

            if sub_chunk.strip():
                chunks.append({
                    "content": sub_chunk.strip(),
                    "section_title": para_title,
                    "paragraph_index": sub_chunk_start_idx,
                })
            # 重置
            current_texts = []
            current_len = 0
            continue

        # 正常段落：尝试合并
        # 段落之间用 \n\n 连接，需要 +2 字符
        added_len = len(para_text) + (2 if current_texts else 0)

        if current_len + added_len > chunk_size and current_texts:
            # 当前 chunk 已满，flush 后开始新 chunk
            _flush_current()

            # overlap: 取前一个 chunk 最后 1-2 个句子作为新 chunk 开头
            if chunks:
                prev_content = chunks[-1]["content"]
                overlap_text = _overlap_sentences(prev_content, chunk_overlap)
                if overlap_text:
                    current_texts.append(overlap_text)
                    current_len = len(overlap_text) + 2

            current_title = para_title
            current_para_idx = para_idx

        elif not current_texts:
            current_title = para_title
            current_para_idx = para_idx

        current_texts.append(para_text)
        current_len += added_len

    # flush 最后一组
    _flush_current()

    return [c for c in chunks if c.get("content")]


# ---- 批量入库接口 ----

class IngestResponse(BaseModel):
    """批量入库响应"""
    success: bool = True
    message: str = ""
    total_chunks: int = 0
    downloaded: int = 0


# ingest 任务状态 Redis key（后台任务与状态查询端点共用）
_INGEST_STATUS_KEY = "scqa:ingest:status"


async def _set_ingest_status(status: dict) -> None:
    """写入 ingest 任务状态（Redis 不可用时降级为仅日志）"""
    import json as _json
    try:
        from app.core.redis_client import redis_manager
        if redis_manager.is_connected:
            await redis_manager.client.set(_INGEST_STATUS_KEY, _json.dumps(status, ensure_ascii=False), ex=3600)
    except Exception as e:
        logger.warning(f"[Ingest] 状态写入失败: {e}")


async def _run_ingest_job() -> None:
    """后台执行下载 + 入库全流程，状态写入 Redis 供前端轮询"""
    import sys

    scripts_dir = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
    sys.path.insert(0, scripts_dir)

    try:
        from scripts.download_real_pdfs import download_all
        from scripts.ingest_pdfs import PDF_DIR, ingest_all

        await _set_ingest_status({"status": "downloading", "message": "正在下载报告..."})
        await asyncio.to_thread(download_all)

        if os.path.exists(PDF_DIR):
            pdfs = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
            mds = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".md")]
            downloaded = len(pdfs) + len(mds)
        else:
            downloaded = 0

        await _set_ingest_status({"status": "indexing", "message": f"已下载 {downloaded} 份，正在入库...", "downloaded": downloaded})
        total_chunks = await asyncio.to_thread(ingest_all)

        # 知识库变更 → 失效检索缓存
        await _invalidate_retrieval_caches()

        await _set_ingest_status({
            "status": "done",
            "message": f"下载 {downloaded} 份报告，入库 {total_chunks or 0} 个 chunk",
            "total_chunks": total_chunks or 0,
            "downloaded": downloaded,
        })
        logger.info(f"[Ingest] 后台入库完成: {downloaded} 份 / {total_chunks or 0} chunks")
    except Exception as e:
        logger.error(f"[Ingest] 后台入库失败: {e}", exc_info=True)
        await _set_ingest_status({"status": "failed", "message": f"入库失败: {e}"})


@router.post("/ingest", response_model=IngestResponse)
async def ingest_real_pdfs(background_tasks: BackgroundTasks, request: Request = None):
    """
    一键导入内置知识库（admin，后台任务，立即返回）

    流程（后台执行，通过 GET /knowledge/ingest/status 轮询进度）：
    1. 下载 5 份供应链公开报告（或使用本地知识库 fallback）
    2. 解析 PDF/Markdown，提取文本和表格
    3. 切片 + 嵌入 + 存入 Milvus（同 doc_id 先删后插，重复导入不会累积重复 chunk）

    之前同步执行会让 HTTP 连接挂起数分钟且无进度反馈，现改为 BackgroundTasks。
    """
    # RBAC：全量入库会改写知识库并触发缓存失效，仅限 admin
    current_user = await get_current_user_full(request)
    check_role(current_user, [UserRole.ADMIN.value])

    await _set_ingest_status({"status": "pending", "message": "任务已提交，等待执行"})
    background_tasks.add_task(_run_ingest_job)
    return IngestResponse(
        success=True,
        message="入库任务已提交后台执行，请通过 GET /api/v1/knowledge/ingest/status 查询进度",
        total_chunks=0,
        downloaded=0,
    )


@router.get("/ingest/status")
async def ingest_status(request: Request = None):
    """查询后台入库任务状态（pending/downloading/indexing/done/failed，admin）"""
    # RBAC：与 POST /ingest 同一 UI 流程，同样仅限 admin
    current_user = await get_current_user_full(request)
    check_role(current_user, [UserRole.ADMIN.value])

    import json as _json
    try:
        from app.core.redis_client import redis_manager
        if redis_manager.is_connected:
            raw = await redis_manager.client.get(_INGEST_STATUS_KEY)
            if raw:
                return _json.loads(raw)
    except Exception as e:
        logger.warning(f"[Ingest] 状态读取失败: {e}")
    return {"status": "unknown", "message": "无进行中的入库任务或状态已过期"}
