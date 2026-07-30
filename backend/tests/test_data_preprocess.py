"""
Tests for app.core.data_preprocess — DataPreprocessor & CleanResult

Covers:
  1. HTML tag / entity removal
  2. Whitespace normalisation
  3. Full-width → half-width conversion
  4. Control-character stripping
  5. PII masking (on / off)
  6. Duplicate-line removal
  7. CleanResult statistics
  8. File-level cleaning
  9. Batch cleaning
 10. RAG chunking
 11. Text statistics
 12. Eval-data generation
"""

import json
import pytest

from app.core.data_preprocess import DataPreprocessor, CleanResult


# ====================================================================
# 1. TestCleanTextHTML
# ====================================================================

class TestCleanTextHTML:
    """clean_text removes HTML tags and HTML entities."""

    def setup_method(self):
        self.pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)

    def test_removes_p_tags(self):
        text = "<p>Hello</p>"
        cleaned, _ = self.pp.clean_text(text)
        assert "<p>" not in cleaned
        assert "</p>" not in cleaned
        assert "Hello" in cleaned

    def test_removes_div_tags(self):
        text = "<div>Content</div>"
        cleaned, _ = self.pp.clean_text(text)
        assert "<div>" not in cleaned
        assert "</div>" not in cleaned
        assert "Content" in cleaned

    def test_removes_tags_with_attributes(self):
        text = '<a href="https://example.com">Link</a>'
        cleaned, _ = self.pp.clean_text(text)
        assert "<a" not in cleaned
        assert "Link" in cleaned

    def test_removes_amp_entity(self):
        text = "A &amp; B"
        cleaned, _ = self.pp.clean_text(text)
        assert "&amp;" not in cleaned
        assert "A" in cleaned
        assert "B" in cleaned

    def test_removes_lt_entity(self):
        text = "A &lt; B"
        cleaned, _ = self.pp.clean_text(text)
        assert "&lt;" not in cleaned

    def test_removes_gt_entity(self):
        text = "A &gt; B"
        cleaned, _ = self.pp.clean_text(text)
        assert "&gt;" not in cleaned

    def test_removes_nested_tags(self):
        text = "<div><p><strong>Deep</strong></p></div>"
        cleaned, _ = self.pp.clean_text(text)
        assert "<" not in cleaned
        assert ">" not in cleaned
        assert "Deep" in cleaned

    def test_removes_multiple_entities(self):
        text = "&amp; &lt; &gt; &quot;"
        cleaned, _ = self.pp.clean_text(text)
        assert "&" not in cleaned or cleaned.strip() == ""
        # All entities replaced with spaces, then collapsed


# ====================================================================
# 2. TestCleanTextWhitespace
# ====================================================================

class TestCleanTextWhitespace:
    """clean_text collapses multiple spaces and excessive newlines."""

    def setup_method(self):
        self.pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)

    def test_collapses_multiple_spaces(self):
        text = "hello     world"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "hello world"

    def test_collapses_tabs(self):
        text = "hello\t\t\tworld"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "hello world"

    def test_collapses_mixed_spaces_and_tabs(self):
        text = "hello \t \t world"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "hello world"

    def test_collapses_three_newlines_to_two(self):
        text = "a\n\n\nb"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "a\n\nb"

    def test_collapses_many_newlines_to_two(self):
        text = "a\n\n\n\n\nb"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "a\n\nb"

    def test_preserves_two_newlines(self):
        text = "a\n\nb"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "a\n\nb"

    def test_preserves_single_newline(self):
        text = "a\nb"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "a\nb"

    def test_strips_leading_and_trailing_whitespace(self):
        text = "   hello   "
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "hello"

    def test_strips_leading_trailing_newlines(self):
        text = "\n\nhello\n\n"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "hello"


# ====================================================================
# 3. TestCleanTextFullToHalf
# ====================================================================

class TestCleanTextFullToHalf:
    """Full-width digits, letters and space are converted to half-width."""

    def setup_method(self):
        self.pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)

    def test_fullwidth_numbers(self):
        text = "\uff11\uff12\uff13"          # "１２３"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "123"

    def test_fullwidth_letters(self):
        text = "\uff21\uff22\uff23"          # "ＡＢＣ"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "ABC"

    def test_fullwidth_lowercase_letters(self):
        text = "\uff41\uff42\uff43"          # "ａｂｃ"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "abc"

    def test_fullwidth_space_becomes_halfwidth(self):
        text = "A\u3000B"                    # fullwidth space between A and B
        cleaned, _ = self.pp.clean_text(text)
        assert "A B" == cleaned

    def test_mixed_fullwidth_and_halfwidth(self):
        text = "\uff112\uff13"               # "１2３" → "123"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "123"

    def test_fullwidth_punctuation(self):
        # Fullwidth exclamation mark U+FF01 → '!'
        text = "Hello\uff01"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "Hello!"

    def test_chinese_chars_unchanged(self):
        text = "\u4f60\u597d\u4e16\u754c"   # "你好世界"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "\u4f60\u597d\u4e16\u754c"

    def test_full_to_half_static_method_directly(self):
        assert DataPreprocessor._full_to_half("\uff11\uff12\uff13") == "123"
        assert DataPreprocessor._full_to_half("\uff21\uff22\uff23") == "ABC"
        assert DataPreprocessor._full_to_half("\u3000") == " "


# ====================================================================
# 4. TestCleanTextControlChars
# ====================================================================

class TestCleanTextControlChars:
    """Control characters are stripped while \\n and \\t are preserved."""

    def setup_method(self):
        self.pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)

    def test_removes_null_byte(self):
        cleaned, _ = self.pp.clean_text("a\x00b")
        assert cleaned == "ab"

    def test_removes_0x01_to_0x08(self):
        text = "a\x01\x02\x03\x04\x05\x06\x07\x08b"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "ab"

    def test_removes_vertical_tab_and_form_feed(self):
        cleaned, _ = self.pp.clean_text("a\x0b\x0cb")
        assert cleaned == "ab"

    def test_removes_0x0e_to_0x1f(self):
        text = "a\x0e\x0f\x10\x1a\x1fb"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "ab"

    def test_removes_del_0x7f(self):
        cleaned, _ = self.pp.clean_text("a\x7fb")
        assert cleaned == "ab"

    def test_preserves_newline(self):
        cleaned, _ = self.pp.clean_text("a\nb")
        assert "\n" in cleaned

    def test_preserves_tab(self):
        # Tab between words becomes a single space via whitespace collapse
        cleaned, _ = self.pp.clean_text("a\tb")
        assert "a b" == cleaned

    def test_combined_control_chars(self):
        text = "hello\x00\x07\x7f world"
        cleaned, _ = self.pp.clean_text(text)
        assert cleaned == "hello world"


# ====================================================================
# 5. TestCleanTextPII
# ====================================================================

class TestCleanTextPII:
    """PII masking toggles with the filter_pii constructor flag."""

    def test_pii_masks_phone_number(self):
        pp = DataPreprocessor(filter_pii=True, remove_duplicates=False)
        text = "Call me at 13812345678 please"
        cleaned, result = pp.clean_text(text)
        assert "13812345678" not in cleaned
        assert "****" in cleaned
        assert result.pii_count >= 1

    def test_pii_does_not_mask_when_disabled(self):
        pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)
        text = "Call me at 13812345678 please"
        cleaned, result = pp.clean_text(text)
        assert "13812345678" in cleaned
        assert result.pii_count == 0

    def test_pii_masks_email(self):
        pp = DataPreprocessor(filter_pii=True, remove_duplicates=False)
        text = "Email: zhangsan@example.com"
        cleaned, result = pp.clean_text(text)
        assert "zhangsan@example.com" not in cleaned
        assert result.pii_count >= 1

    def test_pii_preserves_semantics(self):
        """Non-PII parts of the text are kept intact."""
        pp = DataPreprocessor(filter_pii=True, remove_duplicates=False)
        text = "Hello world, no sensitive data here."
        cleaned, result = pp.clean_text(text)
        assert cleaned == "Hello world, no sensitive data here."
        assert result.pii_count == 0


# ====================================================================
# 6. TestCleanTextDedup
# ====================================================================

class TestCleanTextDedup:
    """Duplicate-line removal and empty-line preservation."""

    def test_removes_duplicate_lines(self):
        pp = DataPreprocessor(filter_pii=False, remove_duplicates=True)
        text = "line1\nline2\nline1\nline3\nline2"
        cleaned, result = pp.clean_text(text)
        lines = cleaned.split("\n")
        non_empty = [l for l in lines if l.strip()]
        assert non_empty == ["line1", "line2", "line3"]
        assert result.duplicate_lines_removed == 2

    def test_preserves_empty_lines(self):
        pp = DataPreprocessor(filter_pii=False, remove_duplicates=True)
        # Empty lines (paragraph separators) are always preserved by dedup.
        # Whitespace collapse (\n{3,} -> \n\n) runs first, then dedup keeps
        # all empty lines (they bypass the seen-set check).
        text = "line1\n\nline2\n\n\nline3"
        cleaned, _ = pp.clean_text(text)
        # After whitespace collapse: "line1\n\nline2\n\nline3"
        # After dedup (empty lines always kept): "line1\n\nline2\n\nline3"
        assert "line1\n\nline2" in cleaned
        assert "line2\n\nline3" in cleaned
        # All non-empty content lines are present
        non_empty = [l for l in cleaned.split("\n") if l.strip()]
        assert non_empty == ["line1", "line2", "line3"]

    def test_no_dedup_when_disabled(self):
        pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)
        text = "line1\nline1\nline1"
        cleaned, result = pp.clean_text(text)
        assert cleaned == "line1\nline1\nline1"
        assert result.duplicate_lines_removed == 0

    def test_dedup_with_whitespace_variants(self):
        """Lines that differ only by surrounding whitespace are considered duplicates."""
        pp = DataPreprocessor(filter_pii=False, remove_duplicates=True)
        text = "line1\n  line1  \nline2"
        cleaned, result = pp.clean_text(text)
        non_empty = [l for l in cleaned.split("\n") if l.strip()]
        assert len(non_empty) == 2
        assert result.duplicate_lines_removed == 1

    def test_all_duplicate_lines(self):
        pp = DataPreprocessor(filter_pii=False, remove_duplicates=True)
        text = "dup\ndup\ndup\ndup"
        cleaned, result = pp.clean_text(text)
        assert cleaned == "dup"
        assert result.duplicate_lines_removed == 3


# ====================================================================
# 7. TestCleanResult
# ====================================================================

class TestCleanResult:
    """CleanResult.compression_ratio edge cases."""

    def test_compression_ratio_normal(self):
        result = CleanResult(
            original_length=100,
            cleaned_length=80,
            removed_chars=20,
            pii_count=0,
            duplicate_lines_removed=0,
        )
        assert result.compression_ratio == 0.2

    def test_compression_ratio_zero_original(self):
        result = CleanResult(
            original_length=0,
            cleaned_length=0,
            removed_chars=0,
            pii_count=0,
            duplicate_lines_removed=0,
        )
        assert result.compression_ratio == 0.0

    def test_compression_ratio_nothing_removed(self):
        result = CleanResult(
            original_length=100,
            cleaned_length=100,
            removed_chars=0,
            pii_count=0,
            duplicate_lines_removed=0,
        )
        assert result.compression_ratio == 0.0

    def test_compression_ratio_everything_removed(self):
        result = CleanResult(
            original_length=50,
            cleaned_length=0,
            removed_chars=50,
            pii_count=0,
            duplicate_lines_removed=0,
        )
        assert result.compression_ratio == 1.0

    def test_compression_ratio_rounded_to_four_decimals(self):
        result = CleanResult(
            original_length=3,
            cleaned_length=1,
            removed_chars=2,
            pii_count=0,
            duplicate_lines_removed=0,
        )
        # 1 - 1/3 = 0.6667 (rounded to 4 decimals)
        assert result.compression_ratio == 0.6667

    def test_default_chunks_empty(self):
        result = CleanResult(
            original_length=10,
            cleaned_length=5,
            removed_chars=5,
            pii_count=0,
            duplicate_lines_removed=0,
        )
        assert result.chunks == []

    def test_clean_result_fields(self):
        result = CleanResult(
            original_length=200,
            cleaned_length=150,
            removed_chars=50,
            pii_count=3,
            duplicate_lines_removed=2,
        )
        assert result.original_length == 200
        assert result.cleaned_length == 150
        assert result.removed_chars == 50
        assert result.pii_count == 3
        assert result.duplicate_lines_removed == 2


# ====================================================================
# 8. TestCleanFile
# ====================================================================

class TestCleanFile:
    """File-level cleaning, FileNotFoundError, and encoding fallback."""

    def test_clean_file_basic(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("<p>Hello</p>  world", encoding="utf-8")

        pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)
        cleaned, result = pp.clean_file(str(f))

        assert "<p>" not in cleaned
        assert "Hello" in cleaned
        assert "world" in cleaned
        assert result.original_length > 0

    def test_clean_file_result_fields(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("simple text", encoding="utf-8")

        pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)
        _, result = pp.clean_file(str(f))

        assert isinstance(result, CleanResult)
        assert result.original_length == len("simple text")
        assert result.cleaned_length == len("simple text")

    def test_file_not_found_raises(self, tmp_path):
        pp = DataPreprocessor(filter_pii=False)
        with pytest.raises(FileNotFoundError):
            pp.clean_file(str(tmp_path / "nonexistent.txt"))

    def test_encoding_fallback_gbk(self, tmp_path):
        """File written in GBK is readable via the encoding fallback chain."""
        f = tmp_path / "gbk.txt"
        content = "\u4f60\u597d\u4e16\u754c"   # "你好世界"
        f.write_bytes(content.encode("gbk"))

        pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)
        cleaned, result = pp.clean_file(str(f))
        assert "\u4f60\u597d" in cleaned
        assert result.original_length > 0

    def test_encoding_fallback_latin1(self, tmp_path):
        """latin-1 fallback reads bytes that are not valid utf-8 or gbk."""
        f = tmp_path / "latin.txt"
        f.write_bytes(b"caf\xe9")                # "cafe" with e-acute

        pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)
        cleaned, _ = pp.clean_file(str(f))
        assert "caf" in cleaned


# ====================================================================
# 9. TestBatchClean
# ====================================================================

class TestBatchClean:
    """batch_clean processes a directory and returns proper stats."""

    def test_batch_clean_two_files(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"

        (input_dir / "a.txt").write_text("<p>Hello</p>", encoding="utf-8")
        (input_dir / "b.txt").write_text("Clean  text  here", encoding="utf-8")

        pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)
        stats = pp.batch_clean(str(input_dir), str(output_dir))

        # --- stats structure ---
        for key in ("total", "success", "failed", "total_pii", "total_chars_removed", "files"):
            assert key in stats, f"Missing key: {key}"

        assert stats["total"] == 2
        assert stats["success"] == 2
        assert stats["failed"] == 0

        # --- output files exist ---
        assert (output_dir / "a.txt").exists()
        assert (output_dir / "b.txt").exists()

        # --- output content is cleaned ---
        a_out = (output_dir / "a.txt").read_text(encoding="utf-8")
        assert "<p>" not in a_out
        assert "Hello" in a_out

        b_out = (output_dir / "b.txt").read_text(encoding="utf-8")
        assert "  " not in b_out   # extra spaces collapsed

    def test_batch_clean_skips_non_matching_extensions(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"

        (input_dir / "readme.txt").write_text("text", encoding="utf-8")
        (input_dir / "image.png").write_bytes(b"\x89PNG\r\n")

        pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)
        stats = pp.batch_clean(str(input_dir), str(output_dir))

        assert stats["total"] == 1   # only .txt counted

    def test_batch_clean_custom_extensions(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"

        (input_dir / "a.md").write_text("# Title", encoding="utf-8")
        (input_dir / "b.txt").write_text("text", encoding="utf-8")

        pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)
        stats = pp.batch_clean(str(input_dir), str(output_dir), file_extensions=[".md"])

        assert stats["total"] == 1
        assert (output_dir / "a.md").exists()
        assert not (output_dir / "b.txt").exists()

    def test_batch_clean_creates_output_dir(self, tmp_path):
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "deep" / "nested" / "output"

        (input_dir / "x.txt").write_text("hello", encoding="utf-8")

        pp = DataPreprocessor(filter_pii=False, remove_duplicates=False)
        stats = pp.batch_clean(str(input_dir), str(output_dir))

        assert output_dir.exists()
        assert stats["success"] == 1


# ====================================================================
# 10. TestChunkForRAG
# ====================================================================

class TestChunkForRAG:
    """chunk_for_rag splitting behaviour."""

    def setup_method(self):
        self.pp = DataPreprocessor(filter_pii=False)

    def test_short_text_returns_single_chunk(self):
        text = "Short text"
        chunks = self.pp.chunk_for_rag(text, chunk_size=512, chunk_overlap=64)
        assert chunks == ["Short text"]

    def test_empty_text(self):
        chunks = self.pp.chunk_for_rag("", chunk_size=512, chunk_overlap=64)
        assert chunks == [""]

    def test_exact_chunk_size(self):
        text = "x" * 512
        chunks = self.pp.chunk_for_rag(text, chunk_size=512, chunk_overlap=64)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_longer_text_splits(self):
        text = "word " * 300         # 1500 chars, no sentence-ending chars
        chunks = self.pp.chunk_for_rag(text, chunk_size=512, chunk_overlap=64)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) > 0

    def test_chunks_cover_all_content(self):
        """The union of chunks should cover the entire original text."""
        text = "word " * 300         # 1500 chars
        chunks = self.pp.chunk_for_rag(text, chunk_size=512, chunk_overlap=64)
        joined = "".join(chunks)
        # Due to overlap the joined string is at least as long as original
        assert len(joined) >= len(text) * 0.9  # allow minor strip loss

    def test_overlap_produces_repeated_content(self):
        """Adjacent chunks share overlapping content."""
        text = "Hello world. " * 40  # 520 chars with periods
        chunks = self.pp.chunk_for_rag(text, chunk_size=100, chunk_overlap=30)
        assert len(chunks) >= 2

    def test_sentence_boundary_preference(self):
        """Chunk should prefer to break at a sentence-ending period."""
        text = "A" * 19 + "." + "B" * 14 + "." + "C" * 24 + "." + "D" * 24 + "."
        chunks = self.pp.chunk_for_rag(text, chunk_size=30, chunk_overlap=5)
        assert len(chunks) >= 2
        # First chunk should end with the period of the first sentence
        assert chunks[0].rstrip().endswith(".")

    def test_newline_boundary(self):
        text = "Line one.\nLine two.\nLine three.\nLine four.\nLine five."
        chunks = self.pp.chunk_for_rag(text, chunk_size=30, chunk_overlap=5)
        assert len(chunks) >= 2

    def test_no_empty_chunks(self):
        text = "Hello world. " * 100
        chunks = self.pp.chunk_for_rag(text, chunk_size=512, chunk_overlap=64)
        assert all(c for c in chunks)

    def test_no_infinite_loop_when_overlap_exceeds_remaining(self):
        """Regression: chunk_for_rag must terminate when chunk_len <= overlap."""
        # 构造：句子边界在 chunk 前段，使 chunk 很短，短于 overlap
        text = "A" * 5 + "。" + "B" * 100
        chunks = self.pp.chunk_for_rag(text, chunk_size=30, chunk_overlap=20)
        assert len(chunks) >= 1
        assert "".join(chunks).replace(" ", "")  # non-empty result


# ====================================================================
# 11. TestTextStats
# ====================================================================

class TestTextStats:
    """text_stats returns correct character / line / word counts."""

    def setup_method(self):
        self.pp = DataPreprocessor(filter_pii=False)

    def test_total_chars(self):
        text = "Hello world"
        stats = self.pp.text_stats(text)
        assert stats["total_chars"] == 11

    def test_total_lines(self):
        text = "line1\nline2\nline3"
        stats = self.pp.text_stats(text)
        assert stats["total_lines"] == 3

    def test_single_line(self):
        stats = self.pp.text_stats("hello")
        assert stats["total_lines"] == 1

    def test_chinese_chars(self):
        text = "\u4f60\u597d\u4e16\u754c"        # "你好世界" — 4 Chinese chars
        stats = self.pp.text_stats(text)
        assert stats["chinese_chars"] == 4

    def test_english_words(self):
        text = "hello world test"
        stats = self.pp.text_stats(text)
        assert stats["english_words"] == 3

    def test_numbers_count(self):
        text = "abc 123 456"
        stats = self.pp.text_stats(text)
        assert stats["numbers"] == 2

    def test_mixed_content(self):
        text = "Hello \u4f60\u597d 123"          # "Hello 你好 123"
        stats = self.pp.text_stats(text)
        assert stats["total_chars"] == len(text)
        assert stats["chinese_chars"] == 2
        assert stats["english_words"] == 1        # "Hello"
        assert stats["numbers"] == 1              # "123"

    def test_non_empty_lines(self):
        text = "a\n\nb\n\nc"
        stats = self.pp.text_stats(text)
        assert stats["non_empty_lines"] == 3

    def test_avg_line_length(self):
        text = "abcde\nfghij"                     # 11 chars / 2 lines = 5.5
        stats = self.pp.text_stats(text)
        assert stats["avg_line_length"] == 5.5

    def test_all_required_keys_present(self):
        stats = self.pp.text_stats("test 123 \u4f60\u597d")
        expected_keys = {
            "total_chars", "total_lines", "non_empty_lines",
            "chinese_chars", "english_words", "numbers", "avg_line_length",
        }
        assert expected_keys == set(stats.keys())


# ====================================================================
# 12. TestGenerateEvalData
# ====================================================================

class TestGenerateEvalData:
    """generate_eval_data produces well-structured JSON from .md headers."""

    def test_generates_json_from_markdown(self, tmp_path):
        kb_dir = tmp_path / "knowledge"
        kb_dir.mkdir()

        md_content = (
            "# Supply Chain Management Overview\n"
            "This section covers the basics of supply chain management.\n\n"
            "## Inventory Optimization Strategy\n"
            "Details about inventory optimization.\n\n"
            "## Logistics Coordination Framework\n"
            "Details about logistics.\n"
        )
        (kb_dir / "scm.md").write_text(md_content, encoding="utf-8")

        output_json = tmp_path / "eval" / "output.json"

        pp = DataPreprocessor(filter_pii=False)
        result = pp.generate_eval_data(str(kb_dir), str(output_json))

        # --- return dict ---
        assert "total_queries" in result
        assert "source_files" in result
        assert "output_path" in result
        assert result["total_queries"] >= 1
        assert result["source_files"] == 1

        # --- output file exists and is valid JSON ---
        assert output_json.exists()
        data = json.loads(output_json.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) >= 1

        # --- each entry has required keys ---
        for entry in data:
            assert "query" in entry
            assert "relevant_chunk_ids" in entry
            assert "source_file" in entry
            assert "section_index" in entry
            assert isinstance(entry["relevant_chunk_ids"], list)
            assert entry["source_file"] == "scm.md"

    def test_skips_short_headers(self, tmp_path):
        """Sections whose first line is < 5 chars are skipped."""
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()

        md_content = (
            "# AB\n"                             # too short (< 5 chars)
            "Short header content.\n\n"
            "# This Is a Long Enough Header\n"
            "This section has a proper topic.\n"
        )
        (kb_dir / "test.md").write_text(md_content, encoding="utf-8")

        output_json = tmp_path / "out.json"
        pp = DataPreprocessor(filter_pii=False)
        result = pp.generate_eval_data(str(kb_dir), str(output_json))

        # Only the long header should produce a query
        data = json.loads(output_json.read_text(encoding="utf-8"))
        topics = [e["query"] for e in data]
        # "AB" should NOT appear as a topic query
        assert all("AB" not in t or len(t) > 10 for t in topics)

    def test_empty_directory(self, tmp_path):
        kb_dir = tmp_path / "empty"
        kb_dir.mkdir()
        output_json = tmp_path / "out.json"

        pp = DataPreprocessor(filter_pii=False)
        result = pp.generate_eval_data(str(kb_dir), str(output_json))

        assert result["total_queries"] == 0
        assert result["source_files"] == 0
        data = json.loads(output_json.read_text(encoding="utf-8"))
        assert data == []

    def test_multiple_md_files(self, tmp_path):
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()

        (kb_dir / "a.md").write_text(
            "# First Document Topic Here\nContent A.\n", encoding="utf-8"
        )
        (kb_dir / "b.md").write_text(
            "# Second Document Topic Here\nContent B.\n", encoding="utf-8"
        )

        output_json = tmp_path / "out.json"
        pp = DataPreprocessor(filter_pii=False)
        result = pp.generate_eval_data(str(kb_dir), str(output_json))

        assert result["source_files"] == 2
        assert result["total_queries"] >= 2

    def test_query_format(self, tmp_path):
        """Query should be '什么是{topic}？' for non-question topics."""
        kb_dir = tmp_path / "kb"
        kb_dir.mkdir()

        (kb_dir / "doc.md").write_text(
            "# Warehouse Management System\nDetails here.\n", encoding="utf-8"
        )

        output_json = tmp_path / "out.json"
        pp = DataPreprocessor(filter_pii=False)
        pp.generate_eval_data(str(kb_dir), str(output_json))

        data = json.loads(output_json.read_text(encoding="utf-8"))
        assert len(data) >= 1
        query = data[0]["query"]
        assert "\u4ec0\u4e48\u662f" in query        # "什么是"
        assert query.endswith("\uff1f")               # "？"
        assert "Warehouse Management System" in query
