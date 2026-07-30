# -*- coding: utf-8 -*-
"""实际问答测试 - 调用 /ask 端点"""
import json, sys, requests, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API_URL = "http://localhost:8001/api/v1/chat/ask"

QUESTIONS = [
    "供应商准入需要提供哪些资质文件？",
    "安全库存的计算公式是什么？",
    "供应商绩效评估ABCD等级的具体分数线是多少？",
    "A类物料和B类物料的抽检比例分别是多少？",
    "采购订单审批中，哪些金额节点需要特殊审批？",
    "库存预警的触发条件是什么？",
    "IQC来料检验的完整流程是什么？",
    "供应商被淘汰后多久可以重新申请准入？",
]

print("=" * 70)
print("SupplyChainRAG - 实际问答测试 (%d 题)" % len(QUESTIONS))
print("=" * 70)

total_time = 0
for i, q in enumerate(QUESTIONS):
    print("\n" + "-" * 70)
    print("[%d/%d] Q: %s" % (i + 1, len(QUESTIONS), q))
    print("-" * 70)
    
    t0 = time.time()
    try:
        r = requests.post(API_URL, json={"question": q}, timeout=120)
        data = r.json()
    except Exception as e:
        print("ERROR: %s" % e)
        continue
    elapsed = time.time() - t0
    total_time += elapsed
    
    answer = data.get("answer", "")
    sources = data.get("sources", [])
    conf = data.get("confidence", 0)
    ctx_used = data.get("context_used", 0)
    query_type = data.get("query_type", "?")
    
    print("耗时: %.1fs | 置信度: %.2f | 上下文: %d | 引用: %d | 路由: %s" % (
        elapsed, conf, ctx_used, len(sources), query_type))
    print()
    print("回答:")
    for line in answer.split("\n"):
        print("  " + line)
    
    if sources:
        print()
        print("引用来源 (Top 5):")
        for j, s in enumerate(sources[:5]):
            src = s.get("source", "?")
            score = s.get("score", 0)
            snip = s.get("snippet", "")[:60].replace("\n", " ")
            print("  [%d] %s (score=%.2f): %s..." % (j + 1, src, score, snip))

print()
print("=" * 70)
print("测试完成: %d 题, 总耗时 %.1fs, 平均 %.1fs/题" % (
    len(QUESTIONS), total_time, total_time / len(QUESTIONS) if QUESTIONS else 0))
print("=" * 70)
