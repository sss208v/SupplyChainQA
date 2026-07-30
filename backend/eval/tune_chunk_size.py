# -*- coding: utf-8 -*-
"""chunk_size 参数调优实验 —— 每个 chunk_size 重建知识库（drop Milvus + 重新切块/embed/入库），
用【官方 RAGAS】度量。属"重实验"：每档都要在 CPU 上重新 embed 全部 chunk，耗时长。

【控制变量】固定生成模型 + judge + 题集 + 检索策略，仅 chunk_size/overlap 变化。
【复刻生产切块】用 app.api.knowledge._chunk_text（生产语义切块器），非 ingest_pdfs 的 1000/200。
【复用】rag_engine.index_document(embed+Milvus+BM25) + collect_data + run_ragas_eval(官方 RAGAS)。

⚠️ 本脚本会 DROP 并重建 Milvus 集合；跑完集合停留在最后一档，
   结束会按 --restore 值重灌一次恢复基准（默认 256）。请在服务空闲时运行。

用法：
  cd backend
  venv\\Scripts\\python.exe eval\\tune_chunk_size.py --values 256,512 --overlap-ratio 0.15 --limit 20
  venv\\Scripts\\python.exe eval\\tune_chunk_size.py --values 256,512 --limit 3   # 冒烟
"""
import argparse
import asyncio
import json
import os
import sys
import time
import hashlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.run_comprehensive_ragas import collect_data, run_ragas_eval, _questions, LLAMA_MODEL
from app.config import get_settings
from app.core.milvus_client import milvus_manager
from app.core.rag_engine import rag_engine
from app.api.knowledge import _chunk_text

settings = get_settings()
EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
# 知识库源文档目录（仓库根 knowledge/）
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KNOWLEDGE_DIR = os.path.join(_REPO, "knowledge")
# 广义权限组：保证评测（admin 视角）能检索到全部重灌 chunk，避免 RBAC 过滤影响 chunk_size 对照
_SG = ["admin", "purchase", "warehouse", "finance", "quality", "logistics", "production"]


def _fmt(v):
    return f"{v:.3f}" if isinstance(v, (int, float)) else "NaN"


def _reset_milvus():
    """drop + 重建空集合（chunk_size 不改变 schema，仅需清空重灌）。"""
    from pymilvus import utility
    milvus_manager.connect()
    name = settings.MILVUS_COLLECTION
    if utility.has_collection(name):
        utility.drop_collection(name)
    milvus_manager.collection = None
    milvus_manager.create_collection()


def _reingest(knowledge_dir, chunk_size, overlap):
    """按指定 chunk_size 重新切块并全量入库（embed + Milvus + BM25）。"""
    settings.CHUNK_SIZE = chunk_size
    settings.CHUNK_OVERLAP = overlap
    _reset_milvus()
    docs = sorted(Path(knowledge_dir).glob("*.md"))
    total = 0
    for md in docs:
        text = md.read_text(encoding="utf-8")
        chunks = _chunk_text(text=text, chunk_size=chunk_size, chunk_overlap=overlap)
        if not chunks:
            continue
        doc_id = hashlib.md5(md.name.encode()).hexdigest()[:12]
        records = [{
            "chunk_id": f"{doc_id}_chunk_{i}",
            "content": c["content"],
            "source": md.name,
            "page_num": 0,
            "section_title": c.get("section_title", ""),
        } for i, c in enumerate(chunks)]
        rag_engine.index_document(doc_id, records, security_group=_SG)
        total += len(records)
    return len(docs), total


async def run(args):
    sizes = [int(x) for x in args.values.split(",")]
    gen = args.gen_model or os.environ.get("RAGAS_GEN_MODEL") or LLAMA_MODEL
    judge = args.judge_model or os.environ.get("RAGAS_JUDGE_MODEL") or LLAMA_MODEL
    questions = _questions(args.limit)
    kd = args.knowledge_dir or KNOWLEDGE_DIR
    if not os.path.isdir(kd):
        raise SystemExit(f"知识库目录不存在: {kd}（用 --knowledge-dir 指定）")

    rows = []
    for size in sizes:
        overlap = int(size * args.overlap_ratio)
        print("=" * 60)
        print(f"[chunk_size={size}, overlap={overlap}] 重建知识库 <- {kd} ...")
        t0 = time.time()
        n_docs, n_chunks = _reingest(kd, size, overlap)
        ingest_sec = time.time() - t0
        print(f"  重灌完成: {n_docs} docs, {n_chunks} chunks, {ingest_sec:.0f}s")

        eval_data = await collect_data(questions, gen)
        ragas = run_ragas_eval(eval_data, judge)
        vals = [v for v in ragas.values() if v is not None]
        rows.append({
            "chunk_size": size, "overlap": overlap, "n_chunks": n_chunks,
            "ingest_seconds": round(ingest_sec, 1),
            "faithfulness": ragas.get("faithfulness"),
            "answer_relevancy": ragas.get("answer_relevancy"),
            "context_precision": ragas.get("context_precision"),
            "context_recall": ragas.get("context_recall"),
            "overall": round(sum(vals) / len(vals), 4) if vals else None,
        })

    if args.restore:
        print(f"\n恢复知识库至 chunk_size={args.restore} ...")
        _reingest(kd, args.restore, int(args.restore * args.overlap_ratio))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"judge_model": judge, "n": len(questions), "sweep": rows}, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 84)
    print(f"chunk_size 调优对照（官方 RAGAS, judge={judge}, n={len(questions)}）")
    print("%-11s %-8s %-9s %-7s %-7s %-7s %-7s %-8s" % (
        "chunk_size", "overlap", "n_chunks", "Faith", "AR", "CP", "CR", "Overall"))
    for r in rows:
        print("%-11d %-8d %-9d %-7s %-7s %-7s %-7s %-8s" % (
            r["chunk_size"], r["overlap"], r["n_chunks"],
            _fmt(r["faithfulness"]), _fmt(r["answer_relevancy"]),
            _fmt(r["context_precision"]), _fmt(r["context_recall"]), _fmt(r["overall"])))
    print(f"\nSaved -> {args.out}")


def main():
    ap = argparse.ArgumentParser(description="chunk_size 调优实验（官方 RAGAS，需重建知识库）")
    ap.add_argument("--values", default="256,512", help="逗号分隔的 chunk_size 候选")
    ap.add_argument("--overlap-ratio", type=float, default=0.15, help="overlap = chunk_size × 该比例")
    ap.add_argument("--limit", type=int, default=20, help="题量（0=全部）")
    ap.add_argument("--knowledge-dir", default=None, help="知识库源 .md 目录（默认 repo/knowledge）")
    ap.add_argument("--gen-model", default=None)
    ap.add_argument("--judge-model", default=None, help="默认取 .env RAGAS_JUDGE_MODEL")
    ap.add_argument("--restore", type=int, default=256, help="跑完恢复的 chunk_size（0=不恢复）")
    ap.add_argument("--out", default=os.path.join(EVAL_DIR, "chunk_size_sweep_result.json"))
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
