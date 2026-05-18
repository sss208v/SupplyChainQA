"""生成阶段评估 — 通过 /chat/stream API 获取回答，LLM-as-Judge 评分"""
import httpx, json, os, time, asyncio, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.core.llm_router import LLMFactory
from langchain_core.messages import HumanMessage

API = "http://localhost:8001/api/v1"
TEST_FILE = os.path.join(os.path.dirname(__file__), "test_dataset_v2.json")

JUDGE_PROMPT = """判断以下回答是否正确回答了问题。标准答案：{ground_truth}\n模型回答：{prediction}\n评分（0-1）：1.0完全正确 0.7基本正确 0.3部分正确 0.0错误\n只输出数字。"""

async def judge(prediction, ground_truth, llm):
    if not prediction or not ground_truth:
        return 0.0
    prompt = JUDGE_PROMPT.format(ground_truth=ground_truth, prediction=prediction[:2000])
    try:
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        match = re.search(r"(\d+\.?\d*)", resp.content.strip())
        return float(match.group(1)) if match else 0.5
    except Exception:
        return 0.5

async def main():
    with open(TEST_FILE, "r", encoding="utf-8") as f:
        test_set = json.load(f)
    resp = httpx.post(f"{API}/auth/login", json={"username":"admin","password":"admin123"}, timeout=5)
    token = resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    eval_items = [item for item in test_set if item.get("ground_truth_answer") != "无法回答"]
    llm = LLMFactory.get_llm(temperature=0.0, model="fast")
    scores, by_type = [], {}

    print(f"\n{'='*60}")
    print(f"  生成阶段评估 — {len(eval_items)} 条（LLM-as-Judge）")
    print(f"{'='*60}\n")

    for i, item in enumerate(eval_items):
        question = item["question"]
        qtype = item.get("question_type", "unknown")
        gt_answer = item["ground_truth_answer"]
        _t0 = time.perf_counter()
        try:
            resp = httpx.post(f"{API}/chat/stream",
                json={"query": question, "stream": True},
                headers=headers, timeout=60)
            prediction = ""
            for line in resp.text.split('\n'):
                if line.startswith('data:'):
                    d = line[5:].strip()
                    try:
                        j = json.loads(d)
                        if j.get('type') == 'content':
                            prediction = j.get('content', '')
                    except: pass
        except Exception as e:
            print(f"  [{i+1}] ERR: {e}")
            continue
        _t = (time.perf_counter() - _t0) * 1000

        score = await judge(prediction, gt_answer, llm)
        scores.append(score)
        by_type.setdefault(qtype, []).append(score)
        bar = "#" * int(score * 10) + "-" * (10 - int(score * 10))
        print(f"  [{i+1:2d}] [{bar}] {score:.2f} {_t:.0f}ms | {question[:50]}")

    def avg(v): return round(sum(v) / len(v), 4) if v else 0.0

    report = {
        "evaluated": len(scores),
        "overall_accuracy": avg(scores),
        "by_question_type": {t: avg(v) for t, v in by_type.items()},
    }
    print(f"\n{'='*60}")
    print(f"  【生成阶段指标】")
    print(f"{'='*60}")
    print(f"  评估: {report['evaluated']}")
    print(f"  准确率: {report['overall_accuracy']:.4f}")
    for t, v in report["by_question_type"].items():
        print(f"  {t:12s}: {v:.4f}")
    print(f"{'='*60}\n")

    path = os.path.join(os.path.dirname(__file__), "generation_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"报告: {path}")

if __name__ == "__main__":
    asyncio.run(main())
