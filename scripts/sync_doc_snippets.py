"""
sync_doc_snippets.py - 同步 docs/INTERVIEW_STUDY_GUIDE.html 中代码片段与真实文件
=============================================================
【用途】从 backend/app/ 真实文件中抽取 26 个 showCode 引用对应的行,
       替换 HTML 中对应的 body <pre> 和 <template>,消除手抄漂移。
       单一真相原则:body pre 与 template 内容完全相同,且都来自真实文件。

【用法】
    python scripts/sync_doc_snippets.py              # 同步并写文件
    python scripts/sync_doc_snippets.py --dry-run     # 不写文件,只报告

【退出码】
    0 = 成功
    1 = 至少一处失败

【已知限制】
- 外部库(nanobot/、easy-langent/)跨仓库,跳过
- tokenize 是简化版:支持 # 注释、单/三引号字符串、数字、关键字、函数调用
  不支持:f-string 内嵌 {} (但因为我们进 string mode 后不再切分,实际是正确的)
=============================================================
"""
import argparse
import html as html_lib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
HTML = ROOT / "docs" / "INTERVIEW_STUDY_GUIDE.html"

EXTERNAL_PREFIXES = ('nanobot/', 'easy-langent/')

KEYWORDS = {
    'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await',
    'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except',
    'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is', 'lambda',
    'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'try', 'while',
    'with', 'yield',
}

SHOWCODE_RE = re.compile(
    r"""showCode\(\s*event\s*,\s*['"]([^'"]+\.py)['"]\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*['"]([^'"]+)['"]"""
)


def resolve_path(name):
    """将 'engine.py' 或 'backend/app/...' 解析为绝对路径。"""
    if name.startswith('backend/app/'):
        p = ROOT / name
        return p if p.exists() else None
    backend_app = ROOT / 'backend' / 'app'
    for py in backend_app.rglob('*.py'):
        if py.name == name:
            return py
    return None


def extract_lines(filepath, line_start, line_end):
    """读文件,返回 [line_start, line_end] 行的列表(已 strip 末尾换行)。"""
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    if line_start < 1 or line_start > len(lines):
        return None
    end = min(line_end, len(lines))
    return [lines[i].rstrip('\n\r') for i in range(line_start - 1, end)]


def find_comment_start(line):
    """找出行内 # 注释起点(忽略字符串内的 #)。返回 -1 表示无注释。"""
    in_str = None
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if in_str:
            if c == '\\' and i + 1 < n:
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c in ('"', "'"):
            triple = line[i:i+3]
            if triple in ('"""', "'''"):
                in_str = c
                # 找三引号结束
                j = i + 3
                while j < n and line[j:j+3] != triple:
                    j += 1
                in_str = None
                i = j + 3 if j < n else n
                continue
            in_str = c
            i += 1
            continue
        if c == '#':
            return i
        i += 1
    return -1


def tokenize_line(line):
    """把一行 Python 代码转成带 tok-* span 的 HTML。"""
    line = html_lib.escape(line, quote=False)
    ci = find_comment_start(line)
    if ci >= 0:
        before, comment = line[:ci], line[ci:]
        return tokenize_code(before) + f'<span class="tok-com">{comment}</span>'
    return tokenize_code(line)


def tokenize_code(line):
    """无注释部分的代码 tokenize。"""
    result = []
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c in ('"', "'"):
            quote = c
            triple = line[i:i+3] in ('"""', "'''")
            if triple:
                j = i + 3
                while j < n and line[j:j+3] != quote * 3:
                    j += 1
                j = min(j + 3, n)
            else:
                j = i + 1
                while j < n and line[j] != quote:
                    if line[j] == '\\' and j + 1 < n:
                        j += 2
                    else:
                        j += 1
                j = min(j + 1, n)
            result.append(f'<span class="tok-str">{line[i:j]}</span>')
            i = j
        elif c.isdigit():
            j = i
            while j < n and (line[j].isdigit() or line[j] == '.'):
                j += 1
            result.append(f'<span class="tok-num">{line[i:j]}</span>')
            i = j
        elif c.isalpha() or c == '_':
            j = i
            while j < n and (line[j].isalnum() or line[j] == '_'):
                j += 1
            word = line[i:j]
            if word in KEYWORDS:
                result.append(f'<span class="tok-kw">{word}</span>')
            else:
                k = j
                while k < n and line[k] == ' ':
                    k += 1
                if k < n and line[k] == '(':
                    result.append(f'<span class="tok-fn">{word}</span>')
                else:
                    result.append(word)
            i = j
        else:
            result.append(c)
            i += 1
    return ''.join(result)


def make_snippet_html(lines):
    """把代码行列表转成 <pre><code>...</code></pre>(用于 template)。"""
    inner = '\n'.join(tokenize_line(l) for l in lines)
    return f'<pre><code>{inner}</code></pre>'


def make_inner_code(lines):
    """只生成 <code>...</code> 部分(用于 body pre 保留外层 <pre> 属性)。"""
    inner = '\n'.join(tokenize_line(l) for l in lines)
    return f'<code>{inner}</code>'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='不写文件,只报告')
    args = parser.parse_args()

    if not HTML.exists():
        print(f"ERROR: {HTML} not found", file=sys.stderr)
        return 1

    html_text = HTML.read_text(encoding='utf-8')

    # 1. 提取所有 showCode 引用,按 tid 去重
    refs_by_tid = {}
    for m in SHOWCODE_RE.finditer(html_text):
        path, ls, le, tid = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        # 用最后一次出现的(覆盖前面的)
        refs_by_tid[tid] = (path, ls, le)

    # 2. 同步每个引用
    synced = []
    skipped = []
    failed = []

    for tid, (path, ls, le) in refs_by_tid.items():
        if path.startswith(EXTERNAL_PREFIXES):
            skipped.append((tid, path, 'external'))
            continue

        filepath = resolve_path(path)
        if filepath is None:
            failed.append((tid, path, f'resolve failed: {path}'))
            continue

        lines = extract_lines(filepath, ls, le)
        if lines is None:
            failed.append((tid, path, f'{path}:{ls}-{le} out of range'))
            continue

        new_html = make_snippet_html(lines)  # for <template>
        new_inner = make_inner_code(lines)   # for body <pre> (preserve attrs)

        # 替换 body pre:保留原 opening tag(带 data-snippet/onclick)
        body_re = re.compile(
            r'(<pre\s+data-snippet="' + re.escape(tid) + r'"[^>]*>)(.*?)(</pre>)',
            re.DOTALL
        )
        html_text, n_body = body_re.subn(
            lambda m: m.group(1) + new_inner + m.group(3),
            html_text, count=1
        )

        # 替换 template 内容(保留外层 <template id="...">...</template> 标签)
        tmpl_re = re.compile(
            r'(<template\s+id="' + re.escape(tid) + r'">)(.*?)(</template>)',
            re.DOTALL
        )
        html_text, n_tmpl = tmpl_re.subn(
            lambda m: m.group(1) + new_html + m.group(3),
            html_text, count=1
        )

        if n_body == 0 and n_tmpl == 0:
            failed.append((tid, path, f'no body pre or template found for {tid}'))
        else:
            synced.append((tid, path, ls, le, n_body, n_tmpl))

    # 3. 输出报告
    mode = 'DRY RUN' if args.dry_run else 'WRITE'
    print(f"\n=== sync_doc_snippets.py [{mode}] ===")
    print(f"Synced: {len(synced)}")
    for tid, path, ls, le, nb, nt in synced:
        relpath = path if path.startswith('backend/app/') else f'(bare) {path}'
        print(f"  ✓ {tid:30s} {relpath}:{ls}-{le}  body={nb} tmpl={nt}")
    if skipped:
        print(f"\nSkipped (external): {len(skipped)}")
        for tid, path, reason in skipped:
            print(f"  - {tid:30s} {path}")
    if failed:
        print(f"\nFailed: {len(failed)}", file=sys.stderr)
        for tid, path, reason in failed:
            print(f"  ✗ {tid:30s} {reason}", file=sys.stderr)

    if not args.dry_run:
        HTML.write_text(html_text, encoding='utf-8')
        print(f"\nWrote {HTML}")

    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
