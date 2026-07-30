"""
check_doc_drift.py - 检测 docs/INTERVIEW_STUDY_GUIDE.html 与代码的漂移
=============================================================
【用途】找出文档中所有 (path:line) 形式的代码引用,对照真实文件验证
       1) 路径是否存在
       2) 行号是否在文件范围内
       3) 是否使用了规范的前缀(backend/app/)

【用法】
    python scripts/check_doc_drift.py            # 跑全量, exit 0 = 无漂移
    python scripts/check_doc_drift.py --verbose   # 列出所有引用

【退出码】
    0 = 无漂移
    1 = 发现漂移(打印列表)
    2 = 文件读取失败

【设计原则】
- 只读,不修改任何文件
- 解析两种引用形式:
  A) showCode(event, 'xxx.py', 157, 162, ...)   (函数调用)
  B) <code class="inline">xxx.py:157-162</code>  (内嵌引用)
- 漂移分类:
  PATH_MISSING       - 文件不存在
  PATH_INCOMPLETE     - 路径缺少 backend/app/ 前缀
  LINE_OUT_OF_RANGE  - 行号超出文件实际行数
=============================================================
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
HTML = ROOT / "docs" / "INTERVIEW_STUDY_GUIDE.html"

# 两种引用模式
# 模式 A: showCode(event, 'engine.py', 157, 162, 'snip_xxx')  - 只看第一个引号字符串
SHOWCODE = re.compile(r"""showCode\(\s*event\s*,\s*['"]([^'"]+\.py)['"]""")

# 模式 B: <code class="inline">xxx.py</code> 或 <code class="inline">xxx.py:line-line</code>
INLINE = re.compile(r"""<code\s+class="inline">((?:backend/app/)?[^<]+\.py)(?::(\d+))?(?:-(\d+))?</code>""")

# 规范前缀
CANONICAL_PREFIX = "backend/app/"

# 外部库引用:跨仓库,不验证
EXTERNAL_PREFIXES = ('nanobot/', 'easy-langent/')


def scan_refs(html_text: str):
    """扫描 HTML,返回 (ref_kind, path, line_start, line_end) 元组列表。"""
    refs = []
    seen = set()

    for m in SHOWCODE.finditer(html_text):
        path = m.group(1)
        # showCode 后面跟两个数字参数,捕获 lineStart 和 lineEnd
        rest = html_text[m.end():m.end() + 200]
        nums = re.match(r"\s*,\s*(\d+)\s*,\s*(\d+)", rest)
        if nums:
            ls, le = int(nums.group(1)), int(nums.group(2))
        else:
            ls, le = None, None
        key = ("showcode", path, ls, le)
        if key not in seen:
            seen.add(key)
            refs.append(("showcode", path, ls, le))

    for m in INLINE.finditer(html_text):
        path = m.group(1)
        ls = int(m.group(2)) if m.group(2) else None
        le = int(m.group(3)) if m.group(3) else None
        key = ("inline", path, ls, le)
        if key not in seen:
            seen.add(key)
            refs.append(("inline", path, ls, le))

    return refs


def build_filename_index():
    """扫描 backend/app/ 下所有 .py 文件,返回 {纯文件名: [全路径列表]} 索引。"""
    backend_app = ROOT / "backend" / "app"
    index = {}
    if not backend_app.exists():
        return index
    for py in backend_app.rglob("*.py"):
        # 跳过 venv 和 __pycache__
        if "venv" in str(py) or "__pycache__" in str(py):
            continue
        index.setdefault(py.name, []).append(py)
    return index


def resolve_bare_filename(name, index):
    """纯文件名 → 全路径(相对 ROOT)。可能多个,返回唯一确定的那个。"""
    candidates = index.get(name, [])
    if len(candidates) == 1:
        return str(candidates[0].relative_to(ROOT)).replace("\\", "/")
    return None  # 0 或多个 → 无法确定


def check_refs(refs, index):
    """对照真实文件,返回漂移列表 [(kind, path, detail)]。"""
    drift = []
    file_cache = {}  # 绝对路径 -> 行数

    for kind, path, ls, le in refs:
        # 外部库引用(nanobot / easy-langent): 跨仓库,跳过
        if path.startswith(EXTERNAL_PREFIXES):
            continue

        # 命令文本检测:`python xxx.py` / `bash xxx.sh` 等命令形式
        # 包含空格或以 "python"/"bash"/"sh" 等开头 → 不是代码引用,跳过
        if ' ' in path or path.startswith(('python ', 'bash ', 'sh ', 'npm ', 'pytest ')):
            continue

        # 路径前缀检查
        if not path.startswith(CANONICAL_PREFIX):
            # inline 引用 + 纯文件名(无 /): 跳过(只用于显示)
            if "/" not in path:
                if kind == "inline":
                    continue
                # showCode 引用纯文件名: 通过索引解析
                resolved = resolve_bare_filename(path, index)
                if resolved is None:
                    if path in index:
                        drift.append((kind, path, f"PATH_AMBIGUOUS (candidates: {[str(p.relative_to(ROOT)).replace(chr(92), '/') for p in index[path]]})"))
                    else:
                        drift.append((kind, path, f"PATH_MISSING (no {path} anywhere in backend/app/)"))
                    continue
                # 解析成功,只验证行号,不报告 prefix 漂移
                full_path = ROOT / resolved
            elif path.startswith("app/"):
                # app/core/xxx.py → backend/app/core/xxx.py(只缺 backend/ 前缀)
                full_path = ROOT / "backend" / path
            else:
                # 已经是相对路径但缺前缀
                full_path = ROOT / CANONICAL_PREFIX / path
        else:
            full_path = ROOT / path

        # 文件存在性
        if not full_path.exists():
            drift.append((kind, path, f"PATH_MISSING ({full_path})"))
            continue

        # 行号范围检查
        if ls is not None:
            cache_key = str(full_path)
            if cache_key not in file_cache:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    file_cache[cache_key] = sum(1 for _ in f)
            n_lines = file_cache[cache_key]
            if ls > n_lines or (le is not None and le > n_lines):
                drift.append((kind, path, f"LINE_OUT_OF_RANGE (file has {n_lines} lines, ref is {ls}-{le})"))

    return drift


def main() -> int:
    if not HTML.exists():
        print(f"ERROR: {HTML} not found", file=sys.stderr)
        return 2

    html_text = HTML.read_text(encoding="utf-8")
    refs = scan_refs(html_text)
    index = build_filename_index()

    verbose = "--verbose" in sys.argv

    if verbose:
        print(f"Scanned {len(refs)} code references:")
        for kind, path, ls, le in sorted(refs, key=lambda x: (x[1], x[2] or 0)):
            line_info = f":{ls}" + (f"-{le}" if le and le != ls else "") if ls else ""
            print(f"  [{kind:8s}] {path}{line_info}")
        print(f"  Index: {len(index)} unique filenames in backend/app/")
        print()

    drift = check_refs(refs, index)
    print(f"Scanned {len(refs)} refs, {len(drift)} drift(s)")

    if drift:
        # 按漂移类型分组
        by_kind = {}
        for kind, path, detail in drift:
            by_kind.setdefault(detail.split(" ")[0], []).append((kind, path, detail))
        for dtype in sorted(by_kind.keys()):
            print(f"\n  [{dtype}] ({len(by_kind[dtype])} items)")
            for kind, path, detail in by_kind[dtype][:20]:  # 每类最多列 20 条
                print(f"    - {path}  ({detail})")
            if len(by_kind[dtype]) > 20:
                print(f"    ... and {len(by_kind[dtype]) - 20} more")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
