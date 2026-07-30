# -*- coding: utf-8 -*-
"""中文模糊表达测试 - 口语化、含糊、错别字、缩写等"""
import json, sys, requests, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API_URL = "http://localhost:8001/api/v1/chat/ask"

QUESTIONS = [
    # 1. 口语化表达（用大白话问专业问题）
    {
        "q": "新来的供货商要交什么东西才能开始合作啊？",
        "type": "口语化",
        "expect": "理解为供应商准入资质要求"
    },
    # 2. 模糊指代（"那个"、"上次说的"）
    {
        "q": "那个物料分ABC三类的，最贵的那种要怎么管？",
        "type": "模糊指代",
        "expect": "理解为A类物料的管理策略"
    },
    # 3. 错别字 / 谐音
    {
        "q": "供应商绩效评价的ABCD挡，几份算不及格？",
        "type": "错别字",
        "expect": "挡→档，几份→几分，理解为D级分数线"
    },
    # 4. 缩写 / 行业黑话
    {
        "q": "IQC检出来不良品怎么处理？能让步接收吗？",
        "type": "行业术语",
        "expect": "理解为来料检验不合格品处理流程"
    },
    # 5. 不完整的问题
    {
        "q": "安全库存怎么算",
        "type": "不完整",
        "expect": "理解为安全库存计算公式"
    },
    # 6. 绕圈子问法
    {
        "q": "我们公司买东西是不是要领导批啊？多少钱的要批？",
        "type": "绕圈子",
        "expect": "理解为采购订单审批金额节点"
    },
    # 7. 多重模糊叠加（口语+不完整+指代不清）
    {
        "q": "库里的东西放太久算啥？多久算太久？",
        "type": "多重模糊",
        "expect": "理解为呆滞料定义和判定周期"
    },
    # 8. 反问 / 否定式提问
    {
        "q": "供应商是不是表现不好就会被踢掉？怎么才算表现不好？",
        "type": "反问式",
        "expect": "理解为供应商淘汰触发条件"
    },
    # 9. 极度口语化 + 情绪化
    {
        "q": "来货检查要多长时间啊？等太久了产线停工怎么办？",
        "type": "情绪化口语",
        "expect": "理解为IQC检验时效要求"
    },
    # 10. 含糊的比较
    {
        "q": "好的供应商和差的供应商有啥区别？",
        "type": "含糊比较",
        "expect": "理解为供应商评级A级vs D级的区别"
    },
]

print("=" * 72)
print("SupplyChainRAG - 中文模糊表达测试 (%d 题)" % len(QUESTIONS))
print("=" * 72)

total_time = 0
pass_count = 0
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
    
    # 截取回答显示（太长则截断）
    lines = answer.split("\n")
    if len(lines) > 12:
        for line in lines[:12]:
            print("  " + line)
        print("  ... (省略 %d 行)" % (len(lines) - 12))
    else:
        for line in lines:
            print("  " + line)
    
    if sources:
        src_names = list(set(s.get("source", "?") for s in sources[:4]))
        print("\n  来源: %s" % ", ".join(src_names))
    
    # 评估是否理解正确
    has_citation = "[" in answer and "]" in answer
    answer_len = len(answer)
    no_hallucination = "暂无" not in answer  # 这些问题应该有答案
    
    # 简单启发式评估
    if answer_len > 30 and has_citation and no_hallucination:
        verdict = "PASS"
        pass_count += 1
    elif answer_len > 30 and no_hallucination:
        verdict = "OK (无引用标注)"
        pass_count += 1
    else:
        verdict = "WARN"
    
    print("\n  评估: %s (长度=%d)" % (verdict, answer_len))

print("\n" + "=" * 72)
print("测试完成: %d/%d PASS | %d 题, 总耗时 %.1fs, 平均 %.1fs/题" % (
    pass_count, len(QUESTIONS), len(QUESTIONS), total_time, 
    total_time / len(QUESTIONS) if QUESTIONS else 0))
print("=" * 72)
