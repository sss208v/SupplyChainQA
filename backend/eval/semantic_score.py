# -*- coding: utf-8 -*-
"""answer-vs-reference 语义相似度对照。

目的：判定 Qwen3 升级后 proxy（关键词重叠）指标下降，是"更简洁/换词"的度量假象，
还是真实的语义回归。用同一 embedding 模型（bge-base-zh-v1.5）分别计算两模型答案
与参考答案的余弦相似度——语义指标对措辞差异公平，可与关键词 proxy 交叉验证。

用法：
    cd backend
    venv\\Scripts\\python.exe eval\\semantic_score.py

输出：eval/semantic_compare.json + 控制台摘要
"""
import os
import sys
import json
import statistics as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from app.core.rag_engine import rag_engine

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))


def _cos(a, b) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    n = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / n) if n else 0.0


def main():
    raw25 = json.load(open(os.path.join(EVAL_DIR, "raw_qwen25.json"), encoding="utf-8"))
    raw3 = json.load(open(os.path.join(EVAL_DIR, "raw_qwen3.json"), encoding="utf-8"))

    ref = {r["user_input"]: str(r.get("reference", "")) for r in raw25}
    a25 = {r["user_input"]: str(r.get("response", "")) for r in raw25}
    a3 = {r["user_input"]: str(r.get("response", "")) for r in raw3}

    print("Loading embedding model (bge-base-zh-v1.5)...")
    rag_engine.embedding.init()
    emb = rag_engine.embedding

    rows = []
    for q, r_ref in ref.items():
        if not r_ref or q not in a3 or q not in a25:
            continue
        ev = emb.embed_query(r_ref)
        c25 = _cos(emb.embed_query(a25[q]), ev)
        c3 = _cos(emb.embed_query(a3[q]), ev)
        rows.append({
            "q": q,
            "sem_qwen25": round(c25, 4),
            "sem_qwen3": round(c3, 4),
            "delta": round(c3 - c25, 4),
            "len25": len(a25[q]),
            "len3": len(a3[q]),
        })

    if not rows:
        print("No comparable rows.")
        return

    avg25 = round(st.mean(x["sem_qwen25"] for x in rows), 4)
    avg3 = round(st.mean(x["sem_qwen3"] for x in rows), 4)
    # 真实回归的候选：语义显著下降（delta < -0.05）
    real_drops = [x for x in rows if x["delta"] < -0.05]
    rows_sorted = sorted(rows, key=lambda x: x["delta"])

    out = {
        "n": len(rows),
        "avg_sem_qwen25": avg25,
        "avg_sem_qwen3": avg3,
        "avg_delta_qwen3_minus_qwen25": round(avg3 - avg25, 4),
        "real_drop_count(delta<-0.05)": len(real_drops),
        "worst_for_qwen3": rows_sorted[:8],
        "best_for_qwen3": rows_sorted[-5:],
    }
    with open(os.path.join(EVAL_DIR, "semantic_compare.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print(f"语义相似度(answer vs reference, bge-base-zh-v1.5), n={len(rows)}")
    print(f"  Qwen2.5-7B avg = {avg25:.4f}")
    print(f"  Qwen3-14B  avg = {avg3:.4f}")
    print(f"  Δ(Qwen3-Qwen2.5) = {avg3 - avg25:+.4f}")
    print(f"  语义显著下降题数(Δ<-0.05) = {len(real_drops)} / {len(rows)}")
    print("  Qwen3 语义最差的几题(可能真实分歧):")
    for x in rows_sorted[:6]:
        print(f"    Δ={x['delta']:+.3f} sem25={x['sem_qwen25']:.3f} sem3={x['sem_qwen3']:.3f} | {x['q'][:34]}")
    print(f"\nSaved -> {os.path.join(EVAL_DIR, 'semantic_compare.json')}")


if __name__ == "__main__":
    main()
