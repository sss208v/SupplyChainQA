"""
SupplyChainRAG - PDF 批量入库脚本
============================================================
读取下载的 PDF 报告，解析并切片后存入 Milvus 和 BM25 索引。

使用 pdfplumber 提取文本和表格，支持：
- 表格 → Markdown 格式保留
- 语义切片（按段落/标题边界）
- 权限组标记（默认 public）

两种使用方式：
1. 通过 API 触发（推荐）：POST /api/v1/knowledge/ingest
   前端知识库页面点击「📥 一键导入大厂供应链样本库」

2. 作为独立脚本运行：
   powershell> cd backend
   powershell> .\venv\Scripts\Activate.ps1
   powershell> $env:PYTHONPATH="."; python scripts/ingest_pdfs.py
   ⚠️ 注意：独立运行需要 Milvus/Redis/PostgreSQL 服务已启动，
      且 rag_engine 单例已完成初始化（即 uvicorn 已启动过至少一次）。
      推荐方式是通过 API 触发，脚本内部会正确处理依赖。
============================================================
"""
import os
import sys
import json
import logging
import hashlib
from typing import Optional

# 确保可以从项目根目录导入
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 项目路径
PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PDF_DIR = os.path.join(PROJECT_DIR, "data", "pdf_reports")


def extract_text_from_pdf(pdf_path: str) -> str:
    """使用 pdfplumber 提取 PDF 文本（含表格转 Markdown）"""
    import pdfplumber
    content_parts = []

    with pdfplumber.open(pdf_path) as pdf:
        logger.info(f"  PDF 页数: {len(pdf.pages)}")
        for page_num, page in enumerate(pdf.pages):
            # 提取表格
            tables = page.extract_tables()
            for table in tables:
                if not table:
                    continue
                # 表格 → Markdown
                md_lines = []
                for row_idx, row in enumerate(table):
                    row = [cell.strip() if cell else "" for cell in row]
                    md_lines.append("| " + " | ".join(row) + " |")
                    if row_idx == 0:
                        # Markdown 表头分隔
                        md_lines.append("| " + " | ".join(["---"] * len(row)) + " |")
                content_parts.append("\n".join(md_lines))

            # 提取段落文本
            text = page.extract_text()
            if text:
                content_parts.append(text.strip())

    return "\n\n".join(content_parts)


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list[dict]:
    """语义切片：按段落/标题边界切分"""
    import re
    paragraphs = re.split(r"\n\s*\n", text)
    chunks = []
    buffer = ""
    idx = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(buffer) + len(para) < chunk_size:
            buffer += "\n\n" + para if buffer else para
        else:
            if buffer:
                chunks.append({
                    "chunk_id": f"chunk_{idx:04d}",
                    "content": buffer,
                    "section_title": _extract_title(buffer),
                })
                idx += 1
                # overlap
                overlap_text = buffer[-overlap:] if len(buffer) > overlap else ""
                buffer = overlap_text + "\n\n" + para if overlap_text else para
            else:
                chunks.append({
                    "chunk_id": f"chunk_{idx:04d}",
                    "content": para,
                    "section_title": _extract_title(para),
                })
                idx += 1

    if buffer:
        chunks.append({
            "chunk_id": f"chunk_{idx:04d}",
            "content": buffer,
            "section_title": _extract_title(buffer),
        })

    return chunks


def _extract_title(text: str) -> str:
    """从文本中提取标题"""
    import re
    lines = text.strip().split("\n")
    for line in lines[:5]:
        line = line.strip()
        if re.match(r"^#{1,4}\s", line):
            return line.lstrip("#").strip()
        if re.match(r"^[一二三四五六七八九十]+[、.．]\s", line):
            return line[:30]
    return lines[0][:40] if lines else ""


def ingest_pdf_to_milvus(pdf_path: str, security_group: Optional[list] = None) -> dict:
    """
    将 PDF 文件导入 Milvus 知识库

    Args:
        pdf_path: PDF 文件路径
        security_group: 权限组列表，默认 ["admin", "purchase", "warehouse"]

    Returns:
        dict: {filename, chunk_count, doc_id}
    """
    if security_group is None:
        security_group = ["admin", "purchase", "warehouse"]

    # 1. 提取文本
    logger.info(f"解析 PDF: {os.path.basename(pdf_path)}")
    text = extract_text_from_pdf(pdf_path)
    logger.info(f"  提取文本长度: {len(text)} 字符")

    if not text.strip():
        logger.warning("  ⚠️  未提取到文本，跳过")
        return {"filename": os.path.basename(pdf_path), "chunk_count": 0}

    # 2. 切片
    chunks = chunk_text(text)
    logger.info(f"  切片数量: {len(chunks)}")

    if not chunks:
        return {"filename": os.path.basename(pdf_path), "chunk_count": 0}

    # 3. 嵌入 + 入库
    from app.core.rag_engine import rag_engine

    doc_id = hashlib.md5(os.path.basename(pdf_path).encode()).hexdigest()[:12]
    result = rag_engine.index_document(doc_id, chunks, security_group=security_group)
    result["filename"] = os.path.basename(pdf_path)

    logger.info(f"  ✅ 入库成功: doc_id={doc_id}, chunks={result.get('chunk_count', 0)}")
    return result


def ingest_all():
    """批量入库所有 PDF"""
    if not os.path.exists(PDF_DIR):
        logger.error(f"PDF 目录不存在: {PDF_DIR}")
        logger.info("请先运行 download_real_pdfs.py")
        return

    pdf_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")]
    md_files = [f for f in os.listdir(PDF_DIR) if f.lower().endswith(".md")]

    all_files = pdf_files + md_files
    if not all_files:
        logger.warning("没有找到需要入库的文件")
        return

    logger.info(f"找到 {len(all_files)} 个文件（{len(pdf_files)} PDF + {len(md_files)} markdown）")

    total_chunks = 0
    for fname in all_files:
        fpath = os.path.join(PDF_DIR, fname)

        if fname.lower().endswith(".md"):
            # Markdown 文件直接读取
            with open(fpath, "r", encoding="utf-8") as f:
                text = f.read()
            chunks = chunk_text(text)
            doc_id = hashlib.md5(fname.encode()).hexdigest()[:12]
            from app.core.rag_engine import rag_engine
            result = rag_engine.index_document(doc_id, chunks, security_group=["admin", "purchase", "warehouse"])
            total_chunks += result.get("chunk_count", 0)
            logger.info(f"  ✅ {fname}: {result.get('chunk_count', 0)} chunks")
        else:
            result = ingest_pdf_to_milvus(fpath)
            total_chunks += result.get("chunk_count", 0)

    logger.info(f"\n批量入库完成: 共 {total_chunks} 个 chunk")
    return total_chunks


if __name__ == "__main__":
    # 当作为独立脚本运行时，需要先确保 Django 环境已初始化
    # 建议通过 API 端点触发入库
    logger.info("请通过前端「一键导入大厂供应链样本库」功能触发入库")
    logger.info("或直接调用 /api/v1/knowledge/ingest 端点")
