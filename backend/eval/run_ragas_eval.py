"""
SmartQA Pro - RAGAS 评估脚本
============================================================
【学习要点】
1. RAGAS 评估的核心流程：
   准备测试集 → 运行RAG系统收集结果 → 计算指标 → 生成报告

2. RAGAS 0.4.x 的关键变化（vs 0.3.x）：
   - 使用 EvaluationDataset 而不是 Dataset
   - 使用 evaluate() 函数而非 Runner
   - 指标通过 metric 模块导入

3. 四大核心指标：
   - Faithfulness: 回答是否忠实于检索上下文（不编造）
   - AnswerRelevance: 回答是否切题
   - ContextPrecision: 检索结果中相关内容的排名是否靠前
   - ContextRecall: 标准答案需要的信息是否都被检索到

4. 评估使用的LLM（Judge LLM）：
   - RAGAS 内部也需要调用LLM来评判回答质量
   - 我们用同一个本地 llama.cpp 服务作为 Judge
   - 评估LLM和被评估LLM可以是同一个，但理想情况应该用更强的模型
============================================================
"""
import asyncio
import json
import sys
import os
import time
from datetime import datetime

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_dataset import TEST_QA_PAIRS


# ============================================================
# Step 1: 构建 RAGAS 评估数据集
# ============================================================
async def run_smartqa_rag(question: str) -> dict:
    """
    调用 SmartQA 的 RAG Agent，获取回答和检索上下文

    【学习要点】RAGAS 评估需要的数据：
    - response: RAG系统生成的回答
    - retrieved_contexts: 检索到的上下文片段列表

    这里我们直接调用 SmartQA 的内部 RAG Agent，
    而不是通过 HTTP API，因为需要拿到检索的上下文。
    """
    # 导入 SmartQA 内部模块（需要 FastAPI 上下文已初始化）
    from app.agents.rag import rag_agent
    from app.core.rag_engine import rag_engine
    from app.config import get_settings

    settings = get_settings()

    # Step 1: 获取检索上下文（模拟 RAG Agent 内部流程）
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

    # 提取检索上下文文本
    retrieved_contexts = [r.get("content", "") for r in unique_results]

    # Step 2: 获取 RAG 回答（完整流程）
    rag_result = await rag_agent.answer(query=question, session_id=None)

    return {
        "response": rag_result["answer"],
        "retrieved_contexts": retrieved_contexts,
        "confidence": rag_result["confidence"],
        "query_type": rag_result["query_type"],
        "context_used": rag_result["context_used"],
    }


async def collect_evaluation_data() -> list[dict]:
    """
    运行 RAG 系统收集评估数据

    【学习要点】
    评估前需要先"运行"一遍RAG系统，收集每个问题的：
    - 系统回答（response）
    - 检索上下文（retrieved_contexts）
    - 然后与人工标注的标准答案（reference）对比
    """
    eval_data = []

    print(f"开始收集评估数据，共 {len(TEST_QA_PAIRS)} 个问题...")
    print("=" * 60)

    for i, pair in enumerate(TEST_QA_PAIRS):
        question = pair["question"]
        reference = pair["reference_answer"]

        print(f"\n[{i+1}/{len(TEST_QA_PAIRS)}] 问题: {question}")

        try:
            start_time = time.time()
            result = await run_smartqa_rag(question)
            elapsed = time.time() - start_time

            response = result["response"]
            retrieved_contexts = result["retrieved_contexts"]

            print(f"  → 回答: {response[:80]}...")
            print(f"  → 检索上下文数: {len(retrieved_contexts)}")
            print(f"  → 耗时: {elapsed:.1f}s")

            eval_data.append({
                "user_input": question,
                "response": response,
                "reference": reference,
                "retrieved_contexts": retrieved_contexts,
            })

        except Exception as e:
            print(f"  ❌ 错误: {type(e).__name__}: {e}")
            eval_data.append({
                "user_input": question,
                "response": f"ERROR: {e}",
                "reference": reference,
                "retrieved_contexts": [],
            })

    return eval_data


# ============================================================
# Step 2: 运行 RAGAS 评估
# ============================================================
def run_ragas_evaluation(eval_data: list[dict]) -> dict:
    """
    使用 RAGAS 计算评估指标

    【学习要点】RAGAS 评估的核心代码只有几行：
    1. 创建 EvaluationDataset
    2. 选择评估指标
    3. 调用 evaluate()

    但准备工作（收集数据）才是最耗时的。
    """
    from ragas import evaluate
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
    from ragas.metrics import (
        Faithfulness,
        AnswerRelevance,
        ContextPrecision,
        ContextRecall,
    )
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    # 配置 RAGAS 使用的 Judge LLM（指向本地 llama.cpp）
    # 【学习要点】RAGAS 内部用这个LLM来判断：
    # - 回答是否忠实于上下文（Faithfulness）
    # - 回答是否切题（AnswerRelevance）
    # - 检索结果是否相关（ContextPrecision/Recall）
    judge_llm = ChatOpenAI(
        model="qwen2.5-7b-instruct-q3_k_m.gguf",
        base_url="http://localhost:8081/v1",
        api_key="sk-no-key-needed",
        temperature=0.0,  # Judge LLM 应该确定性输出
    )

    # Embeddings 也用本地模型
    # 【学习要点】ContextPrecision 和 ContextRecall 需要计算语义相似度
    # RAGAS 内部用 embeddings 来做这个
    # 这里我们用 SmartQA 已有的 BGE embeddings
    from app.core.rag_engine import rag_engine
    judge_embeddings = rag_engine._embedding_model

    # 构建 EvaluationDataset
    samples = []
    for item in eval_data:
        sample = SingleTurnSample(
            user_input=item["user_input"],
            response=item["response"],
            reference=item["reference"],
            retrieved_contexts=item["retrieved_contexts"],
        )
        samples.append(sample)

    dataset = EvaluationDataset(samples=samples)

    # 选择评估指标
    metrics = [
        Faithfulness(),
        AnswerRelevance(),
        ContextPrecision(),
        ContextRecall(),
    ]

    print("\n" + "=" * 60)
    print("开始 RAGAS 评估...")
    print(f"评估数据量: {len(samples)} 条")
    print(f"评估指标: {[m.__class__.__name__ for m in metrics]}")
    print("=" * 60)

    # 运行评估
    result = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=judge_llm,
        embeddings=judge_embeddings,
    )

    return result


# ============================================================
# Step 3: 生成评估报告
# ============================================================
def generate_report(result, eval_data: list[dict]) -> str:
    """
    生成 Markdown 格式的评估报告
    """
    # 提取指标分数
    scores = {}
    if hasattr(result, '_scores'):
        scores = result._scores
    elif hasattr(result, 'scores'):
        scores = result.scores
    else:
        # RAGAS 0.4.x 返回的结果格式
        try:
            # 尝试转为 pandas 获取分数
            if hasattr(result, 'to_pandas'):
                df = result.to_pandas()
                for col in df.columns:
                    if col not in ['user_input', 'response', 'reference', 'retrieved_contexts']:
                        scores[col] = df[col].mean()
        except Exception:
            pass

    # 尝试直接从 result 提取
    if not scores:
        try:
            # RAGAS Result 对象
            for key in ['faithfulness', 'answer_relevance', 'context_precision', 'context_recall']:
                if hasattr(result, key):
                    scores[key] = getattr(result, key)
        except Exception:
            pass

    report_lines = [
        "# SmartQA Pro - RAGAS 评估报告",
        "",
        f"**评估时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**评估数据量**: {len(eval_data)} 条",
        f"**RAGAS 版本**: 0.4.3",
        f"**被评估LLM**: Qwen2.5-7B-Instruct (Q3_K_M, llama.cpp本地推理)",
        f"**Judge LLM**: 同被评估LLM",
        "",
        "---",
        "",
        "## 一、总体评分",
        "",
    ]

    # 总体分数表格
    if scores:
        report_lines.append("| 指标 | 英文名 | 得分 | 等级 |")
        report_lines.append("|------|--------|------|------|")
        metric_names = {
            'faithfulness': ('忠实度', 'Faithfulness'),
            'answer_relevance': ('答案相关性', 'AnswerRelevance'),
            'context_precision': ('上下文精确度', 'ContextPrecision'),
            'context_recall': ('上下文召回率', 'ContextRecall'),
        }
        for key, val in scores.items():
            cn_name, en_name = metric_names.get(key, (key, key))
            if isinstance(val, (int, float)):
                score_val = val
            else:
                try:
                    score_val = float(val)
                except (ValueError, TypeError):
                    score_val = 0.0

            if score_val >= 0.8:
                grade = "🟢 优秀"
            elif score_val >= 0.6:
                grade = "🟡 良好"
            elif score_val >= 0.4:
                grade = "🟠 一般"
            else:
                grade = "🔴 较差"

            report_lines.append(f"| {cn_name} | {en_name} | {score_val:.4f} | {grade} |")
    else:
        report_lines.append("*(无法提取分数，原始结果如下)*")
        report_lines.append(f"```json\n{str(result)[:2000]}\n```")

    # 逐题详情
    report_lines.extend([
        "",
        "---",
        "",
        "## 二、逐题评估详情",
        "",
    ])

    for i, item in enumerate(eval_data):
        report_lines.append(f"### 问题 {i+1}")
        report_lines.append(f"**问**: {item['user_input']}")
        report_lines.append(f"**标准答案**: {item['reference']}")
        report_lines.append(f"**系统回答**: {item['response'][:300]}{'...' if len(item['response']) > 300 else ''}")
        report_lines.append(f"**检索上下文数**: {len(item['retrieved_contexts'])}")
        report_lines.append("")

    # 评估说明
    report_lines.extend([
        "---",
        "",
        "## 三、指标说明",
        "",
        "| 指标 | 含义 | 评估什么 |",
        "|------|------|---------|",
        "| 忠实度 Faithfulness | 回答是否只基于检索上下文 | RAG 是否产生幻觉 |",
        "| 答案相关性 AnswerRelevance | 回答是否切题 | 生成质量 |",
        "| 上下文精确度 ContextPrecision | 相关内容是否排名靠前 | 检索排序质量 |",
        "| 上下文召回率 ContextRecall | 标准答案所需信息是否被检索到 | 检索完整性 |",
        "",
        "---",
        "",
        "## 四、改进建议",
        "",
    ])

    # 根据分数给出改进建议
    suggestions = []
    for key, val in scores.items():
        if isinstance(val, (int, float)):
            if val < 0.5:
                if key == 'faithfulness':
                    suggestions.append("- **忠实度较低**：LLM 可能在编造信息。建议加强 Prompt 中'只基于上下文回答'的指令，或调整 temperature 至更低值。")
                elif key == 'answer_relevance':
                    suggestions.append("- **答案相关性较低**：回答可能偏题。建议优化 RAG Prompt，让 LLM 更聚焦用户问题。")
                elif key == 'context_precision':
                    suggestions.append("- **上下文精确度较低**：检索结果中无关内容较多。建议调整 Reranker 或减小 top_k。")
                elif key == 'context_recall':
                    suggestions.append("- **上下文召回率较低**：标准答案需要的信息没被检索到。建议增加 top_k 或优化 Embedding 模型。")

    if suggestions:
        report_lines.extend(suggestions)
    else:
        report_lines.append("所有指标均达标，系统表现良好！")

    return "\n".join(report_lines)


# ============================================================
# 主流程
# ============================================================
async def main():
    print("=" * 60)
    print("SmartQA Pro - RAGAS 评估系统")
    print("=" * 60)

    # Step 1: 初始化 SmartQA 上下文（连接 Milvus 等）
    print("\n[1/4] 初始化 SmartQA 上下文...")
    try:
        from app.core.milvus_client import milvus_manager
        from app.core.redis_client import redis_client
        # 确保连接
        milvus_manager.connect()
        print("  ✅ Milvus 连接成功")
    except Exception as e:
        print(f"  ❌ 初始化失败: {e}")
        return

    # Step 2: 收集评估数据
    print("\n[2/4] 收集评估数据...")
    eval_data = await collect_evaluation_data()

    # 保存原始数据
    eval_dir = os.path.dirname(os.path.abspath(__file__))
    raw_path = os.path.join(eval_dir, "eval_raw_data.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(eval_data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 原始数据已保存: {raw_path}")

    # Step 3: 运行 RAGAS 评估
    print("\n[3/4] 运行 RAGAS 评估（这可能需要几分钟）...")
    try:
        result = run_ragas_evaluation(eval_data)
        print("  ✅ RAGAS 评估完成")
        print(f"\n  评估结果概览:")
        print(f"  {result}")
    except Exception as e:
        print(f"  ❌ RAGAS 评估失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

        # 即使 RAGAS 评估失败，也保存已收集的数据
        print("\n  评估数据已保存，可手动排查问题后重新运行。")
        return

    # Step 4: 生成报告
    print("\n[4/4] 生成评估报告...")
    report = generate_report(result, eval_data)
    report_path = os.path.join(eval_dir, f"eval_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"  ✅ 报告已保存: {report_path}")

    # 保存 RAGAS 原始结果
    try:
        result_path = os.path.join(eval_dir, "eval_ragas_result.json")
        # 尝试序列化结果
        if hasattr(result, 'to_pandas'):
            df = result.to_pandas()
            df.to_json(result_path, orient='records', force_ascii=False, indent=2)
            print(f"  ✅ RAGAS 结果已保存: {result_path}")
    except Exception as e:
        print(f"  ⚠️ RAGAS 结果保存失败: {e}")

    print("\n" + "=" * 60)
    print("评估完成！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
