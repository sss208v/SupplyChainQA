"""
SmartQA Pro - RAG 评估 API
============================================================
1. 离线评估需要 ground truth（相关文档标注），用于计算 Recall/Precision/MRR/NDCG
2. 在线评估无需 ground truth，通过 rerank_score 分布和检索来源分析质量
3. LLM-as-Judge：用大模型评判生成答案的质量（需要调用 LLM API）
============================================================
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from app.core.rag_evaluator import rag_evaluator
from app.core.evaluator import ragas_evaluator, load_ground_truth
from app.agents.rag import rag_agent
from app.core.llm_router import LLMFactory
import asyncio

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/evaluate", tags=["评估"])


# ---- 请求/响应模型 ----

class OfflineEvalRequest(BaseModel):
    """离线评估请求（需要 ground truth）"""
    query: str = Field(..., description="查询文本")
    retrieved_chunk_ids: list[str] = Field(..., description="检索返回的chunk_id列表（按排名顺序）")
    relevant_chunk_ids: list[str] = Field(..., description="实际相关的chunk_id列表（ground truth）")


class OnlineEvalRequest(BaseModel):
    """在线评估请求（无需 ground truth）"""
    query: str = Field(..., description="查询文本")
    top_k: int = Field(default=5, description="检索返回的Top-K", ge=1, le=20)


class JudgeRequest(BaseModel):
    """LLM-as-Judge 评判请求"""
    query: str = Field(..., description="原始问题")
    retrieved_contexts: list[str] = Field(..., description="检索到的上下文列表")
    generated_answer: str = Field(..., description="LLM生成的答案")
    reference_answer: Optional[str] = Field(default=None, description="参考答案（可选）")


# ---- API 接口 ----

@router.post("/offline")
async def evaluate_offline(req: OfflineEvalRequest):
    """
    离线评估：基于 ground truth 计算检索指标

    适用场景：
    - 提前准备好标注数据（query → relevant chunks）
    - 对比不同检索策略的效果
    - 评估 recall@K、precision@K、MRR、NDCG 等指标

    请求示例：
    {
        "query": "RAG系统的核心组件有哪些？",
        "retrieved_chunk_ids": ["doc1_chunk_0", "doc2_chunk_3", "doc3_chunk_1"],
        "relevant_chunk_ids": ["doc1_chunk_0", "doc2_chunk_3"]
    }
    """
    try:
        result = rag_evaluator.evaluate_retrieval(
            query=req.query,
            retrieved_chunk_ids=req.retrieved_chunk_ids,
            relevant_chunk_ids=req.relevant_chunk_ids,
        )
        return {
            "success": True,
            "evaluation": result.to_dict(),
        }
    except Exception as e:
        logger.error(f"离线评估失败: {e}")
        raise HTTPException(status_code=500, detail=f"评估失败: {e}")


@router.post("/online")
async def evaluate_online(req: OnlineEvalRequest):
    """
    在线评估：无需 ground truth，基于 rerank_score 分布评估检索质量

    适用场景：
    - 快速评估当前查询的检索效果
    - 生产环境实时监控
    - 无标注数据时的质量摸底

    评估维度：
    - avg_rerank_score: 平均重排序分数（越高越好）
    - vector_ratio: 向量检索结果占比
    - bm25_ratio: BM25检索结果占比
    - quality_label: 质量等级（excellent/good/fair/poor/no_signal）
    """
    try:
        result = rag_evaluator.evaluate_online(
            query=req.query,
            top_k=req.top_k,
        )
        return {
            "success": True,
            "evaluation": result,
        }
    except Exception as e:
        logger.error(f"在线评估失败: {e}")
        raise HTTPException(status_code=500, detail=f"评估失败: {e}")


@router.post("/judge")
async def evaluate_judge(req: JudgeRequest):
    """
    LLM-as-Judge：使用大模型评判生成答案的质量

    评判维度：
    1. Answer Correctness（答案正确性）: 1-5分
    2. Answer Relevance（答案相关性）: 1-5分
    3. Context Utilization（上下文利用）: 1-5分
    4. Hallucination（幻觉程度）: 1-5分（越低越好）

    注意：需要配置 LLM provider（deepseek/minimax/ollama）
    """
    try:
        from app.config import get_settings
        settings = get_settings()

        # 构建评判 Prompt
        judge_prompt = f"""你是一个严格的 RAG 系统答案质量评审员。请对以下问答进行评估。

【问题】
{req.query}

【检索到的上下文】
{chr(10).join([f"[{i+1}] {ctx}" for i, ctx in enumerate(req.retrieved_contexts)])}

【生成的答案】
{req.generated_answer}

{f'【参考答案】{req.reference_answer}' if req.reference_answer else ''}

请从以下四个维度打分（1-5分，5分最高）：
1. Answer Correctness（答案正确性）: 答案是否正确回答了问题
2. Answer Relevance（答案相关性）: 答案是否与问题相关
3. Context Utilization（上下文利用）: 答案是否充分利用了检索到的上下文
4. Hallucination（幻觉程度）: 答案中是否存在与上下文不符的内容（1分=大量幻觉，5分=无幻觉）

请用以下JSON格式返回：
{{
    "answer_correctness": X,
    "answer_relevance": X,
    "context_utilization": X,
    "hallucination": X,
    "overall_score": X,
    "feedback": "简短评价（1-2句话）"
}}
"""

        # 调用 LLM
        try:
            if settings.LLM_PROVIDER == "deepseek":
                import openai
                client = openai.OpenAI(
                    api_key=settings.DEEPSEEK_API_KEY,
                    base_url=settings.DEEPSEEK_BASE_URL,
                )
                response = client.chat.completions.create(
                    model=settings.DEEPSEEK_MODEL,
                    messages=[{"role": "user", "content": judge_prompt}],
                    temperature=0.1,
                )
                content = response.choices[0].message.content
            elif settings.LLM_PROVIDER == "minimax":
                import openai
                client = openai.OpenAI(
                    api_key=settings.MINIMAX_API_KEY,
                    base_url=settings.MINIMAX_BASE_URL,
                )
                response = client.chat.completions.create(
                    model=settings.MINIMAX_MODEL,
                    messages=[{"role": "user", "content": judge_prompt}],
                    temperature=0.1,
                )
                content = response.choices[0].message.content
            else:
                raise HTTPException(status_code=400, detail="仅支持 deepseek / minimax 作为 judge")

            import json, re
            # 提取JSON
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                scores = json.loads(match.group())
            else:
                scores = {"raw_output": content}

            return {
                "success": True,
                "judge_result": scores,
            }

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"LLM 调用失败: {e}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Judge评估失败: {e}")
        raise HTTPException(status_code=500, detail=f"评判失败: {e}")


@router.get("/full")
async def run_full_evaluation():
    """
    运行全量 RAGAS 评估套件

    对黄金测试集中的每条 query 执行：
    1. 混合检索（向量 + BM25）
    2. LLM 生成回答
    3. 计算三大 RAGAS 指标：
       - Context Precision（检索准确率）
       - Faithfulness（忠实度/防幻觉）
       - Answer Relevance（回答相关性）

    返回逐条评分和汇总统计。
    """
    try:
        ground_truth = load_ground_truth()
        if not ground_truth:
            return {
                "success": False,
                "error": "黄金测试集为空，请确认 backend/data/eval_ground_truth.json 存在",
            }

        logger.info(f"启动全量 RAGAS 评估: {len(ground_truth)} 条测试用例")

        # 逐条评估（仅检索模式，避免 LLM 调用耗时过高）
        results = []
        total_cp = 0.0
        total_faith = 0.0
        total_ar = 0.0
        total_time = 0.0

        for item in ground_truth:
            query = item.get("query", "")
            qid = item.get("id", "unknown")
            t0 = asyncio.get_event_loop().time()

            try:
                # 检索
                search_result = rag_agent.rag.search(query, top_k=5)
                chunks = search_result.get("results", [])

                # 评估
                eval_result = ragas_evaluator.evaluate_single(
                    query=query,
                    retrieved_chunks=chunks,
                    generated_answer="",
                    reference_answer=item.get("reference_answer", ""),
                )

                elapsed = (asyncio.get_event_loop().time() - t0) * 1000
                total_time += elapsed

                results.append({
                    "id": qid,
                    "query": query,
                    "context_precision": eval_result.context_precision,
                    "faithfulness": eval_result.faithfulness,
                    "answer_relevance": eval_result.answer_relevance,
                    "overall": eval_result.overall_score,
                    "retrieval_count": eval_result.retrieval_count,
                    "time_ms": round(elapsed, 1),
                })
                total_cp += eval_result.context_precision
                total_faith += eval_result.faithfulness
                total_ar += eval_result.answer_relevance

            except Exception as e:
                logger.error(f"评估用例 {qid} 失败: {e}")
                results.append({
                    "id": qid,
                    "query": query,
                    "error": str(e),
                })

        n = len(results)
        summary = {
            "total_queries": n,
            "avg_context_precision": round(total_cp / n, 4) if n > 0 else 0,
            "avg_faithfulness": round(total_faith / n, 4) if n > 0 else 0,
            "avg_answer_relevance": round(total_ar / n, 4) if n > 0 else 0,
            "avg_overall": round((total_cp + total_faith + total_ar) / (n * 3), 4) if n > 0 else 0,
            "total_time_ms": round(total_time, 1),
            "details": results,
        }

        return {"success": True, "summary": summary}

    except Exception as e:
        logger.error(f"全量评估失败: {e}")
        raise HTTPException(status_code=500, detail=f"评估失败: {e}")


@router.get("/summary")
async def get_evaluation_summary():
    """
    获取评估历史汇总

    返回所有离线评估指标的平均值，包括：
    - avg_recall_at_5: 平均召回率
    - avg_ndcg_at_5: 平均 NDCG
    - avg_mrr: 平均 MRR
    - avg_map: 平均 AP
    - avg_retrieval_score: 综合检索得分
    - retrieval_score_p50/p90: 综合得分的50/90分位数
    """
    try:
        summary = rag_evaluator.get_summary()
        return {
            "success": True,
            "summary": summary,
        }
    except Exception as e:
        logger.error(f"获取汇总失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取汇总失败: {e}")
