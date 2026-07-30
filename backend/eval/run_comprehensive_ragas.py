# -*- coding: utf-8 -*-
"""综合 RAG 评估 - 仅使用官方 RAGAS（ragas 0.4.3, LLM-as-Judge）

本脚本已收敛为单一官方 RAGAS 评估，输出四项官方指标：
  Faithfulness / AnswerRelevancy / ContextPrecision / ContextRecall
历史的关键词 proxy 指标（v5.2 等）已彻底移除，不再输出任何非官方代理分数。

judge 通过 backend/.env 配置（推荐 DeepSeek 官方 deepseek-v4-flash 非思考模式）：
  RAGAS_JUDGE_BASE_URL / RAGAS_JUDGE_MODEL / RAGAS_JUDGE_API_KEY（缺省回退本地 llama.cpp）

用法：
  生成答案:  python eval/run_comprehensive_ragas.py --generate-only --gen-model Qwen3-14B --out eval/raw.json
  官方评分:  python eval/run_comprehensive_ragas.py --judge-only --in eval/raw.json --out eval/result.json
  完整流程:  python eval/run_comprehensive_ragas.py
"""
import asyncio, json, sys, os, time, argparse
from datetime import datetime
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.test_dataset import TEST_QA_PAIRS
from eval.eval_utils import strip_citation_tail, strip_non_factual_frame
from app.core.milvus_client import milvus_manager
from app.core.rag_engine import rag_engine
from app.agents.rag import rag_agent
from app.config import get_settings

settings = get_settings()
eval_dir = os.path.dirname(os.path.abspath(__file__))

LLAMA_API_URL = settings.LOCAL_LLM_BASE_URL
LLAMA_MODEL = settings.LOCAL_LLM_MODEL

NEW_QUESTIONS = [
    {"question": "采购订单的审批流程是怎样的？",
     "reference_answer": "采购订单审批路径：采购员制单、采购经理初审、需求部门复核、财务总监审批（金额>=5万元）、采购总监终审。审批须在24小时内完成，紧急订单标注加急可优先处理。单笔金额超过100万元需总经理联签。",
     "relevant_source_files": ["采购管理规范.md", "采购订单管理规范.md"],
     "relevant_keywords": ["采购订单", "审批", "审核"],
     "source_file": "采购订单管理规范.md"},
    {"question": "来料检验不合格时如何处理？",
     "reference_answer": "来料检验不合格品分三类处理：关键不合格品一律退货，由IQC主管审批；主要不合格品可退货、让步接收（须工程部和技术部门双方会签）或降级使用；次要不合格品可让步接收，由IQC检验员直接判定。单个供应商年度让步接收不得超过3次。连续三个月合格率低于95%的供应商启动淘汰评估。",
     "relevant_source_files": ["质量检验标准.md", "质检标准与不合格品处理.md"],
     "relevant_keywords": ["来料检验", "不合格", "退货", "让步接收"],
     "source_file": "质检标准与不合格品处理.md"},
    {"question": "物料编码规则是什么？",
     "reference_answer": "物料编码采用12位数字字母混合码，结构为：分类代码（4位）+子类代码（3位）+顺序号（4位）+校验位（1位）。分类代码01XX为原材料、02XX为半成品、03XX为成品、04XX为辅助材料、05XX为包装材料。编码遵循一物一码原则，校验位采用Luhn算法计算。",
     "relevant_source_files": ["物料编码规则说明.md"],
     "relevant_keywords": ["物料编码", "编码规则", "分类代码"],
     "source_file": "物料编码规则说明.md"},
    {"question": "生产计划如何排程？",
     "reference_answer": "MPS主生产计划月度滚动编制：每月20日前接收销售预测和确认订单，22日前核对产线产能（A线日产500件、B线日产300件、C线日产800件），24日前生成逐周MPS排程，25日联合评审，27日前批准发布并锁定前2周计划。MRP每周一运行，计算净需求等于毛需求减现有库存减在途采购量加安全库存。",
     "relevant_source_files": ["生产计划管理规范.md"],
     "relevant_keywords": ["MPS", "主生产计划", "排程", "产能"],
     "source_file": "生产计划管理规范.md"},
    {"question": "物流发货的标准流程是什么？",
     "reference_answer": "标准出库四步法：1.领料申请（需求部门在ERP提交领料单）2.拣货（仓管员按FIFO先进先出原则发货）3.复核（另一名仓管员确认品名数量无误）4.发货登记（双方签字确认，系统扣减库存）。常规领料2小时内完成备货，紧急领料30分钟内响应。",
     "relevant_source_files": ["物流与仓储SOP.md"],
     "relevant_keywords": ["物流", "发货", "出库", "领料"],
     "source_file": "物流与仓储SOP.md"},
    {"question": "供应商等级评定的周期是多久？",
     "reference_answer": "供应商绩效评估每季度进行一次，年度综合评定。评估采用百分制四大维度：质量40%、交期30%、价格20%、服务10%。等级划分：A级>=4.5分（优秀）、B级3.5-4.4分（良好）、C级2.5-3.4分（合格限期整改）、D级<2.5分（启动淘汰）。评估结果于每季度末15日内公布。",
     "relevant_source_files": ["供应商绩效评估细则.md", "供应商管理手册.md"],
     "relevant_keywords": ["供应商", "等级评定", "季度", "ABCD"],
     "source_file": "供应商绩效评估细则.md"},
    {"question": "安全库存的计算公式是什么？",
     "reference_answer": "安全库存等于日均消耗量乘以采购周期天数再乘以1.5。日均消耗量取近3个月平均值，采购周期为从下单到入库的实际天数，系数1.5为标准浮动系数。A类物料可调整至1.8，C类可调至1.2。当库存降至安全库存的1.2倍时系统自动触发采购建议，降至安全库存以下触发紧急采购。",
     "relevant_source_files": ["库存管理制度.md", "库存管理ABC分类法.md"],
     "relevant_keywords": ["安全库存", "计算公式", "日均消耗"],
     "source_file": "库存管理制度.md"},
    {"question": "跨部门协作的审批节点有哪些？",
     "reference_answer": "标准采购到付款流程P2P：采购部下单、供应商交货、仓储部验收入库、生产部领料出库、财务部付款。紧急采购审批权限按金额分级：5000元以下部门经理即时审批，5000到5万元总监2小时内审批，5到20万元副总经理4小时内审批，20万元以上总经理当日内审批。紧急采购可先执行后补单，3个工作日内补齐审批手续。",
     "relevant_source_files": ["跨部门协作流程.md"],
     "relevant_keywords": ["跨部门", "协作", "审批", "P2P"],
     "source_file": "跨部门协作流程.md"},
]

ALL_QUESTIONS = list(TEST_QA_PAIRS) + NEW_QUESTIONS
print(f"Total questions: {len(ALL_QUESTIONS)} ({len(TEST_QA_PAIRS)} existing + {len(NEW_QUESTIONS)} new)")


# ============================================================
# 数据收集
# ============================================================

async def collect_data(questions, gen_model):
    milvus_manager.connect()
    milvus_manager.create_collection()

    # Graph RAG：评测直连链路需显式连接 Neo4j（生产由 main.py startup 连接），
    # 否则 engine.search 里 is_connected 恒为 False，图谱路静默短路
    from app.core.neo4j_client import neo4j_client
    if not neo4j_client.is_connected:
        await neo4j_client.connect()

    print("Rebuilding BM25 index from Milvus...")
    c = milvus_manager.collection
    c.load()
    all_chunks = []
    offset = 0
    batch_size = 5000
    while True:
        batch = c.query(expr="id > 0", output_fields=["doc_id", "chunk_id", "content", "source", "page_num", "security_group"], limit=batch_size, offset=offset)
        if not batch:
            break
        all_chunks.extend(batch)
        offset += batch_size
        if len(batch) < batch_size:
            break
    doc_chunks = defaultdict(list)
    for r in all_chunks:
        doc_chunks[r["doc_id"]].append({
            "chunk_id": r["chunk_id"],
            "content": r["content"],
            "source": r["source"],
            "page_num": r.get("page_num", 0),
            "security_group": r.get("security_group", ["admin"]),
        })
    for doc_id, chunks in doc_chunks.items():
        sg = chunks[0].get("security_group", ["admin"])
        rag_engine.bm25.index_documents(doc_id, chunks, security_group=sg)
    print(f"  BM25 rebuilt: {len(all_chunks)} chunks, {len(doc_chunks)} docs")

    from langchain_openai import ChatOpenAI
    import app.core.llm_router

    def get_local_llm(*args, **kwargs):
        return ChatOpenAI(
            api_key="not-needed",
            base_url=LLAMA_API_URL,
            model=gen_model,
            temperature=kwargs.get("temperature", 0.3) if "temperature" in kwargs else 0.3,
            max_tokens=1024,
            timeout=180,
        )
    app.core.llm_router.LLMFactory.get_llm = staticmethod(get_local_llm)

    eval_data = []
    print(f"\nCollecting RAG responses for {len(questions)} questions...")

    for i, pair in enumerate(questions):
        question = pair["question"]
        reference = pair["reference_answer"]
        print(f"\n[{i+1}/{len(questions)}] Q: {question}")

        try:
            start = time.time()
            rag_result = await rag_agent.answer(query=question, session_id=None)
            response = rag_result["answer"]
            sources = rag_result.get("sources", [])
            confidence = rag_result.get("confidence", 0)
            ctx_used = rag_result.get("context_used", 0)
            elapsed = time.time() - start
            retrieved_contexts = [s.get("snippet", "") for s in sources]
            print(f"  Answer ({elapsed:.1f}s, {len(sources)} srcs, ctx={ctx_used}, conf={confidence:.2f}): {response[:80]}...")
            eval_data.append({
                "user_input": question,
                # 送 judge 前口径清洗（口径标记 v-cite-strip）：先剥尾部引用列表（元信息非事实陈述），
                # 再剥开场铺垫/冗余收尾等非事实框架句；两者都会被 Faithfulness 当独立陈述误判。
                # 原始答案保留在 response_raw 供审计。
                "response": strip_non_factual_frame(strip_citation_tail(response)),
                "response_raw": response,
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


# ============================================================
# RAGAS 评估 (best-effort)
# ============================================================

def run_ragas_eval(eval_data, judge_model):
    try:
        from ragas import evaluate
        from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
        from ragas.metrics._faithfulness import Faithfulness
        from ragas.metrics._answer_relevance import AnswerRelevancy
        from ragas.metrics._context_precision import ContextPrecision
        from ragas.metrics._context_recall import ContextRecall
        from langchain_openai import ChatOpenAI

        # judge 可切换：设了 RAGAS_JUDGE_BASE_URL(见 backend/.env)则用外部强 judge，否则本地 llama。
        # 从 backend/.env 读判分配置（避免密钥进入命令行）。
        _envp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        _ecfg = {}
        if os.path.exists(_envp):
            for _l in open(_envp, encoding="utf-8"):
                _l = _l.strip()
                if _l and not _l.startswith("#") and "=" in _l:
                    _kk, _vv = _l.split("=", 1)
                    _ecfg[_kk.strip()] = _vv.strip()
        _jcfg = lambda k: os.getenv(k) or _ecfg.get(k)
        _judge_base = _jcfg("RAGAS_JUDGE_BASE_URL") or LLAMA_API_URL
        _judge_key = _jcfg("RAGAS_JUDGE_API_KEY") or _jcfg("SENSENOVA_API_KEY") or "not-needed"
        _judge_model = _jcfg("RAGAS_JUDGE_MODEL") or judge_model
        _is_remote = ("localhost" not in _judge_base) and ("127.0.0.1" not in _judge_base)
        print(f"  RAGAS judge -> base={_judge_base} model={_judge_model} remote={_is_remote}")
        _judge_kwargs = dict(
            api_key=_judge_key, base_url=_judge_base, model=_judge_model,
            temperature=0.0, max_tokens=4096, max_retries=5, timeout=600, n=1,
        )
        if _is_remote:
            # DeepSeek V4 默认思考模式（慢 + 要求 n=1）；judge 用非思考模式：更快、可用 temperature。
            _judge_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        judge_llm = ChatOpenAI(**_judge_kwargs)
        rag_engine.embedding.init()
        judge_embeddings = rag_engine.embedding._model
        valid_data = [d for d in eval_data if not d["response"].startswith("ERROR")]
        print(f"\nRAGAS scoring {len(valid_data)} valid samples (best-effort)...")

        samples = [SingleTurnSample(
            user_input=d["user_input"], response=d["response"],
            reference=d["reference"], retrieved_contexts=d["retrieved_contexts"],
        ) for d in valid_data]

        dataset = EvaluationDataset(samples=samples)
        # 推理型 judge（如 deepseek-v4-flash）强制 n=1，故 AnswerRelevancy strictness=1（否则请求 n=3 报 400）。
        metrics = [Faithfulness(), AnswerRelevancy(strictness=1), ContextPrecision(), ContextRecall()]
        # 本地 judge（Qwen3-14B）在 RAGAS 高并发结构化调用下易超时（实测 20 job 中 13 个 TimeoutError）。
        # 串行（max_workers=1）+ 加长超时，用时换可靠性；换强 judge（API）时可适当调高并发。
        from ragas.run_config import RunConfig
        _rc = RunConfig(timeout=600, max_workers=(8 if _is_remote else 1), max_retries=5)
        result = evaluate(dataset=dataset, metrics=metrics, llm=judge_llm, embeddings=judge_embeddings, run_config=_rc)

        import pandas as pd
        df = result.to_pandas()
        metric_cols = [c for c in df.columns if c not in ['user_input', 'response', 'reference', 'retrieved_contexts']]

        ragas_scores = {}
        for col in metric_cols:
            valid_vals = df[col].dropna()
            if len(valid_vals) > 0:
                ragas_scores[col] = round(valid_vals.mean(), 4)
            else:
                ragas_scores[col] = None

        print(f"\nRAGAS Scores ({len(valid_data)} samples):")
        for k, v in ragas_scores.items():
            if v is not None:
                status = "PASS" if v >= 0.75 else "FAIL"
                print(f"  {k}: {v:.4f} [{status}]")
            else:
                print(f"  {k}: ALL NaN (judge failed)")

        return ragas_scores
    except Exception as e:
        print(f"RAGAS evaluation failed: {e}")
        return {}


# ============================================================
# A/B 支持：生成与评判解耦（控制变量 A/B 前提）
#   - 生成模型与 judge 模型分开取（env RAGAS_GEN_MODEL / RAGAS_JUDGE_MODEL
#     或 --gen-model / --judge-model，缺省回退 settings.LOCAL_LLM_MODEL）
#   - --generate-only：只生成答案，raw 每条写入 gen_model 字段
#   - --judge-only：读取指定 raw + 固定 judge 模型算四项指标
#   - 无参：向后兼容的完整流程
# ============================================================

def _resolve_models(args):
    """解析生成/评判模型：命令行 > 环境变量 > settings.LOCAL_LLM_MODEL"""
    gen = args.gen_model or os.environ.get("RAGAS_GEN_MODEL") or LLAMA_MODEL
    judge = args.judge_model or os.environ.get("RAGAS_JUDGE_MODEL") or LLAMA_MODEL
    return gen, judge


def _questions(limit, dataset_path=None):
    if dataset_path:
        import json as _json
        with open(dataset_path, encoding="utf-8") as _f:
            qs = _json.load(_f)  # 自定义评测集 [{question, reference_answer, ...}]
    else:
        qs = list(ALL_QUESTIONS)
    if limit and limit > 0:
        qs = qs[:limit]  # 小样本子集，用于链路验证
    return qs


def _save_raw(eval_data, path, gen_model):
    for d in eval_data:
        d.setdefault("gen_model", gen_model)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(eval_data, f, ensure_ascii=False, indent=2)
    print(f"Saved raw ({len(eval_data)} records, gen_model={gen_model}) -> {path}")


def _load_raw(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"Loaded {len(data)} records <- {path}")
    return data


def _config_snapshot():
    return {
        "RERANK_TOP_K": settings.RERANK_TOP_K,
        "RRF_K": settings.RRF_K,
        "VECTOR_TOP_K": settings.VECTOR_TOP_K,
        "BM25_TOP_K": settings.BM25_TOP_K,
        "RERANKER_ENABLED": settings.RERANKER_ENABLED,
        "CRAG_ENABLED": settings.CRAG_ENABLED,
        "CHUNK_SIZE": settings.CHUNK_SIZE,
    }


def _evaluate_and_save(eval_data, gen_model, judge_model, out_path):
    """仅用官方 RAGAS（ragas 0.4.3, LLM-as-Judge）评分并落盘，输出四项官方指标。

    已移除所有关键词 proxy 指标：不再混入/回退 proxy，某项失败则如实记 NaN。
    """
    print(f"\n[ragas] Running OFFICIAL RAGAS (ragas 0.4.3, judge_model={judge_model})...")
    ragas_scores = run_ragas_eval(eval_data, judge_model)

    metric_keys = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    valid = [v for k in metric_keys if (v := ragas_scores.get(k)) is not None]
    print("\nOFFICIAL RAGAS METRICS:")
    all_pass = True
    for k in metric_keys:
        v = ragas_scores.get(k)
        if v is None:
            all_pass = False
            print(f"  {k:22s}: NaN (judge 未产出有效值)")
        else:
            status = "PASS" if v >= 0.75 else "FAIL"
            if v < 0.75:
                all_pass = False
            print(f"  {k:22s}: {v:.4f} [{status}] (target: >= 0.75)")
    overall = round(sum(valid) / len(valid), 4) if valid else 0.0
    print(f"\n  {'OVERALL':22s}: {overall:.4f}")
    print(f"  Status: {'ALL PASS' if all_pass else 'SOME METRICS BELOW 0.75 / NaN'}")

    output = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "gen_model": gen_model,
        "judge_model": judge_model,
        "samples": len(eval_data),
        "valid_samples": sum(1 for d in eval_data if not str(d.get("response", "")).startswith("ERROR")),
        "ragas_metrics": ragas_scores,   # 官方 ragas 0.4.3 LLM-as-Judge 四项（唯一权威）
        "overall": overall,
        "config": _config_snapshot(),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nSaved result -> {out_path}")
    return output


async def run_generate(args):
    gen_model, _ = _resolve_models(args)
    out = args.out or os.path.join(eval_dir, "eval_raw_data_comprehensive.json")
    print(f"[generate-only] gen_model={gen_model} -> {out}")
    eval_data = await collect_data(_questions(args.limit, getattr(args, "dataset", None)), gen_model)
    _save_raw(eval_data, out, gen_model)


def run_judge(args):
    _, judge_model = _resolve_models(args)
    inp = args.in_path or os.path.join(eval_dir, "eval_raw_data_comprehensive.json")
    eval_data = _load_raw(inp)
    gen_model = eval_data[0].get("gen_model", "unknown") if eval_data else "unknown"
    out = args.out or os.path.join(eval_dir, "eval_comprehensive_result.json")
    print(f"[judge-only] judge_model={judge_model} gen_model={gen_model} <- {inp}")
    _evaluate_and_save(eval_data, gen_model, judge_model, out)


async def run_full(args):
    gen_model, judge_model = _resolve_models(args)
    print("=" * 60)
    print(f"SupplyChainRAG - Comprehensive Evaluation ({len(_questions(args.limit, getattr(args, 'dataset', None)))} questions)")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"gen_model={gen_model}  judge_model={judge_model}")
    print(f"Config: RERANK_TOP_K={settings.RERANK_TOP_K}, RRF_K={settings.RRF_K}, "
          f"RERANKER_ENABLED={settings.RERANKER_ENABLED}, CRAG={settings.CRAG_ENABLED}")
    print("=" * 60)

    eval_data = await collect_data(_questions(args.limit, getattr(args, "dataset", None)), gen_model)
    raw_path = os.path.join(eval_dir, "eval_raw_data_comprehensive.json")
    _save_raw(eval_data, raw_path, gen_model)

    out = args.out or os.path.join(eval_dir, "eval_comprehensive_result.json")
    _evaluate_and_save(eval_data, gen_model, judge_model, out)


def main():
    parser = argparse.ArgumentParser(description="Comprehensive RAG eval (支持控制变量 A/B)")
    parser.add_argument("--generate-only", action="store_true", help="只生成答案并落盘 raw")
    parser.add_argument("--judge-only", action="store_true", help="只对已有 raw 做评判")
    parser.add_argument("--in", dest="in_path", default=None, help="judge-only 输入 raw 路径")
    parser.add_argument("--out", default=None, help="输出路径（raw 或 result）")
    parser.add_argument("--gen-model", default=None, help="覆盖生成模型名")
    parser.add_argument("--judge-model", default=None, help="覆盖 judge 模型名")
    parser.add_argument("--limit", type=int, default=0, help=">0 时取前 N 题做链路验证")
    parser.add_argument("--dataset", default=None, help="自定义评测集 JSON(含 question/reference_answer)，默认用内置 ALL_QUESTIONS")
    args = parser.parse_args()

    if args.judge_only:
        run_judge(args)
    elif args.generate_only:
        asyncio.run(run_generate(args))
    else:
        asyncio.run(run_full(args))


if __name__ == "__main__":
    main()
