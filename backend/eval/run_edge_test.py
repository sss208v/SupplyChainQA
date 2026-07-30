# -*- coding: utf-8 -*-
"""边界问题测试 - 考察 RAG 系统的鲁棒性"""
import json, sys, requests, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API_URL = "http://localhost:8001/api/v1/chat/ask"

# 精心设计的边界问题
QUESTIONS = [
    # 1. 知识库中不存在的问题（应该回答"暂无相关信息"）
    {
        "q": "供应商的年度营收需要达到多少才能准入？",
        "type": "不存在",
        "expect": "应回答暂无相关信息，或说明知识库中没有营收门槛要求"
    },
    # 2. 多跳推理（需要跨文档综合信息）
    {
        "q": "如果一个C级供应商连续两个季度没改善，最终会怎样？整个过程需要多长时间？",
        "type": "多跳推理",
        "expect": "C级→整改通知→30天整改→未改善→D级→淘汰，需串联多个流程节点"
    },
    # 3. 容易混淆的细节（AQL vs 抽检比例）
    {
        "q": "AQL 0.65 对应的是哪种缺陷等级？这个等级的物料抽检比例是多少？",
        "type": "细节区分",
        "expect": "AQL 0.65 对应 Major 主要缺陷，不应和 ABC 物料分类的抽检比例搞混"
    },
    # 4. 反向验证（问一个错误的说法让系统纠正）
    {
        "q": "听说安全库存系数C类物料是1.8，对吗？",
        "type": "纠偏",
        "expect": "应纠正：C类是1.2，A类才是1.8"
    },
    # 5. 模糊/宽泛的问题
    {
        "q": "供应链管理有什么风险？",
        "type": "宽泛",
        "expect": "应系统性地列出知识库中提到的各类风险，而不是泛泛而谈"
    },
    # 6. 涉及具体数字的精确查询
    {
        "q": "紧急采购金额在5000到5万之间时，谁来审批？时限多少？",
        "type": "精确数字",
        "expect": "总监审批，2小时内"
    },
    # 7. 条件分支问题
    {
        "q": "来料检验不合格有哪些处理方式？分别在什么条件下适用？",
        "type": "条件分支",
        "expect": "退货/让步接收/降级使用/全检，需说明各自适用条件"
    },
    # 8. 跨领域综合（采购+库存+质量交叉）
    {
        "q": "从采购下单到物料入库，整个链路涉及哪些关键节点和时效要求？",
        "type": "跨领域综合",
        "expect": "需串联采购审批→下单→供应商交货→IQC检验→入库，各环节时效"
    },
]

print("=" * 72)
print("SupplyChainRAG - 边界问题压力测试 (%d 题)" % len(QUESTIONS))
print("=" * 72)

total_time = 0
for i, item in enumerate(QUESTIONS):
    q = item["q"]
    qtype = item["type"]
    expect = item["expect"]
    
    print("\n" + "=" * 72)
    print("[%d/%d] 类型: %s" % (i + 1, len(QUESTIONS), qtype))
    print("Q: %s" % q)
    print("期望: %s" % expect)
    print("-" * 72)
    
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
    
    print("耗时: %.1fs | 置信度: %.2f | 上下文: %d | 引用: %d" % (
        elapsed, conf, ctx_used, len(sources)))
    print()
    print("回答:")
    for line in answer.split("\n"):
        print("  " + line)
    
    if sources:
        src_names = list(set(s.get("source", "?") for s in sources[:5]))
        print("\n  来源: %s" % ", ".join(src_names))
    
    # 简要评估
    has_citation = "[" in answer and "]" in answer
    says_no_info = "暂无" in answer or "没有找到" in answer or "知识库中暂无" in answer
    answer_len = len(answer)
    print()
    if qtype == "不存在":
        verdict = "PASS" if says_no_info or answer_len < 100 else "WARN - 可能产生了幻觉"
    elif qtype == "纠偏":
        verdict = "PASS" if "1.2" in answer or "C类" in answer else "WARN - 可能没有纠正"
    else:
        verdict = "PASS" if has_citation and answer_len > 50 else "WARN"
    print("  评估: %s (长度=%d, 有引用=%s)" % (verdict, answer_len, has_citation))

print("\n" + "=" * 72)
print("测试完成: %d 题, 总耗时 %.1fs, 平均 %.1fs/题" % (
    len(QUESTIONS), total_time, total_time / len(QUESTIONS) if QUESTIONS else 0))
print("=" * 72)
