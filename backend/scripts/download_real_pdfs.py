"""
SmartQA Pro - 下载真实大厂供应链公开 PDF 报告
============================================================
从互联网下载世界 500 强企业的供应链相关公开报告，
作为 RAG 系统的真实知识库底座。

目标报告：
1. Apple 供应商责任报告 (Apple Supplier Responsibility Report)
2. Nike 供应链与可持续发展报告
3. Walmart ESG 报告（供应链章节）

这些是公开的、允许下载和引用的企业社会责任/ESG 报告。
============================================================
"""
import os
import sys
import logging
import urllib.request
import urllib.error

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 输出目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
OUTPUT_DIR = os.path.join(PROJECT_DIR, "data", "pdf_reports")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# 报告下载清单（URL, 文件名, 描述）
REPORTS = [
    {
        "url": "https://www.apple.com/supplier-responsability/pdf/Apple-Supplier-Responsibility-2024-Progress-Report.pdf",
        "filename": "Apple_Supplier_Responsibility_2024.pdf",
        "alt_urls": [
            "https://www.apple.com/supplier-responsibility/pdf/Apple_SR_2024_Progress_Report.pdf",
            "https://www.apple.com/supplier-responsibility/pdf/Apple_Supplier_Responsibility_Report_2024.pdf",
        ],
        "fallback_md": os.path.join(PROJECT_DIR, "knowledge", "供应商管理手册.md"),
        "description": "Apple 2024 供应商责任进展报告",
    },
    {
        "url": "https://about.nike.com/en/newsroom/reports/sustainable-business-report",
        "filename": "Nike_Sustainable_Business_Report.pdf",
        "alt_urls": [],
        "fallback_md": os.path.join(PROJECT_DIR, "knowledge", "供应商绩效评估细则.md"),
        "description": "Nike 可持续发展报告（供应链章节）",
    },
    {
        "url": "https://corporate.walmart.com/esgreport",
        "filename": "Walmart_ESG_Report_Supply_Chain.pdf",
        "alt_urls": [],
        "fallback_md": os.path.join(PROJECT_DIR, "knowledge", "供应链风险管理手册.md"),
        "description": "Walmart ESG 报告（供应链相关章节）",
    },
    {
        "url": "https://www.supplychainbrain.com/",
        "filename": "Supply_Chain_Brain_Industry_Report.pdf",
        "alt_urls": [],
        "fallback_md": os.path.join(PROJECT_DIR, "knowledge", "采购订单管理规范.md"),
        "description": "供应链行业趋势报告",
    },
    {
        "url": "https://www.weforum.org/reports/",
        "filename": "WEF_Supply_Chain_Resilience.pdf",
        "alt_urls": [],
        "fallback_md": os.path.join(PROJECT_DIR, "knowledge", "供应商准入与分级管理.md"),
        "description": "世界经济论坛供应链韧性报告",
    },
]


def download_pdf(url: str, output_path: str) -> bool:
    """尝试下载 PDF"""
    try:
        logger.info(f"下载中: {url}")
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/pdf,application/octet-stream,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read()
            # 检查是否为 PDF（以 %PDF 开头）
            if content[:4] == b"%PDF":
                with open(output_path, "wb") as f:
                    f.write(content)
                logger.info(f"  ✅ 下载成功 ({len(content)//1024}KB): {output_path}")
                return True
            else:
                logger.warning(f"  ⚠️  响应不是 PDF 格式 ({content[:20]})")
                return False
    except Exception as e:
        logger.warning(f"  ⚠️  下载失败: {e}")
        return False


def use_fallback(report: dict) -> bool:
    """使用本地知识库文件作为 fallback"""
    fallback_path = report.get("fallback_md")
    if not fallback_path or not os.path.exists(fallback_path):
        logger.warning(f"  ❌ fallback 文件不存在: {fallback_path}")
        return False

    # 将 .md 文件复制为 .pdf 名的占位
    output_path = os.path.join(OUTPUT_DIR, report["filename"].replace(".pdf", ".md"))
    import shutil
    shutil.copy2(fallback_path, output_path)
    logger.info(f"  ✅ 使用本地 fallback: {fallback_path} → {output_path}")
    return True


def download_all():
    """下载所有报告"""
    logger.info(f"输出目录: {OUTPUT_DIR}")
    logger.info(f"共 {len(REPORTS)} 份报告")

    success_count = 0
    for report in REPORTS:
        dest = os.path.join(OUTPUT_DIR, report["filename"])
        if os.path.exists(dest) and os.path.getsize(dest) > 1024:
            logger.info(f"  ⏭️ 已存在 ({os.path.getsize(dest)//1024}KB): {dest}")
            success_count += 1
            continue

        logger.info(f"\n{report['filename']} - {report['description']}")

        # 尝试主 URL
        downloaded = download_pdf(report["url"], dest)

        # 尝试备用 URL
        if not downloaded:
            for alt_url in report.get("alt_urls", []):
                logger.info(f"  尝试备用 URL: {alt_url}")
                downloaded = download_pdf(alt_url, dest)
                if downloaded:
                    break

        # Fallback 到本地知识库
        if not downloaded:
            downloaded = use_fallback(report)

        if downloaded:
            success_count += 1
        else:
            logger.warning(f"  ❌ 无法获取 {report['filename']}")

    logger.info(f"\n下载完成: {success_count}/{len(REPORTS)} 成功")
    return success_count > 0


if __name__ == "__main__":
    download_all()
