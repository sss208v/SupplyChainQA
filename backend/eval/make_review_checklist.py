# -*- coding: utf-8 -*-
"""生成评测集人工审核清单：把 eval_set_clean.json 按 source_file 分组，
每条 QA 旁自动附上源文档中含对应关键数字的原文行，并标记两类风险：
- 关键事实同时出现在其他源文档（模板重复/跨文档冲突，影响题目锚定性）
- 与其他题目高度相似（近重复题）

纯本地脚本，不调 LLM。输出两份：
- eval_review_checklist.md   只读清单（存档用）
- eval_review_checklist.html 交互审核页（浏览器打开，PASS/FIX/DROP 单选 +
  可直接编辑题干/reference + 备注，进度存 localStorage，一键导出定稿 JSON）

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\make_review_checklist.py
"""
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.build_eval_set import KNOWLEDGE_DIR, EVAL_DIR

IN_PATH = os.path.join(EVAL_DIR, "eval_set_clean.json")
OUT_PATH = os.path.join(EVAL_DIR, "eval_review_checklist.md")
OUT_HTML = os.path.join(EVAL_DIR, "eval_review_checklist.html")

# 数字锚点：可带 ≥≤± 前缀、% 后缀、最多 3 个汉字单位尾巴（如 3个工作、4小时、12位）
TOKEN_RE = re.compile(r"[≥≤±>]?\d[\d.]*\s*[%％]?[\u4e00-\u9fa5]{0,3}")
MAX_LINES_PER_ITEM = 8

# 交互审核页模板（单文件无依赖；进度存 localStorage，导出定稿/审核记录 JSON）
HTML_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>评测集人工审核 eval_set_clean.json</title>
<style>
  body{font-family:"Microsoft YaHei",system-ui,sans-serif;margin:0;background:#f5f6f8;color:#222}
  #bar{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:10px 18px;
       display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:9}
  #bar b{font-size:15px}
  .btn{padding:6px 14px;border:1px solid #bbb;border-radius:6px;background:#fff;cursor:pointer}
  .btn.primary{background:#2b6de0;color:#fff;border-color:#2b6de0}
  .btn.on{background:#e8f0fe;border-color:#2b6de0;color:#2b6de0}
  #wrap{max-width:980px;margin:0 auto;padding:14px}
  h2{font-size:15px;color:#555;margin:22px 4px 6px}
  .card{background:#fff;border:1px solid #e2e4e8;border-left:5px solid #c8ccd4;
        border-radius:8px;padding:12px 16px;margin-bottom:10px}
  .card.PASS{border-left-color:#2ea44f}.card.FIX{border-left-color:#e09b2b}.card.DROP{border-left-color:#d64545}
  .idx{font-weight:700;color:#2b6de0;margin-right:6px}
  .tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:10px;background:#eef0f3;color:#666;margin-left:6px}
  .warn{font-size:12.5px;color:#a35b00;background:#fff7e8;border-radius:6px;padding:4px 8px;margin:6px 0}
  .hits{font-size:13px;background:#f3f7f3;border-radius:6px;padding:6px 10px;margin:6px 0;color:#2c4a2c}
  .hits div{margin:2px 0}
  .hits .ln{color:#7a9a7a;margin-right:6px}
  label.fld{display:block;font-size:12px;color:#888;margin-top:8px}
  textarea{width:100%;box-sizing:border-box;font:13.5px/1.5 inherit;border:1px solid #d6d9de;
           border-radius:6px;padding:6px 8px;resize:vertical;background:#fcfcfd}
  textarea.edited{border-color:#e09b2b;background:#fffdf5}
  .dec{margin-top:10px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
  .dec label{cursor:pointer;font-size:14px}
  .dec input[type=radio]{accent-color:#2b6de0}
  .note{flex:1;min-width:220px;border:1px solid #d6d9de;border-radius:6px;padding:5px 8px;font:13px inherit}
  #stat{font-size:13px;color:#666}
</style>
</head>
<body>
<div id="bar">
  <b>评测集人工审核</b><span id="stat"></span>
  <span style="flex:1"></span>
  <button class="btn" data-f="all">全部</button>
  <button class="btn" data-f="todo">未审</button>
  <button class="btn" data-f="risk">有风险标记</button>
  <button class="btn" id="expLog">导出审核记录</button>
  <button class="btn primary" id="expFinal">导出定稿 JSON</button>
</div>
<div id="wrap"></div>
<script>
const DATA = __DATA__;
const KEY = "scqa_eval_review_v1";
let st = {};
try { st = JSON.parse(localStorage.getItem(KEY)) || {}; } catch(e) { st = {}; }
let filter = "all";

function save(){ localStorage.setItem(KEY, JSON.stringify(st)); paintStat(); }
function s(i){ return st[i] || (st[i] = {}); }
function esc(t){ return t.replace(/&/g,"&amp;").replace(/</g,"&lt;"); }
function isRisk(r){ return r.cross.length || r.dups.length || r.no_anchor || r.missing_doc; }

function render(){
  const wrap = document.getElementById("wrap");
  wrap.innerHTML = "";
  let curFile = null;
  for (const r of DATA){
    const d = st[r.idx] || {};
    if (filter === "todo" && d.dec) continue;
    if (filter === "risk" && !isRisk(r)) continue;
    if (r.file !== curFile){
      curFile = r.file;
      const h = document.createElement("h2");
      h.textContent = curFile + (r.missing_doc ? "（⚠ 源文档缺失）" : "");
      wrap.appendChild(h);
    }
    const c = document.createElement("div");
    c.className = "card " + (d.dec || "");
    let warns = "";
    if (r.cross.length) warns += `<div class="warn">⚠ 关键事实也在：${esc(r.cross.slice(0,6).join("、"))}${r.cross.length>6?"…":""}</div>`;
    if (r.dups.length)  warns += `<div class="warn">⚠ 与题 [${r.dups.join("] [")}] 高度相似</div>`;
    if (r.no_anchor)    warns += `<div class="warn">⚠ 无数字锚点，请对照全文</div>`;
    let hits = "";
    if (r.hits.length){
      hits = `<div class="hits">${r.hits.map(h=>`<div><span class="ln">L${h[0]}</span>${esc(h[1])}</div>`).join("")}` +
             (r.more_hits ? `<div class="ln">…另有 ${r.more_hits} 行命中</div>` : "") + `</div>`;
    } else {
      hits = `<div class="hits" style="background:#fdf3f3;color:#8a3b3b">无命中行——请打开原文逐项核对</div>`;
    }
    c.innerHTML = `
      <div><span class="idx">[${r.idx}]</span><span class="tag">${r.type}</span></div>
      ${warns}
      <label class="fld">题干（FIX 时可直接改）</label>
      <textarea rows="1" data-i="${r.idx}" data-k="q">${esc(d.q ?? r.question)}</textarea>
      <label class="fld">reference_answer（FIX 时可直接改）</label>
      <textarea rows="2" data-i="${r.idx}" data-k="ref">${esc(d.ref ?? r.ref)}</textarea>
      <label class="fld">原文命中行</label>${hits}
      <div class="dec">
        <label><input type="radio" name="r${r.idx}" value="PASS" ${d.dec==="PASS"?"checked":""}> PASS</label>
        <label><input type="radio" name="r${r.idx}" value="FIX"  ${d.dec==="FIX" ?"checked":""}> FIX</label>
        <label><input type="radio" name="r${r.idx}" value="DROP" ${d.dec==="DROP"?"checked":""}> DROP</label>
        <input class="note" placeholder="备注" data-i="${r.idx}" data-k="note" value="${esc(d.note || "")}">
      </div>`;
    c.querySelectorAll("input[type=radio]").forEach(el => el.onchange = () => {
      s(r.idx).dec = el.value; c.className = "card " + el.value; save();
    });
    c.querySelectorAll("textarea,.note").forEach(el => el.oninput = () => {
      const k = el.dataset.k, orig = k === "q" ? r.question : r.ref;
      s(r.idx)[k] = el.value;
      if (k !== "note") el.classList.toggle("edited", el.value !== orig);
      save();
    });
    wrap.appendChild(c);
  }
}

function paintStat(){
  const n = DATA.length;
  let p=0,f=0,dr=0;
  for (const r of DATA){ const d=st[r.idx]||{};
    if(d.dec==="PASS")p++; else if(d.dec==="FIX")f++; else if(d.dec==="DROP")dr++; }
  document.getElementById("stat").textContent =
    `已审 ${p+f+dr}/${n}（PASS ${p} / FIX ${f} / DROP ${dr}）`;
}

function download(name, obj){
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 2)], {type:"application/json"}));
  a.download = name; a.click(); URL.revokeObjectURL(a.href);
}

document.getElementById("expFinal").onclick = () => {
  const todo = DATA.filter(r => !(st[r.idx]||{}).dec).length;
  if (todo && !confirm(`还有 ${todo} 条未审（将按原样保留），确定导出？`)) return;
  const out = [];
  for (const r of DATA){
    const d = st[r.idx] || {};
    if (d.dec === "DROP") continue;
    out.push({ question: (d.q ?? r.question).trim(),
               reference_answer: (d.ref ?? r.ref).trim(),
               source_file: r.file, type: r.type });
  }
  download("eval_set_clean_reviewed.json", out);
};

document.getElementById("expLog").onclick = () => {
  const log = DATA.map(r => { const d = st[r.idx]||{}; return {
    idx: r.idx, source_file: r.file, decision: d.dec || "TODO",
    edited: (d.q !== undefined && d.q !== r.question) || (d.ref !== undefined && d.ref !== r.ref),
    note: d.note || "" }; });
  download("eval_review_log.json", log);
};

document.querySelectorAll("#bar .btn[data-f]").forEach(b => b.onclick = () => {
  filter = b.dataset.f;
  document.querySelectorAll("#bar .btn[data-f]").forEach(x => x.classList.toggle("on", x === b));
  render();
});

render(); paintStat();
document.querySelector('#bar .btn[data-f="all"]').classList.add("on");
</script>
</body>
</html>
"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).replace("％", "%")


def extract_tokens(ref: str):
    """从 reference_answer 提取数字锚点 token（规范化后去重）。"""
    toks = []
    for m in TOKEN_RE.finditer(ref):
        t = _norm(m.group())
        if len(t) >= 2 and t not in toks:
            toks.append(t)
    return toks


def fallback_keywords(ref: str):
    """无数字锚点时退化用长中文词做行匹配。"""
    words = sorted(set(re.findall(r"[\u4e00-\u9fa5]{4,}", ref)), key=len, reverse=True)
    return words[:4]


def match_lines(doc_lines, tokens):
    """返回源文档中命中任一 token 的行 [(行号, 原文), ...]。"""
    hits = []
    for ln, raw in enumerate(doc_lines, 1):
        line_n = _norm(raw)
        if not line_n:
            continue
        if any(t in line_n for t in tokens):
            hits.append((ln, raw.strip()))
    return hits


def bigrams(s: str):
    s = _norm(re.sub(r"[？?，。、：:（）()《》“”\"]", "", s))
    return {s[i:i + 2] for i in range(len(s) - 1)}


def main():
    items = json.load(open(IN_PATH, encoding="utf-8"))
    print(f"评测集 {len(items)} 条 <- {IN_PATH}")

    # 缓存评测集涉及的所有源文档（行列表 + 规范化全文）
    doc_lines, doc_norm = {}, {}
    for fn in {it["source_file"] for it in items}:
        p = Path(KNOWLEDGE_DIR) / fn
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        doc_lines[fn] = text.splitlines()
        doc_norm[fn] = _norm(text)

    # 近重复题：问题字符 bigram Jaccard
    grams = [bigrams(it["question"]) for it in items]
    similar = defaultdict(list)
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            inter = len(grams[i] & grams[j])
            union = len(grams[i] | grams[j]) or 1
            if inter / union >= 0.55:
                similar[i].append(j)
                similar[j].append(i)

    by_file = defaultdict(list)
    for idx, it in enumerate(items):
        by_file[it["source_file"]].append(idx)

    n_no_anchor, n_cross, n_dup = 0, 0, len(similar)
    records = []  # 供 HTML 交互页使用的结构化数据（与 md 同序）
    out = []
    out.append("# 评测集人工审核清单（eval_set_clean.json）\n")
    out.append(f"共 {len(items)} 条，按 source_file 分组。每条做三层核对后勾选结论：\n")
    out.append("1. **逐数字 grounding**：对照下方「原文命中行」，确认 reference 的每个数字/步骤/术语与原文一致；")
    out.append("2. **题目锚定性**：若出现「⚠ 关键事实也在其他文档」，判断题干是否足以锁定本文档（不足则 FIX 加锚定）；")
    out.append("3. **跨文档冲突**：同主题文档事实矛盾的题（如两篇物料编码），FIX 加锚定或 DROP 二选一。\n")
    out.append("结论：`PASS` 原样保留 / `FIX` 改 reference 或题干（写明改法）/ `DROP` 删除。")
    out.append("审核完直接按结论修改 eval_set_clean.json（条目编号 = json 数组下标），再用 eval_repeat.py ×3 重跑基线。\n")
    out.append("---\n")

    for fn in sorted(by_file):
        exists = bool(doc_lines[fn])
        out.append(f"\n## {fn}" + ("" if exists else "（⚠ 源文档缺失）"))
        for idx in by_file[fn]:
            it = items[idx]
            tokens = extract_tokens(it["reference_answer"])
            use_fallback = not tokens
            if use_fallback:
                tokens = fallback_keywords(it["reference_answer"])
                n_no_anchor += 1
            hits = match_lines(doc_lines[fn], tokens) if exists else []

            out.append(f"\n### [{idx}] {it['question']}")
            out.append(f"- type: `{it['type']}`")
            out.append(f"- **Ref**: {it['reference_answer']}")
            if use_fallback:
                out.append("- （无数字锚点，以下按长关键词匹配，请人工对照全文）")
            if hits:
                out.append("- **原文命中行**：")
                for ln, line in hits[:MAX_LINES_PER_ITEM]:
                    line = line if len(line) <= 160 else line[:160] + "…"
                    out.append(f"  > L{ln}: {line}")
                if len(hits) > MAX_LINES_PER_ITEM:
                    out.append(f"  > …另有 {len(hits) - MAX_LINES_PER_ITEM} 行命中，必要时打开原文")
            else:
                out.append("- **原文命中行**：（无命中——锚点措辞与原文不同或事实缺失，请打开原文逐项核对）")

            # 跨文档：用特异 token（含 %/≥≤± 或长度>=4）搜其他源文档
            strong = [t for t in tokens if ("%" in t or t[0] in "≥≤±" or len(t) >= 4)]
            others = []
            if strong and exists:
                for ofn, ntext in doc_norm.items():
                    if ofn == fn or not ntext:
                        continue
                    if any(t in ntext for t in strong):
                        others.append(ofn)
            if others:
                n_cross += 1
                shown = ", ".join(others[:6]) + ("…" if len(others) > 6 else "")
                out.append(f"- ⚠ 关键事实也在其他文档：{shown}")
            if idx in similar:
                dups = ", ".join(f"[{j}]" for j in sorted(similar[idx]))
                out.append(f"- ⚠ 与题 {dups} 高度相似（考虑合并/删减）")
            out.append("- 结论：`[ ] PASS`　`[ ] FIX`　`[ ] DROP`　备注：")

            records.append({
                "idx": idx,
                "file": fn,
                "type": it["type"],
                "question": it["question"],
                "ref": it["reference_answer"],
                "hits": hits[:MAX_LINES_PER_ITEM],
                "more_hits": max(0, len(hits) - MAX_LINES_PER_ITEM),
                "cross": others,
                "dups": sorted(similar.get(idx, [])),
                "no_anchor": use_fallback,
                "missing_doc": not exists,
            })

    out.append("\n---\n")
    out.append(f"统计：共 {len(items)} 条；关键事实跨文档出现 {n_cross} 条；"
               f"近重复题 {n_dup} 条；无数字锚点 {n_no_anchor} 条。\n")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(out))

    data_json = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(HTML_TMPL.replace("__DATA__", data_json))

    print(f"跨文档风险 {n_cross} 条 / 近重复 {n_dup} 条 / 无数字锚点 {n_no_anchor} 条")
    print(f"只读清单 -> {OUT_PATH}")
    print(f"交互审核页 -> {OUT_HTML}（浏览器打开，审完点「导出定稿 JSON」）")


if __name__ == "__main__":
    main()
