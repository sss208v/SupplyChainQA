"""
SmartQA Pro - RAGAS 评估脚本（精简版）
直接运行，结果输出到文件
"""
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


async def collect_data():
    """收集 RAG 评估数据"""
    # 确保 Milvus 连接
    milvus_manager.connect()

    eval_data = []
    print(f"Collecting eval data for {len(TEST_QA_PAIRS)} questions...")

    for i, pair in enumerate(TEST_QA_PAIRS):
        question = pair["question"]
        reference = pair["reference_answer"]

        print(f"\n[{i+1}/{len(TEST_QA_PAIRS)}] Q: {question}")

        try:
            start = time.time()

            # 获取检索上下文
            query_type = rag_agent._classify_query(question)
            search_queries = await rag_agent._prepare_queries(question, query_type)

            all_results = []
            for sq in search_queries:
                result = rag_engine.search(sq, top_k=settings.RERANK_TOP_K)
                all_results.extend(result.get("results", []))

            # 去重
            seen = set()
            unique_results = []
            for r in all_results:
                chunk_id = r.get("chunk_id", "")
                if chunk_id not in seen:
                    seen.add(chunk_id)
                    unique_results.append(r)

            retrieved_contexts = [r.get("content", "") for r in unique_results]

            # 获取 RAG 回答
            rag_result = await rag_agent.answer(query=question, session_id=None)
            response = rag_result["answer"]

            elapsed = time.time() - start
            print(f"  A: {response[:60]}... ({elapsed:.1f}s, {len(retrieved_contexts)} contexts)")

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
    """运行 RAGAS 评估"""
    from ragas import evaluate
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
    from ragas.metrics import (
        Faithfulness,
        AnswerRelevance,
        ContextPrecision,
        ContextRecall,
    )
    from langchain_openai import ChatOpenAI

    # Judge LLM = 本地 llama.cpp
    judge_llm = ChatOpenAI(
        model="qwen2.5-7b-instruct-q3_k_m.gguf",
        base_url="http://localhost:8081/v1",
        api_key="sk-no-key-needed",
        temperature=0.0,
    )

    # Embeddings = SmartQA 已有的 BGE
    judge_embeddings = rag_engine._embedding_model

    # 构建数据集
    samples = []
    for item in eval_data:
        samples.append(SingleTurnSample(
            user_input=item["user_input"],
            response=item["response"],
            reference=item["reference"],
            retrieved_contexts=item["retrieved_contexts"],
        ))

    dataset = EvaluationDataset(samples=samples)

    metrics = [
        Faithfulness(),
        AnswerRelevance(),
        ContextPrecision(),
        ContextRecall(),
    ]

    print(f"\nRunning RAGAS evaluation on {len(samples)} samples...")
    print(f"Metrics: {[m.__class__.__name__ for m in metrics]}")

    result = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=judge_llm,
        embeddings=judge_embeddings,
    )

    return result


def generate_report(result, eval_data):
    """生成 Markdown 报告"""
    # 提取分数
    scores = {}
    try:
        df = result.to_pandas()
        metric_cols = [c for c in df.columns if c not in ['user_input', 'response', 'reference', 'retrieved_contexts']]
        for col in metric_cols:
            val = df[col]
            # 过滤 NaN
            valid = val.dropna()
            if len(valid) > 0:
                scores[col] = valid.mean()
            else:
                scores[col] = 0.0
    except Exception as e:
        print(f"Warning: could not extract scores from pandas: {e}")

    lines = [
        "# SmartQA Pro - RAGAS 评估报告",
        "",
        f"**评估时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**评估数据量**: {len(eval_data)} 条",
        f"**RAGAS 版本**: 0.4.3",
        f"**被评估LLM**: Qwen2.5-7B-Instruct (Q3_K_M, llama.cpp本地推理)",
        f"**Judge LLM**: 同被评估LLM",
        f"**知识库**: 企业IT支持知识库 (9 chunks)",
        "",
        "---",
        "",
        "## 一、总体评分",
        "",
    ]

    if scores:
        lines.append("| 指标 | 英文名 | 得分 | 等级 |")
        lines.append("|------|--------|------|------|")
        names = {
            'faithfulness': ('忠实度', 'Faithfulness'),
            'answer_relevance': ('答案相关性', 'AnswerRelevance'),
            'context_precision': ('上下文精确度', 'ContextPrecision'),
            'context_recall': ('上下文召回率', 'ContextRecall'),
        }
        for key, val in scores.items():
            cn, en = names.get(key, (key, key))
            v = float(val)
            grade = "优秀" if v >= 0.8 else "良好" if v >= 0.6 else "一般" if v >= 0.4 else "较差"
            lines.append(f"| {cn} | {en} | {v:.4f} | {grade} |")
    else:
        lines.append("*(无法提取分数)*")
        lines.append(f"```")
        lines.append(str(result)[:1000])
        lines.append(f"```")

    # 逐题
    lines.extend(["", "---", "", "## 二、逐题详情", ""])
    for i, item in enumerate(eval_data):
        lines.append(f"### Q{i+1}: {item['user_input']}")
        lines.append(f"- **标准答案**: {item['reference'][:200]}")
        lines.append(f"- **系统回答**: {item['response'][:200]}...")
        lines.append(f"- **检索上下文数**: {len(item['retrieved_contexts'])}")
        lines.append("")

    # 指标说明
    lines.extend([
        "---", "",
        "## 三、指标说明", "",
        "| 指标 | 含义 |",
        "|------|------|",
        "| 忠实度 Faithfulness | 回答是否只基于检索上下文，不编造 |",
        "| 答案相关性 AnswerRelevance | 回答是否切题 |",
        "| 上下文精确度 ContextPrecision | 检索结果中相关内容的排名 |",
        "| 上下文召回率 ContextRecall | 标准答案所需信息是否被检索到 |",
    ])

    return "\n".join(lines)


async def main():
    eval_dir = os.path.dirname(os.path.abspath(__file__))

    # Step 1: 收集数据
    print("=" * 60)
    print("Step 1: Collecting evaluation data...")
    eval_data = await collect_data()

    raw_path = os.path.join(eval_dir, "eval_raw_data.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(eval_data, f, ensure_ascii=False, indent=2)
    print(f"\nRaw data saved: {raw_path}")

    # Step 2: RAGAS 评估
    print("\n" + "=" * 60)
    print("Step 2: Running RAGAS evaluation...")
    try:
        result = run_ragas(eval_data)
        print(f"\nRAGAS result: {result}")

        # Step 3: 生成报告
        print("\n" + "=" * 60)
        print("Step 3: Generating report...")
        report = generate_report(result, eval_data)
        report_path = os.path.join(eval_dir, f"eval_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report saved: {report_path}")

        # Save result
        try:
            df = result.to_pandas()
            result_path = os.path.join(eval_dir, "eval_ragas_result.csv")
            df.to_csv(result_path, index=False, encoding="utf-8-sig")
            print(f"Result CSV saved: {result_path}")
        except Exception as e:
            print(f"Warning: CSV save failed: {e}")

    except Exception as e:
        print(f"\nRAGAS evaluation FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

        # Still save raw data
        print("\nRaw eval data has been saved. Fix the error and re-run RAGAS separately.")

    print("\n" + "=" * 60)
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
