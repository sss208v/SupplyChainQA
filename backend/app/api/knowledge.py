"""
SmartQA Pro - 知识库API路由
============================================================
【学习要点】
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
import os
import uuid
import re
import logging
from typing import Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Depends, Request
from pydantic import BaseModel, Field
from app.core.milvus_client import milvus_manager
from app.core.rag_engine import rag_engine
from app.core.data_filter import PIIFilter
from app.config import get_settings
from app.models.user import UserRole
from app.core.auth import (
    get_current_user_required,
    get_current_user_full,
    check_role,
)

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
    visibility: str = "public"


class KnowledgeListResponse(BaseModel):
    """知识库列表响应"""
    total: int
    documents: list[KnowledgeBaseResponse]


# ---- API接口 ----

@router.post("/upload", response_model=KnowledgeBaseResponse)
async def upload_document(
    file: UploadFile = File(..., description="上传的文档文件"),
    visibility: str = Form("public", description="可见性: public/admin_only"),
    request: Request = None,
):
    """
    上传文档到知识库
    支持格式：PDF、TXT、Markdown
    处理流程：上传 → 解析 → 切片 → 嵌入 → 存入Milvus
    """
    # RBAC：需要admin或manager角色
    current_user = await get_current_user_full(request)
    check_role(current_user, [UserRole.ADMIN.value, UserRole.MANAGER.value])

    # 验证文件类型
    allowed_types = [".pdf", ".txt", ".md", ".markdown", ".docx", ".doc"]
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {file_ext}，支持: {allowed_types}",
        )

    # 生成文档ID
    doc_id = str(uuid.uuid4())[:12]

    # 保存文件
    file_path = os.path.join(UPLOAD_DIR, f"{doc_id}_{file.filename}")
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    logger.info(f"文件上传成功: {file.filename}, doc_id={doc_id}")

    try:
        # 解析文档 → 切片
        chunks = _parse_and_chunk(file_path, file.filename, doc_id)

        # 嵌入 + 存入Milvus（带可见性）
        result = rag_engine.index_document(doc_id, chunks, visibility=visibility)

        return KnowledgeBaseResponse(
            doc_id=doc_id,
            filename=file.filename,
            chunk_count=result.get("chunk_count", 0),
            status="indexed",
            visibility=visibility,
        )

    except Exception as e:
        logger.error(f"文档处理失败: {e}")
        raise HTTPException(status_code=500, detail="文档处理失败，请检查文件格式或稍后重试")


@router.get("/list", response_model=KnowledgeListResponse)
async def list_documents(request: Request = None):
    """获取知识库文档列表 - 按用户角色过滤可见性"""
    # 尝试获取当前用户，未登录则只看 public
    try:
        current_user = await get_current_user_full(request)
        role = current_user.get("role", "employee") if current_user else "employee"
    except Exception:
        role = "employee"

    # admin 看全部，其他角色只看 public
    visibility_filter = None if role == "admin" else "public"
    docs = milvus_manager.list_documents(visibility_filter=visibility_filter)

    return KnowledgeListResponse(
        total=len(docs),
        documents=[
            KnowledgeBaseResponse(
                doc_id=d["doc_id"],
                filename=d.get("source", "unknown"),
                chunk_count=d["chunk_count"],
                status="indexed",
                visibility=d.get("visibility", "public"),
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
    """从知识库删除文档"""
    # RBAC：需要admin或manager角色
    current_user = await get_current_user_full(request)
    check_role(current_user, [UserRole.ADMIN.value, UserRole.MANAGER.value])

    try:
        milvus_manager.delete_by_doc_id(doc_id)
        rag_engine.bm25.remove_doc(doc_id)
        return {"message": f"文档{doc_id}已删除", "doc_id": doc_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail="文档删除失败，请稍后重试")


# ---- 辅助函数 ----

def _parse_and_chunk(file_path: str, filename: str, doc_id: str) -> list[dict]:
    """
    解析文档并切片

    【学习要点】
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
    """读取PDF文件内容（使用pymupdf4llm，结构化Markdown输出）

    【选型说明】参考RAG-Anything的多模态解析思路：
    1. pymupdf4llm 输出结构化Markdown，保留标题层级和表格
    2. 表格自动转为Markdown表格格式，检索时语义更完整
    3. 支持图片提取和描述（需VLM配合）
    4. 比纯文本提取（pdfplumber）保留更多结构信息

    面试话术："PDF解析我用了pymupdf4llm，它输出结构化Markdown，
    表格自动转为Markdown格式，标题层级保留，比纯文本提取效果好很多。
    参考了RAG-Anything的多模态解析思路。"
    """
    try:
        import pymupdf4llm
        md_text = pymupdf4llm.to_markdown(file_path)
        if md_text.strip():
            logger.info(f"[pymupdf4llm] PDF解析成功: {len(md_text)} 字符")
            return md_text
    except Exception as e:
        logger.warning(f"[pymupdf4llm] 解析失败: {e}，回退到opendataloader")

    try:
        import opendataloader_pdf
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            opendataloader_pdf.run(
                input_path=file_path,
                output_folder=tmp_dir,
                generate_markdown=True,
            )
            md_files = [f for f in os.listdir(tmp_dir) if f.endswith('.md')]
            if md_files:
                md_path = os.path.join(tmp_dir, md_files[0])
                with open(md_path, 'r', encoding='utf-8') as f:
                    return f.read()
            raise ValueError("OpenDataLoader未生成可读文件")
    except ImportError:
        logger.warning("[opendataloader] 未安装，回退到pdfplumber")
        import pdfplumber
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n\n".join(text_parts)


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
                # 段落
                text = element.text
                if text and text.strip():
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
