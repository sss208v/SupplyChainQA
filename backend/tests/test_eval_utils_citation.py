# -*- coding: utf-8 -*-
"""strip_citation_tail 单测：评测送 judge 前的引用尾部剥离（P0-1 口径清洗）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.eval_utils import strip_citation_tail


def test_strip_citation_tail_normal():
    """正常路径：标题行 + 多条引用行整块剥离，正文行内 [n] 保留。"""
    resp = (
        "运费分摊文件归档保存期限不少于3年 [4]。\n"
        "引用：\n"
        "[4] SC-finance-530.md — 操作步骤及归档要求\n"
        "[1] SC-admin-366.md — 审批过程"
    )
    assert strip_citation_tail(resp) == "运费分摊文件归档保存期限不少于3年 [4]。"


def test_strip_citation_tail_no_citation_block():
    """无引用块：原文仅做首尾空白清理，行内 [n] 标注不受影响。"""
    resp = "处理时效达标率的目标值是≥90% [1]，考核周期是月度 [1]。"
    assert strip_citation_tail(resp) == resp


def test_strip_citation_tail_items_without_header():
    """只有 [n] 引用行、没有"引用："标题行，也应剥离。"""
    resp = "紧急采购须在3个工作日内补齐审批手续 [1]。\n[1] 采购审批权限与流程规范.md — 紧急采购"
    assert strip_citation_tail(resp) == "紧急采购须在3个工作日内补齐审批手续 [1]。"


def test_strip_citation_tail_blank_lines_between():
    """正文与引用块之间的空行一并清理。"""
    resp = "供应商分为四个等级 [1] [2]。\n\n引用：\n\n[1] 供应商准入与分级管理.md — 第八条\n\n[2] 供应商管理手册.md — 分级\n"
    assert strip_citation_tail(resp) == "供应商分为四个等级 [1] [2]。"


def test_strip_citation_tail_header_mid_text_not_removed():
    """正文中间的"引用："不构成尾部块时不误删（尾行是正文则整体保留）。"""
    resp = "引用：\n以上内容基于制度文件，保存期限不少于3年 [1]。"
    assert strip_citation_tail(resp) == resp.strip()


def test_strip_citation_tail_empty_input():
    """空输入：原样返回不抛错。"""
    assert strip_citation_tail("") == ""
    assert strip_citation_tail(None) is None


def test_strip_citation_tail_citation_only():
    """整段只有引用块（极端情况）：剥离后为空串。"""
    resp = "引用：\n[1] 文档A.md — 章节1"
    assert strip_citation_tail(resp) == ""
