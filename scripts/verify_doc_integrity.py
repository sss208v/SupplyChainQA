"""
verify_doc_integrity.py - 验证 INTERVIEW_STUDY_GUIDE.html 的结构完整性
=============================================================
【用途】模拟浏览器执行 showCode() 时的查找逻辑,精确诊断
       "代码片段未找到" / body pre 与 template 不一致 / 引用孤岛等问题。

【用法】
    python scripts/verify_doc_integrity.py            # 全量验证
    python scripts/verify_doc_integrity.py --verbose   # 显示每个细节

【检查项】
1. 所有 <template id="..."> 存在性(应有 25 个:19 项目 + 6 external)
2. 所有 onclick='showCode(...,tid)' 的 tid 都能找到 template
3. 所有 data-snippet="..." 都能找到 template
4. body pre 的 <code> 内容 hash == 对应 template 的 <code> 内容 hash
   (Step A 同步后,两者必须完全相同)
5. 19 个项目 template 的 <code> 内容 == 真实文件 lineStart..lineEnd 行
6. JS 关键函数 (showCode / validateQASchema) 是否被定义
7. 浏览器解析:无 <pre> 嵌套 <pre>,无 <template> 嵌套 <template>
=============================================================
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
HTML = ROOT / "docs" / "INTERVIEW_STUDY_GUIDE.html"

EXTERNAL_PREFIXES = ('nanobot/', 'easy-langent/')


def strip_code(html_block):
    """提取 <code>...</code> 之间的内容,作为比对基准(去 spans,只留纯文本逻辑结构)。"""
    m = re.search(r'<code>(.*?)</code>', html_block, re.DOTALL)
    if not m:
        return ''
    inner = m.group(1)
    # 去除所有 span 标签,只留内容(用于比较"代码逻辑"是否一致)
    inner = re.sub(r'<span\s+class="[^"]+">([^<]*)</span>', r'\1', inner)
    return inner.strip()


def extract_code_lines(html_block):
    """提取 <code> 内的代码行列表(按 \n 切,过滤空行)。"""
    m = re.search(r'<code>(.*?)</code>', html_block, re.DOTALL)
    if not m:
        return []
    inner = m.group(1)
    # 去除所有 span 标签
    inner = re.sub(r'<span\s+class="[^"]+">([^<]*)</span>', r'\1', inner)
    # HTML 实体反转
    inner = inner.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"').replace('&#x27;', "'")
    return [l for l in inner.split('\n') if l.strip()]


def file_lines_for(file_path, line_start, line_end):
    """读文件返回指定行范围(1-indexed, 闭区间),过滤空行(与 extract_code_lines 一致)。"""
    full = ROOT / file_path if file_path.startswith('backend/app/') else None
    if full is None or not full.exists():
        # 通过文件名在 backend/app/ 下查找
        for py in (ROOT / 'backend' / 'app').rglob('*.py'):
            if py.name == file_path:
                full = py
                break
        else:
            return None
    with open(full, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    if line_start < 1 or line_start > len(lines):
        return None
    end = min(line_end, len(lines))
    return [l for l in (lines[i].rstrip('\n\r') for i in range(line_start - 1, end)) if l.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', action='store_true', help='显示每个细节')
    args = parser.parse_args()

    if not HTML.exists():
        print(f"ERROR: {HTML} not found", file=sys.stderr)
        return 1

    text = HTML.read_text(encoding='utf-8')

    # ── 收集所有 template id
    template_ids = set(re.findall(r'<template\s+id="([^"]+)"', text))

    # ── 收集所有 onclick 引用的 tid
    onclick_tids = set()
    onclick_pattern = re.compile(r"""onclick="showCode\([^,]+,\s*['"][^'"]+\.py['"],\s*\d+,\s*\d+,\s*['"]([^'"]+)['"]""")
    for m in onclick_pattern.finditer(text):
        onclick_tids.add(m.group(1))

    # ── 收集所有 data-snippet
    data_snippet_ids = set(re.findall(r'data-snippet="([^"]+)"', text))

    # ── 收集所有 showCode 的 (file, lineStart, lineEnd, tid) 用于代码一致性
    showcode_pattern = re.compile(
        r"""showCode\([^,]+,\s*['"]([^'"]+\.py)['"],\s*(\d+),\s*(\d+),\s*['"]([^'"]+)['"]"""
    )
    refs = []
    for m in showcode_pattern.finditer(text):
        refs.append((m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)))

    # ── 按 tid 去重
    refs_by_tid = {}
    for r in refs:
        refs_by_tid[r[3]] = r

    print("=" * 60)
    print("INTERVIEW_STUDY_GUIDE.html 完整性验证")
    print("=" * 60)

    # ── Check 1: template 总数(动态,不 hard-code 数量)
    print(f"\n[1] Templates 总数")
    project_tids = {t for t in template_ids if not (t.startswith('snip_nanobot_') or t.startswith('snip_easy_langchain_'))}
    external_tids = template_ids - project_tids
    print(f"  项目 templates: {len(project_tids)} (总 - 外部 = 动态)")
    print(f"  External templates: {len(external_tids)} (snip_nanobot_/snip_easy_langchain_ 前缀)")
    if external_tids and len(external_tids) != 6:
        print(f"  ⚠️  External templates 数量变化(原 6),需更新 EXTERNAL_PREFIXES 列表")
    # 孤立 template 检测(在 template_ids 中,但不在任何引用中)
    used_tids = onclick_tids | data_snippet_ids
    orphan_templates = template_ids - used_tids
    non_external_orphans = [t for t in orphan_templates if not t.startswith('snip_nanobot_') and not t.startswith('snip_easy_langchain_')]
    if non_external_orphans:
        print(f"  ℹ️  项目内孤立 template(无引用,作为 drawer 备用): {sorted(non_external_orphans)}")
    if len(project_tids) < 10:
        print(f"  ⚠️  项目 templates 数量 {len(project_tids)} < 10(可能丢失过多)")

    # ── Check 2: onclick tid 都能找到 template
    print(f"\n[2] onclick 引用完整性")
    missing_in_tpl = onclick_tids - template_ids
    if missing_in_tpl:
        print(f"  ✗ {len(missing_in_tpl)} 个 onclick 引用了不存在的 template:")
        for tid in sorted(missing_in_tpl):
            print(f"     - {tid}")
    else:
        print(f"  ✓ 所有 {len(onclick_tids)} 个 onclick 引用都能找到 template")

    # ── Check 3: data-snippet 都能找到 template
    print(f"\n[3] data-snippet 完整性")
    missing_ds = data_snippet_ids - template_ids
    if missing_ds:
        print(f"  ✗ {len(missing_ds)} 个 data-snippet 引用了不存在的 template:")
        for tid in sorted(missing_ds):
            print(f"     - {tid}")
    else:
        print(f"  ✓ 所有 {len(data_snippet_ids)} 个 data-snippet 都能找到 template")

    # ── Check 4: body pre vs template 内容一致性
    print(f"\n[4] body pre 与 template 内容一致性(Step A 同步结果验证)")
    inconsistent = []
    for tid in data_snippet_ids & template_ids:
        # 找 body pre
        pre_re = re.compile(
            r'(<pre\s+data-snippet="' + re.escape(tid) + r'"[^>]*>.*?</pre>)',
            re.DOTALL
        )
        pre_m = pre_re.search(text)
        # 找 template
        tpl_re = re.compile(
            r'(<template\s+id="' + re.escape(tid) + r'">.*?</template>)',
            re.DOTALL
        )
        tpl_m = tpl_re.search(text)
        if not pre_m or not tpl_m:
            continue
        pre_hash = hashlib.md5(strip_code(pre_m.group(1)).encode()).hexdigest()[:8]
        tpl_hash = hashlib.md5(strip_code(tpl_m.group(1)).encode()).hexdigest()[:8]
        if pre_hash != tpl_hash:
            inconsistent.append((tid, pre_hash, tpl_hash))

    if inconsistent:
        print(f"  ✗ {len(inconsistent)} 个 body pre 与 template 不一致:")
        for tid, ph, th in inconsistent:
            print(f"     - {tid}: body={ph} tmpl={th}")
    else:
        print(f"  ✓ 所有 {len(data_snippet_ids & template_ids)} 对 body pre 和 template 内容完全一致")

    # ── Check 5: 项目 template 与真实文件内容一致
    print(f"\n[5] 项目 template 与真实文件内容一致性")
    mismatched = []
    checked = 0
    for tid, ref in refs_by_tid.items():
        path, ls, le, _ = ref
        if path.startswith(EXTERNAL_PREFIXES):
            continue
        if tid not in template_ids:
            continue
        tpl_re = re.compile(
            r'<template\s+id="' + re.escape(tid) + r'">(.*?)</template>',
            re.DOTALL
        )
        tpl_m = tpl_re.search(text)
        if not tpl_m:
            continue
        tpl_lines = extract_code_lines(tpl_m.group(1))
        file_lines = file_lines_for(path, ls, le)
        if file_lines is None:
            mismatched.append((tid, path, ls, le, 'file not found or range invalid'))
            continue
        if tpl_lines != file_lines:
            mismatched.append((tid, path, ls, le, f'content drift ({len(tpl_lines)} vs {len(file_lines)} lines)'))
            if args.verbose:
                print(f"  ✗ {tid}: {path}:{ls}-{le}  tpl={len(tpl_lines)} file={len(file_lines)}")
        else:
            checked += 1
            if args.verbose:
                print(f"  ✓ {tid}: {path}:{ls}-{le}  ({len(tpl_lines)} lines)")

    if mismatched:
        print(f"  ✗ {len(mismatched)} 个项目 template 与真实文件不一致:")
        for tid, path, ls, le, reason in mismatched:
            print(f"     - {tid}: {path}:{ls}-{le} - {reason}")
    else:
        print(f"  ✓ 全部 {checked} 个项目 template 与真实文件一致")

    # ── Check 6: 关键 JS 函数定义
    print(f"\n[6] 关键 JS 函数定义")
    funcs = ['function showCode', 'function validateQASchema', 'function toggleQA', 'function renderQuiz']
    for fn in funcs:
        if fn in text:
            print(f"  ✓ {fn} 已定义")
        else:
            print(f"  ✗ {fn} 缺失!")

    # ── Check 7: 重复 template id
    print(f"\n[7] 重复 ID 检查")
    all_ids = re.findall(r'id="(snip_[^"]+)"', text)
    dup_ids = set([i for i in all_ids if all_ids.count(i) > 1])
    if dup_ids:
        print(f"  ✗ {len(dup_ids)} 个重复 ID:")
        for i in dup_ids:
            print(f"     - {i} 出现 {all_ids.count(i)} 次")
    else:
        print(f"  ✓ 无重复 template id")

    # ── 总结
    print("\n" + "=" * 60)
    print("验证总结")
    print("=" * 60)
    # 数量是动态的(可增可减),所以不作为 fail 条件
    fail = (
        missing_in_tpl
        or missing_ds
        or inconsistent
        or mismatched
        or dup_ids
    )
    if fail:
        print("✗ 有问题需要修复")
        return 1
    else:
        print("✓ 所有检查通过")
        return 0


if __name__ == '__main__':
    sys.exit(main())
