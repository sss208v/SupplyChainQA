# -*- coding: utf-8 -*-
"""对 build_eval_set.py 生成的候选 QA 做【DeepSeek 独立 grounding 核验】：
逐条读回其 source 文档，判定 reference_answer 是否被原文【完全且准确】支持，
只保留 supported=true 的，输出 eval_set_clean.json；被拒的连同理由写入 *_rejected.json。

这是"人工校验"前的自动化前置：用第二次独立 LLM 判定压掉幻觉/夸大，降低人工抽检量。
仍建议人工对定稿集抽检（真实性优先，不全盘信任 LLM）。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\curate_eval_set.py --in eval\\generated_qa_candidates.json
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.build_eval_set import _judge_cfg, KNOWLEDGE_DIR, EVAL_DIR

VERIFY_PROMPT = """下面是一篇文档，以及基于它生成的一个问答对。请判断 reference_answer 是否【完全且准确地】被文档内容支持：所有数字、步骤、术语都能在文档中找到，无编造、无夸大、无引入文档外知识。

只输出 JSON，不要解释：{{"supported": true 或 false, "reason": "简短理由"}}

文档《{filename}》正文：
{content}

问题：{question}
reference_answer：{reference_answer}
"""


def _parse_obj(text: str):
    m = re.search(r"\{.*\}", text.strip(), re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="DeepSeek 独立核验候选 QA 的 grounding")
    ap.add_argument("--in", dest="in_path", default=os.path.join(EVAL_DIR, "generated_qa_candidates.json"))
    ap.add_argument("--out", default=os.path.join(EVAL_DIR, "eval_set_clean.json"))
    ap.add_argument("--max-chars", type=int, default=6000)
    args = ap.parse_args()

    base, model, key = _judge_cfg()
    if not (base and model and key):
        raise SystemExit("DeepSeek 配置缺失（检查 backend/.env RAGAS_JUDGE_*）")
    is_remote = ("localhost" not in base) and ("127.0.0.1" not in base)

    from openai import OpenAI
    client = OpenAI(api_key=key, base_url=base)

    candidates = json.load(open(args.in_path, encoding="utf-8"))
    print(f"待核验候选 {len(candidates)} 条；核验模型={model}")

    # 缓存文档正文，避免重复读盘
    _doc_cache = {}

    def _doc(fn):
        if fn not in _doc_cache:
            p = Path(KNOWLEDGE_DIR) / fn
            _doc_cache[fn] = (p.read_text(encoding="utf-8")[:args.max_chars] if p.exists() else "")
        return _doc_cache[fn]

    kept, rejected = [], []
    for i, c in enumerate(candidates, 1):
        content = _doc(c["source_file"])
        if not content:
            rejected.append({**c, "reason": "source 文档缺失"})
            continue
        prompt = VERIFY_PROMPT.format(filename=c["source_file"], content=content,
                                      question=c["question"], reference_answer=c["reference_answer"])
        try:
            kw = dict(model=model, temperature=0.0,
                      messages=[{"role": "user", "content": prompt}])
            if is_remote:
                kw["extra_body"] = {"thinking": {"type": "disabled"}}
            resp = client.chat.completions.create(**kw)
            verdict = _parse_obj(resp.choices[0].message.content) or {}
        except Exception as e:
            rejected.append({**c, "reason": f"核验调用失败: {type(e).__name__}"})
            continue

        if verdict.get("supported") is True:
            kept.append(c)
        else:
            rejected.append({**c, "reason": verdict.get("reason", "未通过核验")})
        if i % 20 == 0:
            print(f"  进度 {i}/{len(candidates)}：保留 {len(kept)} / 拒 {len(rejected)}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)
    rej_path = args.out.replace(".json", "_rejected.json")
    with open(rej_path, "w", encoding="utf-8") as f:
        json.dump(rejected, f, ensure_ascii=False, indent=2)

    # 覆盖度统计
    from collections import Counter
    by_type = Counter(c["type"] for c in kept)
    print(f"\n核验通过 {len(kept)} 条 / 拒 {len(rejected)} 条")
    print(f"类型分布: {dict(by_type)}")
    print(f"定稿 -> {args.out}")
    print(f"被拒(含理由) -> {rej_path}")
    print("[NOTE] 仍建议人工抽检定稿集，真实性优先。")


if __name__ == "__main__":
    main()
