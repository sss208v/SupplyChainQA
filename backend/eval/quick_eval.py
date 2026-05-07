"""快速 RAGAS 评估 - 只取前5题测试"""
import asyncio
import json
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from test_dataset import TEST_QA_PAIRS
from app.core.milvus_client import milvus_manager
from app.core.rag_engine import rag_engine
from app.agents.rag import rag_agent
from app.config import get_settings

settings = get_settings()
# 只取前5题
TEST_SUBSET = TEST_QA_PAIRS[:5]


async def collect_data():
    milvus_manager.connect()
    eval_data = []
    print(f"Collecting data for {len(TEST_SUBSET)} questions...")

    for i, pair in enumerate(TEST_SUBSET):
        question = pair["question"]
        reference = pair["reference_answer"]
        print(f"\n[{i+1}/{len(TEST_SUBSET)}] Q: {question}")

        try:
            start = time.time()
            query_type = rag_agent._classify_query(question)
            search_queries = await rag_agent._prepare_queries(question, query_type)

            all_results = []
            for sq in search_queries:
                result = rag_engine.search(sq, top_k=settings.RERANK_TOP_K)
                all_results.extend(result.get("results", []))

            seen = set()
            unique_results = []
            for r in all_results:
                chunk_id = r.get("chunk_id", "")
                if chunk_id not in seen:
                    seen.add(chunk_id)
                    unique_results.append(r)

            retrieved_contexts = [r.get("content", "") for r in unique_results]
            rag_result = await rag_agent.answer(query=question, session_id=None)
            response = rag_result["answer"]

            elapsed = time.time() - start
            print(f"  A: {response[:80]}... ({elapsed:.1f}s, {len(retrieved_contexts)} ctx)")

            eval_data.append({
                "user_input": question,
                "response": response,
                "reference": reference,
                "retrieved_contexts": retrieved_contexts,
            })
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            eval_data.append({
                "user_input": question,
                "response": f"ERROR: {e}",
                "reference": reference,
                "retrieved_contexts": [],
            })

    return eval_data


def run_ragas(eval_data):
    from ragas import evaluate
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
    from ragas.metrics._faithfulness import Faithfulness
    from ragas.metrics._answer_relevance import AnswerRelevancy
    from ragas.metrics._context_precision import ContextPrecision
    from ragas.metrics._context_recall import ContextRecall
    from langchain_openai import ChatOpenAI

    judge_llm = ChatOpenAI(
        model="qwen2.5-7b-instruct-q3_k_m.gguf",
        base_url="http://localhost:8081/v1",
        api_key="sk-no-key-needed",
        temperature=0.0,
        max_tokens=512,
        max_retries=3,
    )

    # 获取 embeddings 模型（BGE-M3）
    rag_engine.embedding.init()  # 确保已初始化
    judge_embeddings = rag_engine.embedding._model

    samples = []
    for item in eval_data:
        samples.append(SingleTurnSample(
            user_input=item["user_input"],
            response=item["response"],
            reference=item["reference"],
            retrieved_contexts=item["retrieved_contexts"],
        ))

    dataset = EvaluationDataset(samples=samples)
    metrics = [Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()]

    print(f"\nRunning RAGAS on {len(samples)} samples...")
    result = evaluate(dataset=dataset, metrics=metrics, llm=judge_llm, embeddings=judge_embeddings)
    return result


async def main():
    eval_dir = os.path.dirname(os.path.abspath(__file__))

    print("=" * 50)
    print("Quick RAGAS Evaluation (5 questions)")
    print("=" * 50)

    # Step 1
    print("\nStep 1: Collect data...")
    eval_data = await collect_data()

    raw_path = os.path.join(eval_dir, "eval_raw_data_quick.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(eval_data, f, ensure_ascii=False, indent=2)
    print(f"\nRaw data saved: {raw_path}")

    # Step 2
    print("\nStep 2: Run RAGAS...")
    try:
        result = run_ragas(eval_data)
        print(f"\nResult: {result}")

        # Step 3: report
        try:
            df = result.to_pandas()
            metric_cols = [c for c in df.columns if c not in ['user_input', 'response', 'reference', 'retrieved_contexts']]
            lines = [
                "# SmartQA Pro - RAGAS Quick Eval Report",
                "",
                f"**Time**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"**Samples**: {len(eval_data)}",
                "",
                "## Overall Scores",
                "",
                "| Metric | Score |",
                "|--------|-------|",
            ]
            for col in metric_cols:
                v = df[col].dropna().mean()
                lines.append(f"| {col} | {v:.4f} |")

            lines.extend(["", "## Per-question Details", ""])
            for i, item in enumerate(eval_data):
                lines.append(f"### Q{i+1}: {item['user_input']}")
                lines.append(f"- Ref: {item['reference'][:150]}")
                lines.append(f"- RAG: {item['response'][:150]}...")
                lines.append("")

            report_path = os.path.join(eval_dir, "eval_report_quick.md")
            with open(report_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            print(f"\nReport saved: {report_path}")

            # CSV
            csv_path = os.path.join(eval_dir, "eval_result_quick.csv")
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            print(f"CSV saved: {csv_path}")

        except Exception as e2:
            print(f"Report generation error: {e2}")
            print(f"Raw result: {str(result)[:2000]}")

    except Exception as e:
        print(f"\nRAGAS FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 50)
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
