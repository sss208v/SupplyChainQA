"""
SmartQA Pro - Guardrails 内容安全过滤模块
============================================================
【功能说明】
基于关键词匹配的轻量级内容安全过滤，不依赖外部AI模型。

1. 输入过滤（Input Guardrails）：
   - 白名单机制：检查用户问题是否属于供应链相关领域
   - 约50个供应链领域关键词覆盖采购、库存、质检、物流、仓储等
   - 如果不在范围内，拒绝请求并提示用户

2. 输出过滤（Output Guardrails）：
   - 黑名单机制：检查LLM回答是否包含有害/敏感内容
   - 覆盖暴力、色情、政治敏感等约20个敏感词
   - 如果包含敏感词，用 * 替换敏感部分

【设计思路】
- 纯关键词匹配，零外部依赖，毫秒级延迟
- 输入过滤用白名单（允许供应链领域），输出过滤用黑名单（禁止敏感内容）
- 模块级单例，避免重复初始化
============================================================
"""
import re
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


class GuardrailsEngine:
    """
    内容安全过滤引擎

    核心方法：
    - check_input(query) -> (allowed, reason): 输入域过滤
    - check_output(answer) -> (safe, filtered_answer): 输出安全过滤
    """

    # ---- 输入白名单：供应链领域关键词（约50个） ----
    SUPPLY_CHAIN_KEYWORDS: list[str] = [
        # 供应链核心
        "供应链", "supply chain", "供应链管理", "SCM",
        # 采购相关
        "采购", "采购管理", "采购订单", "采购流程", "采购合同", "招标", "比价",
        "供应商", "供应商管理", "供应商评估", "供应商准入", "供应商审核",
        # 库存相关
        "库存", "库存管理", "安全库存", "库存周转", "库存盘点", "在库", "备货",
        "入库", "出库", "仓存",
        # 仓储相关
        "仓储", "仓库", "仓储管理", "仓管", "货架", "库位", "仓储布局",
        # 物流相关
        "物流", "物流管理", "运输", "配送", "发货", "收货", "货运", "承运",
        "快递", "运单", "运费",
        # 质检相关
        "质检", "质量检测", "质量管理", "质量控制", "来料检验", "品质", "品控",
        "QA", "QC", "检验标准", "合格率",
        # ERP/系统相关
        "ERP", "MRP", "WMS", "TMS", "MES", "BOM", "工单", "生产工单",
        # 物料相关
        "物料", "物料管理", "物料清单", "物料编码", "原材料", "半成品", "成品",
        "SKU", "品类",
        # 生产制造相关
        "生产", "生产计划", "排产", "产能", "交期", "排程",
        # 订单相关
        "订单", "订单管理", "订单跟踪", "下单", "订单状态",
        # 需求相关
        "需求", "需求预测", "需求计划", "补货", "需求管理",
        # 制度规范
        "制度", "规范", "流程", "管理办法", "操作规程", "SOP",
        # 报表分析
        "报表", "数据分析", "统计", "KPI", "指标", "报表分析",
    ]

    # ---- 输出黑名单：敏感词（约20个类别） ----
    SENSITIVE_PATTERNS: list[str] = [
        # 暴力相关
        "杀人", "杀戮", "暴力", "炸弹", "爆炸", "枪击", "枪支", "武器",
        # 色情相关
        "色情", "淫秽", "裸体", "性爱", "黄色",
        # 政治敏感
        "颠覆", "政变", "恐怖主义", "恐怖袭击",
        # 违法相关
        "制毒", "贩毒", "赌博", "洗钱", "诈骗",
        # 自我伤害
        "自杀", "自残",
        # 歧视相关
        "种族歧视", "性别歧视",
        # 其他有害内容
        "邪教", "传销",
    ]

    def __init__(self):
        # 预编译正则：输入白名单匹配（忽略大小写）
        escaped = [re.escape(kw) for kw in self.SUPPLY_CHAIN_KEYWORDS]
        self._input_pattern = re.compile(
            "|".join(escaped), re.IGNORECASE
        )

        # 预编译正则：输出黑名单匹配
        escaped_sensitive = [re.escape(word) for word in self.SENSITIVE_PATTERNS]
        self._output_pattern = re.compile(
            "|".join(escaped_sensitive), re.IGNORECASE
        )

        logger.info(
            f"GuardrailsEngine 初始化完成 "
            f"(输入白名单={len(self.SUPPLY_CHAIN_KEYWORDS)}词, "
            f"输出黑名单={len(self.SENSITIVE_PATTERNS)}词)"
        )

    def check_input(self, query: str) -> Tuple[bool, str]:
        """
        输入过滤：检查用户问题是否属于供应链知识库范围

        使用白名单关键词匹配，只要query中包含任意一个供应链领域关键词即视为允许。
        问候语（你好、在吗等短句）也允许通过，交由后续意图路由处理。

        Args:
            query: 用户输入的问题

        Returns:
            (allowed, reason): allowed=True允许继续处理，
                              allowed=False拒绝并返回原因
        """
        if not query or not query.strip():
            return False, "问题不能为空"

        query_clean = query.strip()

        # 问候语/短句放行（交由意图路由处理）
        greeting_patterns = [
            "你好", "嗨", "hi", "hello", "在吗", "在不在",
            "谢谢", "感谢", "再见", "拜拜", "help", "帮助",
        ]
        if len(query_clean) <= 10:
            for g in greeting_patterns:
                if g in query_clean.lower():
                    return True, "问候语放行"

        # 关键词白名单匹配
        if self._input_pattern.search(query_clean):
            return True, "匹配供应链领域关键词"

        # 未匹配到任何关键词
        return False, "该问题不在供应链知识库范围内，请提问与供应链、采购、库存、质检、物流、仓储等相关的问题"

    def check_output(self, answer: str) -> Tuple[bool, str]:
        """
        输出过滤：检查LLM回答是否包含敏感/有害内容

        使用黑名单关键词匹配，如果回答中包含敏感词，用 * 替换。

        Args:
            answer: LLM生成的回答

        Returns:
            (safe, filtered_answer): safe=True表示原始回答安全，
                                     safe=False表示已做过滤，返回过滤后的回答
        """
        if not answer or not answer.strip():
            return True, answer

        # 检查是否包含敏感词
        if not self._output_pattern.search(answer):
            return True, answer

        # 包含敏感词：用 * 替换匹配的部分
        filtered = self._output_pattern.sub(
            lambda m: "*" * len(m.group()), answer
        )

        logger.warning(
            f"输出内容包含敏感词，已过滤。"
            f"原文长度={len(answer)}, 过滤后长度={len(filtered)}"
        )

        return False, filtered


# 模块级单例（避免重复创建正则表达式）
_guardrails_engine: GuardrailsEngine | None = None


def get_guardrails_engine() -> GuardrailsEngine:
    """获取 GuardrailsEngine 单例"""
    global _guardrails_engine
    if _guardrails_engine is None:
        _guardrails_engine = GuardrailsEngine()
    return _guardrails_engine


# ---- 便捷函数 ----

def check_input(query: str) -> Tuple[bool, str]:
    """快速输入过滤"""
    return get_guardrails_engine().check_input(query)


def check_output(answer: str) -> Tuple[bool, str]:
    """快速输出过滤"""
    return get_guardrails_engine().check_output(answer)


if __name__ == "__main__":
    # 测试
    engine = GuardrailsEngine()

    print("=== 输入过滤测试 ===")
    test_inputs = [
        ("库存管理的最佳实践是什么？", True),
        ("采购订单如何创建？", True),
        ("供应链中断应急方案", True),
        ("今天天气怎么样？", False),
        ("推荐一部好看的电影", False),
        ("你好", True),
        ("ERP系统配置流程", True),
        ("物料编码规则说明", True),
        ("怎么追女生？", False),
        ("如何提高库存周转率？", True),
    ]

    for query, expected in test_inputs:
        allowed, reason = engine.check_input(query)
        status = "✓" if allowed == expected else "✗"
        print(f"  {status} [{allowed}] \"{query}\" → {reason}")

    print("\n=== 输出过滤测试 ===")
    test_outputs = [
        ("库存管理是指对仓库中物资的进出存进行管理的过程", True),
        ("该产品含有暴力色情内容", False),
        ("物料清单BOM是一份详细的列表", True),
        ("这种做法涉及赌博和洗钱行为", False),
    ]

    for answer, expected_safe in test_outputs:
        safe, filtered = engine.check_output(answer)
        status = "✓" if safe == expected_safe else "✗"
        print(f"  {status} [safe={safe}] 原文: \"{answer}\"")
        if not safe:
            print(f"       过滤后: \"{filtered}\"")
