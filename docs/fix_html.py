#!/usr/bin/env python3
"""Fix the broken INTERVIEW_STUDY_GUIDE.html by inserting missing </div> tags.

The body content (lines 352-2413) has 64 missing </div> closes (comment-aware count).
We insert them at structurally-correct points without touching head/style/script/template.
"""
import re
import sys

SRC = 'INTERVIEW_STUDY_GUIDE.html'

with open(SRC, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# ---------------------------------------------------------------------------
# Insertion plan: list of (insert_before_line, num_closes, reason)
# Each entry inserts num_closes </div> lines immediately BEFORE the given line.
# Line numbers refer to the ORIGINAL file (1-indexed).
# We process from bottom to top so earlier line numbers stay valid.
# ---------------------------------------------------------------------------
insertions = [
    # --- STAR stories section (section id="stories", line 1757) ---
    # story2 module@1789 unclosed; story3 module@1817 starts without closing it.
    (1817, 1, "close module story2 @1789"),
    # story3 star-timer@1818 unclosed; h4@1822 starts without closing it.
    (1822, 1, "close star-timer story3 @1818"),
    # story3 alert@1835 unclosed + story3 module@1817 unclosed; h2@1843 starts new section.
    (1843, 2, "close alert@1835 + module story3 @1817"),

    # --- Honesty section: alerts are siblings at 6-space indent ---
    # alert ok@1863 unclosed; h2@1871 starts next subsection.
    (1871, 1, "close alert ok @1863"),
    # alert info@1873 unclosed; next alert@1878 starts.
    (1878, 1, "close alert info @1873"),
    # alert info@1878 unclosed; next alert@1883 starts.
    (1883, 1, "close alert info @1878"),
    # alert warn@1883 unclosed; h2@1891 starts next subsection.
    (1891, 1, "close alert warn @1883"),

    # --- Reverse-questions section ---
    # alert warn@1912 unclosed; </section>@1919 closes the section.
    (1919, 1, "close alert warn @1912"),

    # --- Countdown section (id="countdown", line 1921): alerts are siblings ---
    # alert danger@1924 unclosed; next alert@1933 starts.
    (1933, 1, "close alert danger @1924"),
    # alert warn@1933 unclosed; next alert@1943 starts.
    (1943, 1, "close alert warn @1933"),
    # alert warn@1943 unclosed; next alert@1952 starts.
    (1952, 1, "close alert warn @1943"),
    # alert warn@1952 unclosed; next alert@1961 starts.
    (1961, 1, "close alert warn @1952"),
    # alert ok@1961 unclosed; next alert@1968 starts.
    (1968, 1, "close alert ok @1961"),
    # alert info@1968 unclosed (last in countdown); </section>@1976 closes section.
    (1976, 1, "close alert info @1968"),

    # --- Q&A blocks Q41-Q55: each needs 3 closes (qa-meta, qa-a, qa) before next qa ---
    (2000, 3, "close Q41 qa-meta/qa-a/qa"),
    (2010, 3, "close Q42 qa-meta/qa-a/qa"),
    (2020, 3, "close Q43 qa-meta/qa-a/qa"),
    (2030, 3, "close Q44 qa-meta/qa-a/qa"),
    (2040, 3, "close Q45 qa-meta/qa-a/qa"),
    (2050, 3, "close Q46 qa-meta/qa-a/qa"),
    (2060, 3, "close Q47 qa-meta/qa-a/qa"),
    (2077, 3, "close Q48 qa-meta/qa-a/qa"),
    (2087, 3, "close Q49 qa-meta/qa-a/qa"),
    (2097, 3, "close Q50 qa-meta/qa-a/qa"),
    (2107, 3, "close Q51 qa-meta/qa-a/qa"),
    (2117, 3, "close Q52 qa-meta/qa-a/qa"),
    (2127, 3, "close Q53 qa-meta/qa-a/qa"),
    (2137, 3, "close Q54 qa-meta/qa-a/qa"),
    # Q55 (data-qa-id="Q55" @2137) unclosed; Q56@2147 starts without closing it.
    (2147, 3, "close Q55 qa-meta/qa-a/qa"),

    # --- Q56 (last qa block): 3 closes before the alert@2157 ---
    (2157, 3, "close Q56 qa-meta/qa-a/qa"),

    # --- Interview tab-pane@429 unclosed; learn tab-pane@2160 starts ---
    # This close must go AFTER </section>@2158 and BEFORE the learn pane@2160.
    # In the original file, line 2159 is blank, 2160 is the learn pane open.
    # We insert before 2160 (i.e. at the blank line 2159 position).
    (2160, 1, "close interview tab-pane @429"),
]

# Count total closes to insert
total_insert = sum(n for _, n, _ in insertions)
print(f"Total </div> to insert: {total_insert}")

# Sort insertions by line descending so we insert from bottom up
insertions.sort(key=lambda x: x[0], reverse=True)

# Determine indentation for each insertion by looking at the line we're inserting before
# and matching the context. We'll use the indentation of the element being closed.
# For qa blocks: the qa div is at 6 spaces indent, so closes should be at 6/8/10 spaces.
# Simpler: match the indent of the line we insert before, or the opening div.

def get_close_indent(lines, before_line, nth_close, reason):
    """Determine indentation for the nth close (0-indexed) being inserted before before_line."""
    # Find the opening div line from the reason/context.
    # We'll just use a reasonable indent based on the structure.
    # The qa closes: qa@6sp, qa-a@8sp, qa-meta@10sp -> closes in reverse: 10sp, 8sp, 6sp
    # The alert closes: match the alert's own indent.
    # The module closes: match module indent (6sp).
    # star-timer close: match star-timer indent (10sp).
    
    # Look at the line before_line - 1 (0-indexed: before_line-1) to get context indent
    target = lines[before_line - 1]  # 0-indexed
    target_indent = len(target) - len(target.lstrip())
    
    if 'qa-meta/qa-a/qa' in reason:
        # 3 closes: qa-meta (10sp), qa-a (8sp), qa (6sp)
        indents = [10, 8, 6]
        return ' ' * indents[nth_close]
    
    # For single inserts, match the indent of the opening div.
    # Find the opening div line mentioned in reason.
    m = re.search(r'@(\d+)', reason)
    if m:
        open_line = int(m.group(1))
        open_text = lines[open_line - 1]
        return ' ' * (len(open_text) - len(open_text.lstrip()))
    
    # Fallback: match target indent
    return ' ' * target_indent


new_lines = list(lines)  # copy, we'll insert into this

for before_line, num, reason in insertions:
    # Insert num </div> lines before `before_line` (1-indexed in original).
    # Since we go bottom-up, the line number is still valid in new_lines
    # ONLY for insertions we haven't done yet (which are all above this point).
    # But new_lines has grown from earlier (lower) insertions... 
    # NO: we go descending, so lower insertions happen first and shift lines ABOVE them? 
    # No - inserting before line X shifts all lines >= X up by num. Since we process
    # descending, the next insertion is at a line < X, which was NOT shifted.
    # Wait: inserting at line X adds lines at position X-1 (0-indexed). Lines at positions
    # < X-1 are unchanged. Lines >= X-1 shift. So a later insertion at line Y < X
    # (0-indexed Y-1 < X-1) is unaffected. Correct!
    
    insert_idx = before_line - 1  # 0-indexed position in new_lines
    
    # Build the close lines with proper indentation
    close_lines = []
    for j in range(num):
        indent = get_close_indent(lines, before_line, j, reason)
        close_lines.append(indent + '</div>\n')
    
    # Insert (in order: first close goes first)
    new_lines[insert_idx:insert_idx] = close_lines
    print(f"  Inserted {num} </div> before original line {before_line}: {reason}")

# Write the fixed file
with open(SRC, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print(f"\nWrote {len(new_lines)} lines (was {len(lines)})")

# Verify
with open(SRC, 'r', encoding='utf-8') as f:
    content = f.read()
alllines = content.split('\n')

# Comment-aware div count
def count_divs_comment_aware(text):
    opens = 0
    closes = 0
    in_comment = False
    i = 0
    n = len(text)
    while i < n:
        if in_comment:
            end = text.find('-->', i)
            if end == -1:
                break
            i = end + 3
            in_comment = False
            continue
        cstart = text.find('<!--', i)
        cend = text.find('-->', cstart) if cstart != -1 else -1
        if cstart != -1 and cend != -1:
            # count divs in text[i:cstart]
            seg = text[i:cstart]
            opens += len(re.findall(r'<div[ >]', seg))
            closes += len(re.findall(r'</div>', seg))
            i = cend + 3
        elif cstart != -1:
            seg = text[i:cstart]
            opens += len(re.findall(r'<div[ >]', seg))
            closes += len(re.findall(r'</div>', seg))
            in_comment = True
            i = n
        else:
            seg = text[i:]
            opens += len(re.findall(r'<div[ >]', seg))
            closes += len(re.findall(r'</div>', seg))
            i = n
    return opens, closes

opens, closes = count_divs_comment_aware(content)
print(f"\nComment-aware div count: opens={opens}, closes={closes}, diff={opens-closes}")

# Raw count
raw_opens = len(re.findall(r'<div[ >]', content))
raw_closes = len(re.findall(r'</div>', content))
print(f"Raw div count: opens={raw_opens}, closes={raw_closes}, diff={raw_opens-raw_closes}")

# --- Structural verification: stack-based, comment-aware ---
def strip_comments_linearray(la):
    out = []
    in_comment = False
    for line in la:
        workline = line
        if in_comment:
            idx = workline.find('-->')
            if idx >= 0:
                workline = ' ' * (idx + 3) + workline[idx + 3:]
                in_comment = False
            else:
                workline = ''
        while '<!--' in workline:
            s = workline.find('<!--')
            e = workline.find('-->', s)
            if e >= 0:
                workline = workline[:s] + ' ' * (e - s + 3) + workline[e + 3:]
            else:
                workline = workline[:s]
                in_comment = True
                break
        out.append(workline)
    return out

new_la = new_lines
cleaned_new = strip_comments_linearray(new_la)

# Trace stack over entire file, find interview & learn tab-pane lines
stack_final = []
tab_interview_line = None
tab_learn_line = None
for i, wl in enumerate(cleaned_new):
    ln = i + 1
    toks = sorted([(m.start(), 'open' if m.group() != '</div>' else 'close')
                   for m in re.finditer(r'<div[ >]|</div>', wl)])
    for pos, kind in toks:
        if kind == 'open':
            sub = wl[pos:wl.find('>', pos) + 1]
            cm = re.search(r'data-tab="([^"]*)"', sub)
            if cm:
                if cm.group(1) == 'interview':
                    tab_interview_line = ln
                    print(f"\nInterview tab-pane opens at line {ln}, depth before={len(stack_final)}")
                elif cm.group(1) == 'learn':
                    tab_learn_line = ln
                    print(f"Learn tab-pane opens at line {ln}, depth before={len(stack_final)}")
            stack_final.append((ln, '?'))
        else:
            if stack_final:
                popped = stack_final.pop()
                # Check if a tab-pane closed
                if popped[0] == tab_interview_line:
                    print(f"Interview tab-pane closed at line {ln} (was opened at {popped[0]})")
                elif popped[0] == tab_learn_line:
                    print(f"Learn tab-pane closed at line {ln} (was opened at {popped[0]})")

print(f"\nFinal stack depth (should be 0): {len(stack_final)}")
if len(stack_final) == 0:
    print("✓ ALL DIVS BALANCED")
else:
    print("✗ UNBALANCED - remaining open divs:")
    for s in stack_final:
        print(f"    line {s[0]}")

# Verify tab-panes are siblings (not nested)
if tab_interview_line and tab_learn_line:
    # The interview pane should close BEFORE the learn pane opens
    # Find where interview pane closes
    s2 = []
    interview_close_line = None
    for i, wl in enumerate(cleaned_new):
        ln = i + 1
        toks = sorted([(m.start(), 'open' if m.group() != '</div>' else 'close')
                       for m in re.finditer(r'<div[ >]|</div>', wl)])
        for pos, kind in toks:
            if kind == 'open':
                s2.append(ln)
            else:
                if s2:
                    popped = s2.pop()
                    if popped == tab_interview_line:
                        interview_close_line = ln
        if interview_close_line:
            break
    if interview_close_line and tab_learn_line:
        if interview_close_line < tab_learn_line:
            print(f"✓ Tab panes are SIBLINGS: interview closes at {interview_close_line}, learn opens at {tab_learn_line}")
        else:
            print(f"✗ Tab panes are NESTED: interview closes at {interview_close_line} AFTER learn opens at {tab_learn_line}")
