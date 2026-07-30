"""
SupplyChainRAG - RAG 评估 API
============================================================
1. 离线评估需要 ground truth（相关文档标注），用于计算 Recall/Precision/MRR/NDCG
2. 在线评估无需 ground truth，通过 rerank_score 分布和检索来源分析质量
3. LLM-as-Judge：用大模型评判生成答案的质量（需要调用 LLM API）
============================================================
"""
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
from app.core.retrieval_evaluator import retrieval_evaluator as rag_evaluator
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
    """LLM-as-Judge 评判请求

    安全约束：retrieved_contexts 限制 ≤20 条，防止攻击者传 N 万条触发
    LLM prompt 长度爆炸 + LLM API 拒绝服务（DoS）。
    """
    query: str = Field(..., max_length=2000, description="原始问题")
    retrieved_contexts: list[str] = Field(
        ..., max_length=20, description="检索到的上下文列表（最多 20 条，防 DoS）"
    )
    generated_answer: str = Field(..., max_length=10000, description="LLM生成的答案")
    reference_answer: Optional[str] = Field(
        default=None, max_length=5000, description="参考答案（可选）"
    )


# ---- API 接口 ----

@router.post("/offline")
async def evaluate_offline(req: OfflineEvalRequest, request: Request):
    """离线评估：基于 ground truth 计算检索指标"""
    from app.core.auth import get_current_user_required
    await get_current_user_required(request)

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
async def evaluate_online(req: OnlineEvalRequest, request: Request):
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
    from app.core.auth import get_current_user_required
    await get_current_user_required(request)

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
async def evaluate_judge(req: JudgeRequest, request: Request):
    """
    LLM-as-Judge：使用大模型评判生成答案的质量

    评判维度：
    1. Answer Correctness（答案正确性）: 1-5分
    2. Answer Relevance（答案相关性）: 1-5分
    3. Context Utilization（上下文利用）: 1-5分
    4. Hallucination（幻觉程度）: 1-5分（越低越好）

    注意：需要配置 LLM provider（deepseek/minimax/ollama）
    """
    from app.core.auth import get_current_user_required
    await get_current_user_required(request)
    try:
        from app.config import get_settings
        get_settings()  # 触发配置加载

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

        # 调用 LLM（异步，不阻塞事件循环）
        llm = LLMFactory.get_llm(temperature=0.1, streaming=False)
        from langchain_core.messages import HumanMessage
        response = await llm.ainvoke([HumanMessage(content=judge_prompt)])
        content = response.content

        # 提取JSON（使用统一工具函数）
        from app.core.utils import parse_llm_json
        try:
            scores = parse_llm_json(content)
        except (ValueError, Exception):
            scores = {"raw_output": content}

        return {
            "success": True,
            "judge_result": scores,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Judge评估失败: {e}")
        raise HTTPException(status_code=500, detail=f"评判失败: {e}")


@router.get("/full")
async def run_full_evaluation(request: Request):
    """返回最新的【官方 RAGAS】评估结果（由 backend/eval/run_comprehensive_ragas.py 生成）。

    本接口不再实时计算任何关键词 proxy 指标；改为读取最近一次官方 ragas 库
    (LLM-as-Judge) 落盘的四项指标（Faithfulness / AnswerRelevancy / ContextPrecision / ContextRecall）。
    """
    from app.core.auth import get_current_user_required
    await get_current_user_required(request)

    import os
    import json
    import glob

    eval_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "eval",
    )
    candidates = []
    for fp in glob.glob(os.path.join(eval_dir, "*.json")):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        rm = data.get("ragas_metrics")
        if isinstance(rm, dict) and any(v is not None for v in rm.values()):
            candidates.append((os.path.getmtime(fp), fp, data))

    if not candidates:
        return {
            "success": False,
            "error": "暂无官方 RAGAS 结果。请先配置 RAGAS_JUDGE_*（backend/.env）并运行 "
                     "backend/eval/run_comprehensive_ragas.py --judge-only 生成官方 RAGAS 评分。",
        }

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, fp, data = candidates[0]
    rm = data["ragas_metrics"]
    metrics = {
        "faithfulness": rm.get("faithfulness"),
        "answer_relevancy": rm.get("answer_relevancy"),
        "context_precision": rm.get("context_precision"),
        "context_recall": rm.get("context_recall"),
    }
    vals = [v for v in metrics.values() if v is not None]
    overall = data.get("overall")
    if overall is None and vals:
        overall = round(sum(vals) / len(vals), 4)

    return {
        "success": True,
        "official": True,
        "source_file": os.path.basename(fp),
        "judge_model": data.get("judge_model"),
        "gen_model": data.get("gen_model"),
        "date": data.get("date"),
        "samples": data.get("valid_samples", data.get("samples", 0)),
        "metrics": metrics,
        "overall": overall,
    }


@router.get("/summary")
async def get_evaluation_summary(request: Request):
    """
    获取评估汇总

    返回所有离线评估指标的平均值，包括：
    - avg_recall_at_5: 平均召回率
    - avg_ndcg_at_5: 平均 NDCG
    - avg_mrr: 平均 MRR
    - avg_map: 平均 AP
    - avg_retrieval_score: 综合检索得分
    - retrieval_score_p50/p90: 综合得分的50/90分位数
    """
    from app.core.auth import get_current_user_required
    await get_current_user_required(request)

    try:
        summary = rag_evaluator.get_summary()
        return {
            "success": True,
            "summary": summary,
        }
    except Exception as e:
        logger.error(f"获取汇总失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取汇总失败: {e}")
