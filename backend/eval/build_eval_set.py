# -*- coding: utf-8 -*-
"""从 knowledge/*.md 用 DeepSeek 生成【事实型】QA 候选（question + 简洁 reference_answer + 标签）。

每篇文档严格只基于其正文生成，供人工抽检后定稿为干净评测集。
生成模型走 backend/.env 的 RAGAS_JUDGE_*（DeepSeek 官方，非思考模式）。
本脚本只调 DeepSeek API + 读本地 md，不需要 llama/Milvus。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\build_eval_set.py --per-doc 1 --limit 3   # 冒烟(前3篇)
  venv\\Scripts\\python.exe eval\\build_eval_set.py --per-doc 1              # 全量
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(EVAL_DIR)
_REPO = os.path.dirname(_BACKEND)
KNOWLEDGE_DIR = os.path.join(_REPO, "knowledge")
_ENV_PATH = os.path.join(_BACKEND, ".env")

_DEPTS = ["admin", "finance", "logistics", "production", "purchase", "quality", "warehouse"]

GEN_PROMPT = """你是评测数据构建助手。请【仅根据下面这篇文档的内容】，生成 {n} 个"事实型"问答对，用于测试知识库问答系统。

要求：
- 问题是用户可能真实问的、且答案能在本文档中明确找到的具体问题（如流程步骤/标准/公式/权限/周期/分类）。
- reference_answer 必须【严格来自文档原文事实】，简洁准确，覆盖关键数字/步骤；绝不编造文档中没有的信息。
- 不要问过于宽泛、或需要跨多篇文档才能回答的问题。
- 严格只输出 JSON 数组，不要任何解释：[{{"question": "...", "reference_answer": "..."}}]

文档《{filename}》正文：
{content}
"""


def _judge_cfg():
    """手动解析 backend/.env 的 DeepSeek judge 配置（不硬编码、不入日志）。"""
    cfg = {}
    if os.path.exists(_ENV_PATH):
        for line in open(_ENV_PATH, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    base = os.getenv("RAGAS_JUDGE_BASE_URL") or cfg.get("RAGAS_JUDGE_BASE_URL")
    model = os.getenv("RAGAS_JUDGE_MODEL") or cfg.get("RAGAS_JUDGE_MODEL")
    key = (os.getenv("RAGAS_JUDGE_API_KEY") or cfg.get("RAGAS_JUDGE_API_KEY")
           or cfg.get("SENSENOVA_API_KEY"))
    return base, model, key


def _doc_type(filename: str) -> str:
    m = re.match(r"SC-([a-z]+)-", filename)
    if m and m.group(1) in _DEPTS:
        return m.group(1)
    return "topic"


def _clean_json_array(text: str):
    text = text.strip()
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group())
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser(description="从 knowledge/*.md 生成事实型 QA 候选（DeepSeek）")
    ap.add_argument("--per-doc", type=int, default=1, help="每篇文档生成几条 QA")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 篇（0=全部，冒烟用）")
    ap.add_argument("--max-chars", type=int, default=6000, help="每篇正文截断字符数（控成本）")
    ap.add_argument("--out", default=os.path.join(EVAL_DIR, "generated_qa_candidates.json"))
    args = ap.parse_args()

    base, model, key = _judge_cfg()
    if not (base and model and key):
        raise SystemExit(f"DeepSeek 配置缺失: base={base} model={model} key_set={bool(key)}（检查 backend/.env RAGAS_JUDGE_*）")
    is_remote = ("localhost" not in base) and ("127.0.0.1" not in base)

    from openai import OpenAI
    client = OpenAI(api_key=key, base_url=base)

    docs = sorted(Path(KNOWLEDGE_DIR).glob("*.md"))
    if args.limit > 0:
        docs = docs[:args.limit]
    print(f"知识文档 {len(docs)} 篇 <- {KNOWLEDGE_DIR}；生成模型={model}")

    candidates = []
    seen_q = set()
    for i, md in enumerate(docs, 1):
        content = md.read_text(encoding="utf-8").strip()
        if len(content) < 50:
            continue
        content = content[:args.max_chars]
        prompt = GEN_PROMPT.format(n=args.per_doc, filename=md.name, content=content)
        try:
            kw = dict(model=model, temperature=0.3,
                      messages=[{"role": "user", "content": prompt}])
            if is_remote:
                kw["extra_body"] = {"thinking": {"type": "disabled"}}
            resp = client.chat.completions.create(**kw)
            items = _clean_json_array(resp.choices[0].message.content)
        except Exception as e:
            print(f"  [{i}/{len(docs)}] {md.name} 生成失败: {type(e).__name__}: {e}")
            continue

        kept = 0
        for it in items:
            q = (it.get("question") or "").strip()
            ref = (it.get("reference_answer") or "").strip()
            # 自动初筛：长度 + 去重
            if len(q) < 6 or len(ref) < 20:
                continue
            qkey = re.sub(r"\s+", "", q)
            if qkey in seen_q:
                continue
            seen_q.add(qkey)
            candidates.append({
                "question": q,
                "reference_answer": ref,
                "source_file": md.name,
                "type": _doc_type(md.name),
            })
            kept += 1
        print(f"  [{i}/{len(docs)}] {md.name} ({_doc_type(md.name)}) -> {kept} 条")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    print(f"\n生成候选 {len(candidates)} 条 -> {args.out}")
    print("[NOTE] 下一步：人工抽检 reference_answer 与原文一致性，剔除幻觉/不准，定稿为 eval_set_clean.json")


if __name__ == "__main__":
    main()
