# -*- coding: utf-8 -*-
"""评测问题集 TEST_QA_PAIRS。

历史上此模块曾缺失（run_comprehensive_ragas.py 依赖它）。这里从既有的
eval_raw_data_comprehensive.json（含 user_input + reference）动态重建问题集，
排除 run_comprehensive_ragas.py 内联的 8 条 NEW_QUESTIONS，避免重复。

每条格式：{"question": ..., "reference_answer": ...}
collect_data() 仅使用 question 与 reference_answer 两个字段。
"""
import os
import json

_EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
_RAW_PATH = os.path.join(_EVAL_DIR, "eval_raw_data_comprehensive.json")

# 与 run_comprehensive_ragas.py 的 NEW_QUESTIONS 一致（脚本会另行追加，此处排除防重复）
_NEW_QUESTIONS = {
    "采购订单的审批流程是怎样的？",
    "来料检验不合格时如何处理？",
    "物料编码规则是什么？",
    "生产计划如何排程？",
    "物流发货的标准流程是什么？",
    "供应商等级评定的周期是多久？",
    "安全库存的计算公式是什么？",
    "跨部门协作的审批节点有哪些？",
}

TEST_QA_PAIRS = []
_seen = set()
if os.path.exists(_RAW_PATH):
    with open(_RAW_PATH, "r", encoding="utf-8") as _f:
        _raw = json.load(_f)
    for _r in _raw:
        _q = (_r.get("user_input") or "").strip()
        _ref = (_r.get("reference") or "").strip()
        if not _q or not _ref:
            continue
        if _q in _NEW_QUESTIONS or _q in _seen:
            continue
        _seen.add(_q)
        TEST_QA_PAIRS.append({"question": _q, "reference_answer": _ref})

if __name__ == "__main__":
    print(f"TEST_QA_PAIRS reconstructed: {len(TEST_QA_PAIRS)} pairs from {_RAW_PATH}")
    for i, p in enumerate(TEST_QA_PAIRS[:3]):
        print(f"  [{i+1}] {p['question']}")
