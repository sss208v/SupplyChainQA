"""
SupplyChainRAG - 数据预处理工具
============================================================
【功能说明】业务数据（文本/图片）的清洗、标注与批量化处理

支持的操作：
1. 文本清洗：去HTML标签、去特殊字符、统一编码、去重
2. PII过滤：调用data_filter模块脱敏敏感信息
3. 文档切片：按大小/语义边界切片，用于RAG索引
4. 批量处理：遍历目录下所有文件，统一清洗后输出
5. 数据标注：为QA对生成标注格式（用于评估）

使用方式：
    # 命令行
    python -m app.core.data_preprocess --input ./raw_data --output ./clean_data
    python -m app.core.data_preprocess --input ./raw_data --filter-pii --chunk
    python -m app.core.data_preprocess --input file.txt --stats

    # 代码调用
    from app.core.data_preprocess import DataPreprocessor
    pp = DataPreprocessor()
    clean_text = pp.clean_text(raw_text)
    chunks = pp.chunk_for_rag(clean_text)
============================================================
"""
import re
import json
import hashlib
import logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CleanResult:
    """清洗结果"""
    original_length: int
    cleaned_length: int
    removed_chars: int
    pii_count: int
    duplicate_lines_removed: int
    chunks: list[str] = field(default_factory=list)

    @property
    def compression_ratio(self) -> float:
        if self.original_length == 0:
            return 0.0
        return round(1 - self.cleaned_length / self.original_length, 4)


class DataPreprocessor:
    """
    数据预处理器

    核心方法：
    - clean_text(text): 单文本清洗
    - clean_file(path): 文件清洗
    - batch_clean(input_dir, output_dir): 批量清洗
    - chunk_for_rag(text, chunk_size, overlap): RAG切片
    - generate_eval_data(input_dir): 生成评估标注数据
    """

    def __init__(self, filter_pii: bool = True, remove_duplicates: bool = True):
        self.filter_pii = filter_pii
        self.remove_duplicates = remove_duplicates
        self._pii_filter = None

        if filter_pii:
            try:
                from app.core.data_filter import PIIFilter
                self._pii_filter = PIIFilter()
            except ImportError:
                logger.warning("PII过滤模块不可用，跳过PII脱敏")

    def clean_text(self, text: str) -> tuple[str, CleanResult]:
        """
        清洗单个文本

        处理步骤：
        1. 去HTML标签
        2. 去多余空白/换行
        3. 统一编码（全角→半角）
        4. 去特殊控制字符
        5. PII脱敏（可选）
        6. 去重复行（可选）

        Args:
            text: 原始文本

        Returns:
            (清洗后文本, 清洗结果统计)
        """
        original_length = len(text)
        pii_count = 0
        dup_removed = 0

        # 1. 去HTML标签
        cleaned = re.sub(r"<[^>]+>", "", text)
        cleaned = re.sub(r"&[a-zA-Z]+;", " ", cleaned)

        # 2. 去多余空白
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        # 3. 全角→半角（数字和字母）
        cleaned = self._full_to_half(cleaned)

        # 4. 去控制字符（保留换行和制表符）
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned)

        # 5. PII脱敏
        if self._pii_filter:
            before = cleaned
            cleaned = self._pii_filter.filter_text(cleaned)
            # 统计PII数量
            pii_matches = self._pii_filter.detect_pii(before)
            pii_count = len(pii_matches)

        # 6. 去重复行
        if self.remove_duplicates:
            lines = cleaned.split("\n")
            seen = set()
            unique_lines = []
            for line in lines:
                line_stripped = line.strip()
                if line_stripped and line_stripped not in seen:
                    seen.add(line_stripped)
                    unique_lines.append(line)
                elif not line_stripped:
                    unique_lines.append(line)
            dup_removed = len(lines) - len(unique_lines)
            cleaned = "\n".join(unique_lines)

        # 7. 首尾去空白
        cleaned = cleaned.strip()

        result = CleanResult(
            original_length=original_length,
            cleaned_length=len(cleaned),
            removed_chars=original_length - len(cleaned),
            pii_count=pii_count,
            duplicate_lines_removed=dup_removed,
        )

        return cleaned, result

    def clean_file(self, file_path: str, encoding: str = "utf-8") -> tuple[str, CleanResult]:
        """
        清洗文件

        Args:
            file_path: 文件路径
            encoding: 文件编码

        Returns:
            (清洗后文本, 清洗结果统计)
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        # 尝试多种编码
        text = None
        for enc in [encoding, "utf-8", "gbk", "gb2312", "latin-1"]:
            try:
                text = path.read_text(encoding=enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if text is None:
            raise ValueError(f"无法解码文件: {file_path}")

        return self.clean_text(text)

    def batch_clean(
        self,
        input_dir: str,
        output_dir: str,
        file_extensions: Optional[list[str]] = None,
    ) -> dict:
        """
        批量清洗目录下的文件

        Args:
            input_dir: 输入目录
            output_dir: 输出目录
            file_extensions: 文件扩展名过滤，默认 [".txt", ".md", ".csv"]

        Returns:
            处理统计 {total, success, failed, total_pii, total_chars_removed}
        """
        if file_extensions is None:
            file_extensions = [".txt", ".md", ".csv"]

        input_path = Path(input_dir)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        stats = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "total_pii": 0,
            "total_chars_removed": 0,
            "files": [],
        }

        for file_path in input_path.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in file_extensions:
                stats["total"] += 1
                rel_path = file_path.relative_to(input_path)

                try:
                    cleaned, result = self.clean_file(str(file_path))

                    # 保持目录结构
                    out_file = output_path / rel_path
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    out_file.write_text(cleaned, encoding="utf-8")

                    stats["success"] += 1
                    stats["total_pii"] += result.pii_count
                    stats["total_chars_removed"] += result.removed_chars
                    stats["files"].append({
                        "file": str(rel_path),
                        "original_size": result.original_length,
                        "cleaned_size": result.cleaned_length,
                        "pii_found": result.pii_count,
                        "dups_removed": result.duplicate_lines_removed,
                    })

                    logger.info(f"✓ {rel_path}: {result.original_length}→{result.cleaned_length}字, PII={result.pii_count}")

                except Exception as e:
                    stats["failed"] += 1
                    stats["files"].append({
                        "file": str(rel_path),
                        "error": str(e),
                    })
                    logger.error(f"✗ {rel_path}: {e}")

        return stats

    def chunk_for_rag(
        self,
        text: str,
        chunk_size: int = 512,
        chunk_overlap: int = 64,
    ) -> list[str]:
        """
        将文本切片用于RAG索引

        Args:
            text: 清洗后的文本
            chunk_size: 切片大小（字符数）
            chunk_overlap: 重叠大小

        Returns:
            切片列表
        """
        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]

            # 在句子边界断开
            if end < len(text):
                for sep in ["\n\n", "。", ".", "！", "!", "？", "?", "\n"]:
                    last_sep = chunk.rfind(sep)
                    if last_sep > chunk_size * 0.5:
                        chunk = text[start:start + last_sep + len(sep)]
                        break

            chunks.append(chunk.strip())
            # 保证 start 至少前进 1 个字符，防止句子边界截断导致死循环
            advance = max(len(chunk) - chunk_overlap, 1)
            start += advance
            if start >= len(text):
                break

        return [c for c in chunks if c]

    def generate_eval_data(
        self,
        knowledge_dir: str,
        output_path: str,
    ) -> dict:
        """
        从知识库文档生成评估标注数据

        为每篇文档生成若干 QA 对，用于离线评估 RAG 检索质量

        Args:
            knowledge_dir: 知识库文档目录
            output_path: 输出JSON路径

        Returns:
            生成统计
        """
        knowledge_path = Path(knowledge_dir)
        eval_data = []

        for file_path in knowledge_path.glob("*.md"):
            text = file_path.read_text(encoding="utf-8")
            doc_id = hashlib.md5(file_path.name.encode()).hexdigest()[:12]

            # 按标题分段
            sections = re.split(r"^#{1,3}\s+", text, flags=re.MULTILINE)
            sections = [s.strip() for s in sections if s.strip()]

            for i, section in enumerate(sections):
                # 取第一行作为标题/主题
                lines = section.split("\n")
                topic = lines[0].strip() if lines else ""
                if not topic or len(topic) < 5:
                    continue

                chunk_id = f"{doc_id}_chunk_{i}"
                eval_data.append({
                    "query": f"什么是{topic}？" if not topic.endswith("？") else topic,
                    "relevant_chunk_ids": [chunk_id],
                    "source_file": file_path.name,
                    "section_index": i,
                })

        # 写入JSON
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(eval_data, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "total_queries": len(eval_data),
            "source_files": len(list(knowledge_path.glob("*.md"))),
            "output_path": str(out),
        }

    def text_stats(self, text: str) -> dict:
        """文本统计"""
        lines = text.split("\n")

        return {
            "total_chars": len(text),
            "total_lines": len(lines),
            "non_empty_lines": len([line for line in lines if line.strip()]),
            "chinese_chars": len(re.findall(r"[\u4e00-\u9fff]", text)),
            "english_words": len(re.findall(r"[a-zA-Z]+", text)),
            "numbers": len(re.findall(r"\d+", text)),
            "avg_line_length": round(len(text) / max(len(lines), 1), 1),
        }

    @staticmethod
    def _full_to_half(text: str) -> str:
        """全角转半角（数字和字母）"""
        result = []
        for char in text:
            code = ord(char)
            # 全角空格
            if code == 0x3000:
                result.append(" ")
            # 全角数字和字母 (FF01-FF5E) → 半角 (0021-007E)
            elif 0xFF01 <= code <= 0xFF5E:
                result.append(chr(code - 0xFEE0))
            else:
                result.append(char)
        return "".join(result)


# ---- 命令行入口 ----

def main():
    import argparse

    parser = argparse.ArgumentParser(description="SupplyChainRAG 数据预处理工具")
    parser.add_argument("--input", "-i", required=True, help="输入文件/目录")
    parser.add_argument("--output", "-o", help="输出目录")
    parser.add_argument("--filter-pii", action="store_true", help="启用PII脱敏")
    parser.add_argument("--chunk", action="store_true", help="输出RAG切片")
    parser.add_argument("--chunk-size", type=int, default=512, help="切片大小")
    parser.add_argument("--stats", action="store_true", help="仅输出统计信息")
    parser.add_argument("--eval-data", help="生成评估数据到指定路径")
    parser.add_argument("--ext", nargs="+", default=[".txt", ".md", ".csv"], help="文件扩展名")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    pp = DataPreprocessor(filter_pii=args.filter_pii)

    input_path = Path(args.input)

    if args.stats:
        # 统计模式
        if input_path.is_file():
            _, result = pp.clean_file(str(input_path))
            text = input_path.read_text(encoding="utf-8")
            stats = pp.text_stats(text)
            print(json.dumps({**stats, "pii_count": result.pii_count}, ensure_ascii=False, indent=2))
        else:
            print("统计模式仅支持单文件，请指定 --input file.txt")
        return

    if args.eval_data:
        # 生成评估数据
        result = pp.generate_eval_data(str(input_path), args.eval_data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if input_path.is_file():
        # 单文件处理
        cleaned, result = pp.clean_file(str(input_path))
        print(f"原文长度: {result.original_length}")
        print(f"清洗后: {result.cleaned_length}")
        print(f"移除字符: {result.removed_chars}")
        print(f"PII数量: {result.pii_count}")
        print(f"重复行: {result.duplicate_lines_removed}")

        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            if args.chunk:
                chunks = pp.chunk_for_rag(cleaned, args.chunk_size)
                for i, chunk in enumerate(chunks):
                    chunk_file = out / f"chunk_{i:04d}.txt"
                    chunk_file.write_text(chunk, encoding="utf-8")
                print(f"切片数: {len(chunks)}, 输出到: {out}")
            else:
                out.write_text(cleaned, encoding="utf-8")
                print(f"输出到: {out}")
        else:
            print("\n--- 清洗结果 ---")
            print(cleaned[:500] + "..." if len(cleaned) > 500 else cleaned)

    elif input_path.is_dir():
        # 目录批量处理
        output_dir = args.output or str(input_path) + "_cleaned"
        stats = pp.batch_clean(str(input_path), output_dir, args.ext)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    else:
        print(f"输入路径不存在: {args.input}")


if __name__ == "__main__":
    main()
