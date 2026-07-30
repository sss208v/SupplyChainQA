# -*- coding: utf-8 -*-
"""strip_non_factual_frame 单测：送 judge 前的非事实框架句剥离（P0-A 口径清洗）。

严格保守边界：只削首尾框架/套话，不动中间事实、数字、引用编号；
无答案题的正式答案（含"暂无相关信息/未明确提及"）整体不削。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.eval_utils import strip_citation_tail, strip_non_factual_frame


def test_strip_opening_frame():
    """开场框架句剥离，事实主体与引用编号保留。"""
    resp = "以下是安全库存的计算方法：安全库存 = 日均消耗量 × 采购周期 × 1.5 [1]。"
    assert strip_non_factual_frame(resp) == "安全库存 = 日均消耗量 × 采购周期 × 1.5 [1]。"


def test_strip_leading_marker():
    """行首话语标记剥离，其后事实保留。"""
    resp = "根据参考资料，PO 单据编号规则为 PO-YYYYMMDD-序号 [1]。"
    assert strip_non_factual_frame(resp) == "PO 单据编号规则为 PO-YYYYMMDD-序号 [1]。"


def test_strip_closing_filler():
    """冗余收尾套话剥离，正文不动。"""
    resp = "质量合格率的权重是40% [5]。希望以上信息对您有帮助。"
    assert strip_non_factual_frame(resp) == "质量合格率的权重是40% [5]。"


def test_closing_filler_ruhao_question():
    """『如有其他问题欢迎咨询』类收尾剥离。"""
    resp = "供应商分为四个等级 [1]。如有其他问题欢迎咨询。"
    assert strip_non_factual_frame(resp) == "供应商分为四个等级 [1]。"


def test_noinfo_answer_not_stripped():
    """无答案题的正式答案（暂无相关信息）整体不削。"""
    resp = "这个问题知识库中暂无相关信息。"
    assert strip_non_factual_frame(resp) == resp


def test_noinfo_未明确提及_not_stripped():
    """含『未明确提及』的诚实无答案句整体不削（即便句首像话语标记）。"""
    resp = "根据参考资料，该供应商供应的物料未明确提及。"
    assert strip_non_factual_frame(resp) == resp


def test_plain_fact_unchanged():
    """纯事实答案无框架句时原样返回（仅首尾空白清理）。"""
    resp = "物料 MAT-001 由供应商 东莞精密轴承有限公司 供应 [1]。"
    assert strip_non_factual_frame(resp) == resp


def test_ruxu_fact_not_mistaken_as_filler():
    """正文中的『如需…』事实句不被当收尾套话误删。"""
    resp = "如需变更物料编码，供应链管理部需在3个工作日内完成审核 [2]。"
    assert strip_non_factual_frame(resp) == resp


def test_error_passthrough():
    """ERROR answer 透传不处理。"""
    assert strip_non_factual_frame("ERROR: timeout") == "ERROR: timeout"


def test_empty_input():
    """空输入原样返回不抛错。"""
    assert strip_non_factual_frame("") == ""
    assert strip_non_factual_frame(None) is None


def test_compose_with_citation_tail():
    """与 strip_citation_tail 组合：先剥引用块、再剥框架句。"""
    resp = (
        "以下是解答：紧急采购须在3个工作日内补齐审批手续 [1]。\n"
        "引用：\n"
        "[1] 采购审批权限与流程规范.md — 紧急采购"
    )
    cleaned = strip_non_factual_frame(strip_citation_tail(resp))
    assert cleaned == "紧急采购须在3个工作日内补齐审批手续 [1]。"


def test_middle_content_with_yixiashi_not_stripped():
    """正文中间（非开头）的『以下…』结构不被误删。"""
    resp = "新供应商准入流程包括以下三步：资质审查、样品测试、现场审核 [4]。"
    assert strip_non_factual_frame(resp) == resp
