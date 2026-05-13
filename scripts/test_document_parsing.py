# -*- coding: utf-8 -*-
"""
SmartQA 文档解析链验证脚本
测试三阶回退：opendataloader → pymupdf4llm → pdfplumber
"""
import sys, os, io, logging, time

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("test")

def test_imports():
    """测试各解析库是否可以导入"""
    results = {}
    
    # opendataloader-pdf
    try:
        import opendataloader_pdf
        results["opendataloader"] = "OK"
    except ImportError as e:
        results["opendataloader"] = f"FAIL: {e}"
    
    # pymupdf4llm
    try:
        import pymupdf4llm
        results["pymupdf4llm"] = "OK"
    except ImportError as e:
        results["pymupdf4llm"] = f"FAIL: {e}"
    
    # pdfplumber
    try:
        import pdfplumber
        results["pdfplumber"] = "OK"
    except ImportError as e:
        results["pdfplumber"] = f"FAIL: {e}"
    
    return results


def test_java():
    """检测 Java 版本"""
    import subprocess
    try:
        r = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=10)
        output = r.stderr or r.stdout
        log.info(f"Java: {output.strip().split(chr(10))[0]}")
        return True
    except FileNotFoundError:
        log.warning("Java: 未安装")
        return False
    except Exception as e:
        log.warning(f"Java: 检测异常 - {e}")
        return False


def test_parse_knowledge_files():
    """解析 knowledge/ 目录下的文件（如果有 PDF 的话）"""
    base = os.path.join(os.path.dirname(__file__), "..", "knowledge")
    if not os.path.isdir(base):
        log.warning(f"knowledge 目录不存在: {base}")
        return {}
    
    files = [f for f in os.listdir(base) if f.lower().endswith(('.pdf', '.md', '.txt'))]
    log.info(f"knowledge/ 文件: {len(files)} 个")
    
    # 取第一个文件测试
    for fname in files[:3]:
        fpath = os.path.join(base, fname)
        if fname.lower().endswith('.pdf'):
            log.info(f"\n{'='*40}\n测试 PDF: {fname}")
            test_single_pdf(fpath)
    
    return {"tested": len([f for f in files if f.endswith('.pdf')])}


def test_single_pdf(file_path):
    """测试单个 PDF 的解析链"""
    if not os.path.exists(file_path):
        log.error(f"文件不存在: {file_path}")
        return
    
    log.info(f"文件大小: {os.path.getsize(file_path)} 字节")
    
    # Tier 1: opendataloader
    if test_java():
        try:
            import opendataloader_pdf
            import tempfile
            t0 = time.time()
            with tempfile.TemporaryDirectory() as tmp_dir:
                opendataloader_pdf.convert(
                    input_path=file_path,
                    output_dir=tmp_dir,
                    format="markdown",
                    quiet=True,
                )
                elapsed = time.time() - t0
                md_files = [f for f in os.listdir(tmp_dir) if f.endswith('.md')]
                if md_files:
                    md_path = os.path.join(tmp_dir, md_files[0])
                    with open(md_path, 'r', encoding='utf-8') as f:
                        text = f.read()
                    log.info(f"[Tier1-opendataloader] OK: {len(text)} 字符, 耗时 {elapsed:.2f}s")
                    # 检查是否包含表格
                    has_table = "|" in text and "---" in text
                    log.info(f"  表格检测: {'有表格' if has_table else '无表格'}")
                else:
                    log.warning(f"[Tier1-opendataloader] FAIL: 未生成 md 文件")
        except Exception as e:
            log.warning(f"[Tier1-opendataloader] FAIL: {e}")
    else:
        log.info("[Tier1-opendataloader] SKIP: Java 不可用")
    
    # Tier 2: pymupdf4llm
    try:
        import pymupdf4llm
        t0 = time.time()
        md_text = pymupdf4llm.to_markdown(file_path)
        elapsed = time.time() - t0
        log.info(f"[Tier2-pymupdf4llm] OK: {len(md_text)} 字符, 耗时 {elapsed:.2f}s")
        has_table = "|" in md_text and "---" in md_text
        log.info(f"  表格检测: {'有表格' if has_table else '无表格'}")
    except Exception as e:
        log.warning(f"[Tier2-pymupdf4llm] FAIL: {e}")
    
    # Tier 3: pdfplumber
    try:
        import pdfplumber
        t0 = time.time()
        with pdfplumber.open(file_path) as pdf:
            text_parts = []
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if table:
                        text_parts.append(f"[表格 {len(table)}行 x {len(table[0]) if table else 0}列]")
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            text = "\n\n".join(text_parts)
        elapsed = time.time() - t0
        log.info(f"[Tier3-pdfplumber] OK: {len(text)} 字符, 耗时 {elapsed:.2f}s, {len(pdf.pages)} 页")
    except Exception as e:
        log.warning(f"[Tier3-pdfplumber] FAIL: {e}")


def test_create_and_parse():
    """创建一个简单 PDF 并用三层解析器分别测试"""
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        import tempfile
        
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            tmp_path = f.name
        
        c = canvas.Canvas(tmp_path, pagesize=A4)
        c.setFont("Helvetica", 16)
        c.drawString(100, 750, "SmartQA Document Parsing Test")
        c.setFont("Helvetica", 10)
        c.drawString(100, 700, "This is a test PDF for supply chain QA system.")
        c.drawString(100, 680, "It contains a simple table:")
        
        # 简单表格
        y = 640
        headers = ["Material", "Quantity", "Price"]
        rows = [["MAT-001", "500", "12.50"], ["MAT-002", "300", "8.90"], ["MAT-003", "1200", "3.40"]]
        
        for i, h in enumerate(headers):
            c.drawString(100 + i*120, y, h)
        y -= 20
        for row in rows:
            for i, cell in enumerate(row):
                c.drawString(100 + i*120, y, cell)
            y -= 20
        
        c.save()
        log.info(f"\n创建测试 PDF: {tmp_path} ({os.path.getsize(tmp_path)} 字节)")
        
        # 测试三层解析
        test_single_pdf(tmp_path)
        
        os.unlink(tmp_path)
    except ImportError:
        log.warning("reportlab 未安装，跳过 PDF 创建测试。pip install reportlab")


if __name__ == "__main__":
    log.info("SmartQA 文档解析链验证")
    log.info("=" * 50)
    
    # 1. 测试导入
    log.info("\n--- 1. 导入检测 ---")
    imports = test_imports()
    for k, v in imports.items():
        status = "OK" if v == "OK" else "FAIL"
        log.info(f"  {k}: {status}")
    
    # 2. Java 检测
    log.info("\n--- 2. Java 检测 ---")
    has_java = test_java()
    
    # 3. 如果有 knowledge/ 下的 PDF，测试它们
    log.info("\n--- 3. 解析测试 ---")
    test_parse_knowledge_files()
    
    # 4. 创建测试 PDF 并解析
    log.info("\n--- 4. 创建并解析测试 PDF ---")
    test_create_and_parse()
    
    log.info("\n" + "=" * 50)
    log.info("验证完成")
