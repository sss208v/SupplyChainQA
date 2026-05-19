"""RAG 系统评估 — 实际可运行版本
测试: 响应成功率 / 回答长度 / 按类型分组 / 性能延迟
报告: retrieval_report.json + generation_report.json
"""
import httpx, json, os, time, asyncio, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
API = "http://localhost:8001/api/v1"
TEST_FILE = os.path.join(os.path.dirname(__file__), "test_dataset_v2.json")

with open(TEST_FILE, "r", encoding="utf-8") as f:
    test_set = json.load(f)

# 登录
resp = httpx.post(f"{API}/auth/login", json={"username":"admin","password":"admin123"}, timeout=5)
token = resp.json()["token"]
headers = {"Authorization": f"Bearer {token}"}

# ========== 检索评估 ==========
print(f"\n{'='*60}")
print(f"  RAG 检索阶段评估 — {len(test_set)} 条")
print(f"{'='*60}\n")

ret_stats = {"total": 0, "responded": 0, "avg_answer_len": 0, "avg_time_ms": 0}
by_type_responded = {}
by_type_len = {}
by_type_time = {}

for i, item in enumerate(test_set):
    question = item["question"]
    qtype = item.get("question_type", "unknown")
    _t0 = time.perf_counter()
    try:
        resp = httpx.post(f"{API}/chat/stream",
            json={"query": question, "stream": True},
            headers=headers, timeout=45)
        answer = ""
        for line in resp.text.split('\n'):
            if line.startswith('data:'):
                d = line[5:].strip()
                try:
                    j = json.loads(d)
                    if j.get('type') == 'content':
                        answer += j.get('content', '')
                except: pass
    except Exception as e:
        answer = ""
    _t = (time.perf_counter() - _t0) * 1000

    has_answer = len(answer) > 10
    ret_stats["total"] += 1
    ret_stats["avg_time_ms"] += _t
    by_type_time.setdefault(qtype, []).append(_t)
    if has_answer:
        ret_stats["responded"] += 1
        ret_stats["avg_answer_len"] += len(answer)
        by_type_responded.setdefault(qtype, []).append(1)
        by_type_len.setdefault(qtype, []).append(len(answer))
    else:
        by_type_responded.setdefault(qtype, []).append(0)
        by_type_len.setdefault(qtype, []).append(0)

    status = "OK" if has_answer else "NO"
    print(f"  [{i+1:2d}] {status:3s} {len(answer):5d}字 {_t:6.0f}ms | {qtype:10s} | {question[:45]}")

# 汇总
n = ret_stats["total"]
def avg(v): return round(sum(v) / len(v), 0) if v else 0

ret_report = {
    "total": n,
    "responded": ret_stats["responded"],
    "response_rate": round(ret_stats["responded"] / n, 4) if n else 0,
    "avg_answer_len": round(ret_stats["avg_answer_len"] / max(ret_stats["responded"], 1), 0),
    "avg_time_ms": round(ret_stats["avg_time_ms"] / n, 0) if n else 0,
    "by_type_response_rate": {
        t: round(sum(v) / len(v), 4) if v else 0
        for t, v in by_type_responded.items()
    },
    "by_type_avg_time": {t: round(avg(v)) for t, v in by_type_time.items()},
}

print(f"\n{'='*60}")
print(f"  【检索阶段指标】")
print(f"{'='*60}")
print(f"  条目: {ret_report['total']}")
print(f"  响应率: {ret_report['response_rate']:.1%} ({ret_report['responded']}/{ret_report['total']})")
print(f"  平均回答长度: {ret_report['avg_answer_len']:.0f} 字")
print(f"  平均延迟: {ret_report['avg_time_ms']:.0f} ms")
print(f"\n  按类型响应率:")
for t, v in sorted(ret_report["by_type_response_rate"].items()):
    bar = "#" * int(v * 20)
    print(f"    {t:12s}: {v:.1%} {bar}")
print(f"\n  按类型平均延迟:")
for t, v in sorted(ret_report["by_type_avg_time"].items()):
    print(f"    {t:12s}: {v:.0f} ms")
print(f"{'='*60}\n")

with open(os.path.join(os.path.dirname(__file__), "retrieval_report.json"), "w", encoding="utf-8") as f:
    json.dump(ret_report, f, ensure_ascii=False, indent=2)

# ========== 生成评估（仅对有回答的条目）==========
print(f"{'='*60}")
print(f"  生成阶段评估 — LLM-as-Judge（抽样 10 条）")
print(f"{'='*60}\n")

eval_items = [
    item for item in test_set
    if item.get("ground_truth_answer") and item["ground_truth_answer"] != "无法回答"
][:10]  # 抽样 10 条控制成本

if eval_items:
    try:
        from app.core.llm_router import LLMFactory
        from langchain_core.messages import HumanMessage
        llm = LLMFactory.get_llm(temperature=0.0, model="fast")

        JUDGE = """判断以下回答是否正确。标准答案：{ground_truth}\n模型回答：{prediction}\n评分0-1。只输出数字。"""

        async def judge(pred, gt):
            if not pred or not gt: return 0.0
            try:
                resp = await llm.ainvoke([HumanMessage(content=JUDGE.format(ground_truth=gt, prediction=pred[:2000]))])
                m = re.search(r"(\d+\.?\d*)", resp.content.strip())
                return float(m.group(1)) if m else 0.5
            except: return 0.5

        async def run_gen():
            scores = []
            for i, item in enumerate(eval_items):
                question = item["question"]
                gt = item["ground_truth_answer"]
            try:
                resp = httpx.post(f"{API}/chat/stream",
                    json={"query": question, "stream": True},
                    headers=headers, timeout=60)
                pred = ""
                for line in resp.text.split('\n'):
                    if line.startswith('data:'):
                        d = line[5:].strip()
                        try:
                            j = json.loads(d)
                            if j.get('type') == 'content':
                                pred += j.get('content', '')
                        except: pass
            except: pred = ""
            score = await judge(pred, gt)
            scores.append(score)
            bar = "#" * int(score * 10) + "-" * (10 - int(score * 10))
            print(f"  [{i+1:2d}] [{bar}] {score:.2f} | {question[:50]}")

        scores = asyncio.run(run_gen())
        gen_report = {"sampled": len(scores), "avg_score": round(sum(scores)/len(scores),4) if scores else 0}
        print(f"\n  抽样准确率: {gen_report['avg_score']:.4f} ({len(scores)} 条)")
        with open(os.path.join(os.path.dirname(__file__), "generation_report.json"), "w", encoding="utf-8") as f:
            json.dump(gen_report, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  LLM-as-Judge 跳过: {e}")
        gen_report = {"sampled": 0, "avg_score": 0, "note": str(e)}
else:
    gen_report = {"sampled": 0, "avg_score": 0}

# ========== 综合报告 ==========
full = {
    "date": time.strftime("%Y-%m-%d %H:%M"),
    "test_set_size": len(test_set),
    "retrieval": ret_report,
    "generation": gen_report,
}
with open(os.path.join(os.path.dirname(__file__), "eval_report.json"), "w", encoding="utf-8") as f:
    json.dump(full, f, ensure_ascii=False, indent=2)

print(f"\n{'='*60}")
print(f"  评估完成！")
print(f"  retrieval_report.json | generation_report.json | eval_report.json")
print(f"{'='*60}\n")
