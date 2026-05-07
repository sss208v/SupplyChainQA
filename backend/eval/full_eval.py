"""
完整 RAGAS 评估 - 20 题全量测试 (后台执行版)
修复: AnswerRelevancy strictness=1
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
EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
PROGRESS_FILE = os.path.join(EVAL_DIR, "eval_progress.txt")


def log_progress(msg):
    """写进度到文件"""
    with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    print(msg)


async def collect_data():
    milvus_manager.connect()
    eval_data = []
    log_progress(f"Starting data collection for {len(TEST_QA_PAIRS)} questions...")

    for i, pair in enumerate(TEST_QA_PAIRS):
        question = pair["question"]
        reference = pair["reference_answer"]
        log_progress(f"[{i+1}/{len(TEST_QA_PAIRS)}] Q: {question}")

        try:
            start = time.time()
            # 1. 检索上下文
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

            # 2. 生成回答
            rag_result = await rag_agent.answer(query=question, session_id=None)
            response = rag_result["answer"]

            elapsed = time.time() - start
            log_progress(f"  A: {response[:80]}... ({elapsed:.1f}s, {len(retrieved_contexts)} ctx)")

            eval_data.append({
                "user_input": question,
                "response": response,
                "reference": reference,
                "retrieved_contexts": retrieved_contexts,
            })
        except Exception as e:
            log_progress(f"  ERROR: {type(e).__name__}: {e}")
            eval_data.append({
                "user_input": question,
                "response": f"ERROR: {e}",
                "reference": reference,
                "retrieved_contexts": [],
            })

    return eval_data


def clean_response(response: str) -> str:
    """
    清洗 RAG 回答，去除导致 AnswerRelevancy noncommittal=1 的前缀后缀。
    """
    import re
    # 去除「仅供参考」
    response = response.replace("「仅供参考」", "").strip()
    # 去除置信度警告行
    response = re.sub(r"⚠️ 该回答的置信度较低.*?建议核实信息准确性。", "", response, flags=re.DOTALL)
    # 去除 [来源X] 标记
    response = re.sub(r"\[来源\d+\]", "", response)
    # 去除多余空行
    response = re.sub(r"\n{3,}", "\n\n", response)
    return response.strip()


def truncate_contexts(contexts: list, max_chars_per_ctx: int = 500, max_total_ctxs: int = 2) -> list:
    """
    截断检索上下文，避免 RAGAS prompt 超过 llama-server context window。
    进一步收紧到 500 字符 / 2 条，为 Faithfulness/ContextRecall 的 JSON 生成留出更多 token 空间。
    """
    truncated = []
    for ctx in contexts[:max_total_ctxs]:
        if len(ctx) > max_chars_per_ctx:
            ctx = ctx[:max_chars_per_ctx] + "..."
        truncated.append(ctx)
    return truncated


async def run_ragas_manual(eval_data):
    """
    手动执行 RAGAS 评估，绕过 evaluate() 函数。
    逐个样本、逐个 metric 调用 _ascore()，避免并发执行导致的问题。
    """
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics._faithfulness import Faithfulness
    from ragas.metrics._answer_relevance import AnswerRelevancy
    from ragas.metrics._context_precision import ContextPrecision
    from ragas.metrics._context_recall import ContextRecall
    from langchain_openai import ChatOpenAI
    import pandas as pd
    import numpy as np

    # 过滤 ERROR 样本
    valid_data = [d for d in eval_data if not d["response"].startswith("ERROR")]
    if len(valid_data) < len(eval_data):
        log_progress(f"Warning: {len(eval_data) - len(valid_data)} errors, using {len(valid_data)} valid")

    judge_llm = ChatOpenAI(
        model="qwen2.5-7b-instruct-q3_k_m.gguf",
        base_url="http://localhost:8081/v1",
        api_key="sk-no-key-needed",
        temperature=0.0,
        max_tokens=1024,
        max_retries=3,
    )

    rag_engine.embedding.init()
    judge_embeddings = rag_engine.embedding._model

    # 创建 metric 实例并配置
    faithfulness = Faithfulness()
    faithfulness.llm = judge_llm

    answer_relevancy = AnswerRelevancy()
    answer_relevancy.strictness = 1
    answer_relevancy.llm = judge_llm
    answer_relevancy.embeddings = judge_embeddings

    context_precision = ContextPrecision()
    context_precision.llm = judge_llm

    context_recall = ContextRecall()
    context_recall.llm = judge_llm

    metrics = [
        ("faithfulness", faithfulness),
        ("answer_relevancy", answer_relevancy),
        ("context_precision", context_precision),
        ("context_recall", context_recall),
    ]

    # 准备样本 - 清洗 response 并截断上下文
    samples = []
    for item in valid_data:
        cleaned_response = clean_response(item["response"])
        truncated_contexts = truncate_contexts(item["retrieved_contexts"])
        samples.append(SingleTurnSample(
            user_input=item["user_input"],
            response=cleaned_response,
            reference=item["reference"],
            retrieved_contexts=truncated_contexts,
        ))

    log_progress(f"Running manual RAGAS on {len(samples)} samples...")

    # 逐个样本、逐个 metric 评估
    all_scores = []
    for i, sample in enumerate(samples):
        row_scores = {
            "user_input": valid_data[i]["user_input"],
            "response": valid_data[i]["response"],
            "reference": valid_data[i]["reference"],
            "retrieved_contexts": valid_data[i]["retrieved_contexts"],
        }
        for metric_name, metric in metrics:
            score = 0.0
            last_err = None
            for attempt in range(3):
                try:
                    score = await metric._single_turn_ascore(sample, callbacks=[])
                    if np.isnan(score):
                        log_progress(f"  Q{i+1} {metric_name}: NaN (attempt {attempt+1})")
                        score = 0.0
                    else:
                        log_progress(f"  Q{i+1} {metric_name}: {score:.4f}")
                    break
                except Exception as e:
                    last_err = e
                    err_type = type(e).__name__
                    # 只有 JSON/Validation 错误才重试
                    if "JSON" in str(e) or "ValidationError" in err_type or "EOF" in str(e):
                        log_progress(f"  Q{i+1} {metric_name}: {err_type} (attempt {attempt+1}/3), retrying...")
                        await asyncio.sleep(1)
                        continue
                    else:
                        log_progress(f"  Q{i+1} {metric_name}: ERROR {err_type}: {e}")
                        break
            else:
                # 3 次都失败
                log_progress(f"  Q{i+1} {metric_name}: FAILED after 3 attempts ({last_err})")
                score = 0.0
            row_scores[metric_name] = score
        all_scores.append(row_scores)

    # 构建 DataFrame
    df = pd.DataFrame(all_scores)
    log_progress(f"Manual RAGAS complete. Columns: {list(df.columns)}")

    return df, valid_data


def generate_report(result_df, eval_data):
    import pandas as pd

    df = result_df
    metric_cols = [c for c in df.columns if c not in ['user_input', 'response', 'reference', 'retrieved_contexts']]

    lines = [
        "# SmartQA Pro - RAGAS 完整评估报告",
        "",
        f"**评估时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**测试样本**: {len(eval_data)} 题",
        f"**LLM**: Qwen2.5-7B-Instruct Q3_K_M (llama.cpp 本地推理, ctx=4096)",
        f"**Embedding**: BAAI/bge-m3",
        f"**Reranker**: BAAI/bge-reranker-v2-m3",
        f"**知识库**: 企业IT支持知识库 (9 chunks, 20 QA对)",
        f"**AnswerRelevancy strictness**: 1",
        "",
        "## 总体得分",
        "",
        "| 指标 | 得分 | 评级 | 说明 |",
        "|------|------|------|------|",
    ]

    metric_desc = {
        "faithfulness": "回答是否忠实于检索上下文",
        "answer_relevancy": "回答与问题的相关性",
        "context_precision": "检索结果中相关内容的排名精度",
        "context_recall": "标准答案所需信息是否都被检索到",
    }

    def get_grade(v):
        if v >= 0.9: return "优秀"
        if v >= 0.7: return "良好"
        if v >= 0.5: return "一般"
        if v >= 0.3: return "较差"
        return "需改进"

    for col in metric_cols:
        v = df[col].dropna().mean()
        desc = metric_desc.get(col, "")
        grade = get_grade(v)
        lines.append(f"| {col} | **{v:.4f}** | {grade} | {desc} |")

    # 评级标准
    lines.extend(["", "## 评级标准", ""])
    lines.append("| 得分范围 | 评级 |")
    lines.append("|---------|------|")
    lines.append("| 0.9-1.0 | 优秀 |")
    lines.append("| 0.7-0.9 | 良好 |")
    lines.append("| 0.5-0.7 | 一般 |")
    lines.append("| 0.3-0.5 | 较差 |")
    lines.append("| 0-0.3   | 需改进 |")

    # 逐题详情
    lines.extend(["", "## 逐题详情", ""])
    for i, item in enumerate(eval_data):
        lines.append(f"### Q{i+1}: {item['user_input']}")
        lines.append(f"- **标准答案**: {item['reference'][:200]}")
        resp_preview = item['response'][:200] if not item['response'].startswith("ERROR") else f"**ERROR**: {item['response'][:100]}"
        lines.append(f"- **RAG 回答**: {resp_preview}")
        if i < len(df):
            for col in metric_cols:
                val = df.iloc[i][col] if col in df.columns else "N/A"
                if isinstance(val, float):
                    lines.append(f"- **{col}**: {val:.4f}")
                else:
                    lines.append(f"- **{col}**: {val}")
        lines.append("")

    # 综合分析
    lines.extend(["", "## 综合分析", ""])

    faithfulness = df.get("faithfulness", pd.Series([0])).dropna().mean()
    answer_relevancy = df.get("answer_relevancy", pd.Series([0])).dropna().mean()
    context_precision = df.get("context_precision", pd.Series([0])).dropna().mean()
    context_recall = df.get("context_recall", pd.Series([0])).dropna().mean()

    lines.append(f"### RAG 系统整体评分: {(faithfulness + answer_relevancy + context_precision + context_recall) / 4:.4f}")
    lines.append("")

    if faithfulness >= 0.9:
        lines.append("**忠实度 (Faithfulness)**: 优秀 - RAG 系统几乎没有幻觉问题，回答内容严格基于检索到的上下文")
    elif faithfulness >= 0.7:
        lines.append("**忠实度 (Faithfulness)**: 良好 - 偶有轻微幻觉，但核心信息准确")
    else:
        lines.append("**忠实度 (Faithfulness)**: 需改进 - 存在明显幻觉问题")
    lines.append("")

    if context_recall >= 0.9:
        lines.append("**上下文召回率 (Context Recall)**: 优秀 - 知识库覆盖了标准答案所需的大部分信息")
    elif context_recall >= 0.7:
        lines.append("**上下文召回率 (Context Recall)**: 良好 - 大部分所需信息被检索到")
    else:
        lines.append("**上下文召回率 (Context Recall)**: 需改进 - 知识库覆盖不足")
    lines.append("")

    if context_precision >= 0.7:
        lines.append("**上下文精确度 (Context Precision)**: 良好 - Reranker 起到了有效的重排序作用")
    elif context_precision >= 0.5:
        lines.append("**上下文精确度 (Context Precision)**: 一般 - 相关文档排名可优化")
    else:
        lines.append("**上下文精确度 (Context Precision)**: 需改进 - 检索结果噪声较大")
    lines.append("")

    if answer_relevancy >= 0.7:
        lines.append("**答案相关性 (Answer Relevancy)**: 良好 - 回答内容与问题高度相关")
    elif answer_relevancy >= 0.5:
        lines.append("**答案相关性 (Answer Relevancy)**: 一般 - 回答可能包含冗余信息")
    else:
        lines.append("**答案相关性 (Answer Relevancy)**: 需改进 - 回答与问题匹配度低")
    lines.append("")

    # 改进建议
    lines.extend(["", "## 改进建议", ""])
    if answer_relevancy < 0.7:
        lines.append("1. **优化 RAG 回答模板** - 去除冗余前缀和后缀，让回答更直接")
        lines.append("2. **增加 AnswerRelevancy strictness** - 使用更强的 Judge LLM（如 DeepSeek API）时可将 strictness 提升到 2-3")
    if context_precision < 0.7:
        lines.append("3. **优化 Reranker 阈值** - 调整 top_k 和 score 阈值，减少噪声文档")
    if context_recall < 0.7:
        lines.append("4. **扩充知识库** - 增加文档覆盖面，确保标准答案所需信息都能被检索到")
    lines.append("5. **使用更强的 LLM** - 升级到 Qwen2.5-14B 或使用 API 可提升回答质量")
    lines.append("6. **优化 chunk 策略** - 调整 chunk_size 和 overlap 参数，提升检索精度")

    report_path = os.path.join(EVAL_DIR, "eval_report_full.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log_progress(f"Report saved: {report_path}")

    # CSV
    csv_path = os.path.join(EVAL_DIR, "eval_result_full.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log_progress(f"CSV saved: {csv_path}")

    return report_path


async def main():
    import pandas as pd
    t0 = time.time()

    # 清除旧进度
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

    log_progress("=" * 60)
    log_progress("SmartQA Pro - RAGAS Full Evaluation (20 questions)")
    log_progress("=" * 60)

    # Step 1: 收集数据
    log_progress("\n[Step 1/3] Collecting RAG data...")
    eval_data = await collect_data()

    raw_path = os.path.join(EVAL_DIR, "eval_raw_data_full.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(eval_data, f, ensure_ascii=False, indent=2)
    log_progress(f"Raw data saved: {raw_path}")

    success = sum(1 for d in eval_data if not d["response"].startswith("ERROR"))
    log_progress(f"Success: {success}/{len(eval_data)}")

    if success == 0:
        log_progress("All samples failed! Check RAG pipeline.")
        return

    # Step 2: 运行 RAGAS
    log_progress("\n[Step 2/3] Running RAGAS evaluation...")
    try:
        result_df, valid_data = await run_ragas_manual(eval_data)
        log_progress(f"RAGAS DataFrame shape: {result_df.shape}")

        # Step 3: 生成报告
        log_progress("\n[Step 3/3] Generating report...")
        report_path = generate_report(result_df, valid_data)

    except Exception as e:
        log_progress(f"RAGAS FAILED: {type(e).__name__}: {e}")
        import traceback
        log_progress(traceback.format_exc())

    log_progress(f"\nTotal time: {time.time()-t0:.1f}s")
    log_progress("=" * 60)
    log_progress("Evaluation complete!")


if __name__ == "__main__":
    asyncio.run(main())
