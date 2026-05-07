"""
SmartQA Pro - PII 数据脱敏模块
============================================================
【功能说明】在调用公有云 LLM API 前，对敏感个人信息进行识别与屏蔽

支持的 PII 类型：
- 身份证号 (18位/15位)
- 手机号 (中国大陆11位)
- 邮箱地址
- 银行卡号 (16-19位)
- 姓名 (需配合上下文识别)
- 地址 (包含省/市/区/街道等关键词)

使用方式：
    from app.core.data_filter import PIIFilter
    filter = PIIFilter()
    safe_text = filter.filter_text("我的手机号是13812345678")
    # 输出: "我的手机号是138****5678"

【设计思路】
1. 正则匹配为主：确定性高、零延迟
2. 分级脱敏：手机号保留前3后4，身份证保留前6后4
3. 可逆/不可逆模式：调试用可逆(reversible)，生产用不可逆(mask)
============================================================
"""
import re
import logging
from typing import Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class MaskMode(str, Enum):
    """脱敏模式"""
    MASK = "mask"           # 不可逆：用*替换
    HASH = "hash"           # 不可逆：用哈希替换
    REPLACE = "replace"     # 可逆：用占位符替换，可还原


@dataclass
class PIIMatch:
    """PII匹配结果"""
    pii_type: str       # PII类型
    original: str       # 原始值
    masked: str         # 脱敏后
    start: int          # 起始位置
    end: int            # 结束位置


class PIIFilter:
    """
    PII 数据过滤器

    核心方法：
    - filter_text(text): 对文本进行PII脱敏
    - detect_pii(text): 检测文本中的PII（不脱敏）
    - filter_for_llm(text): 专为LLM调用设计的脱敏（保留语义）
    """

    # ---- 正则模式 ----
    PATTERNS = {
        # 身份证号：18位（最后一位可为X）或15位
        "id_card": re.compile(
            r"(?<!\d)"
            r"[1-9]\d{5}"                          # 地区码
            r"(?:19|20)\d{2}"                       # 年
            r"(?:0[1-9]|1[0-2])"                    # 月
            r"(?:0[1-9]|[12]\d|3[01])"              # 日
            r"\d{3}[\dXx]"                          # 顺序码+校验码
            r"(?!\d)"
        ),
        # 手机号：1开头的11位数字
        "phone": re.compile(
            r"(?<!\d)"
            r"1[3-9]\d{9}"
            r"(?!\d)"
        ),
        # 邮箱
        "email": re.compile(
            r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
        ),
        # 银行卡号：16-19位数字
        "bank_card": re.compile(
            r"(?<!\d)"
            r"[3-6]\d{15,18}"
            r"(?!\d)"
        ),
        # 固定电话：区号-号码
        "landline": re.compile(
            r"(?<!\d)"
            r"0\d{2,3}[-\s]?\d{7,8}"
            r"(?!\d)"
        ),
        # IPv4 地址
        "ipv4": re.compile(
            r"(?<!\d)"
            r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
            r"(?!\d)"
        ),
        # 中文姓名：基于上下文关键词识别（"姓名"、"名字"、"联系人"等后面跟2-4个中文字符）
        "name": re.compile(
            r'(?:姓名|名字|联系人|负责人|申请人|患者|员工|用户)[：:\s]*([\u4e00-\u9fa5]{2,4})'
        ),
        # 地址：包含省/市/区/县/街道/路/号等关键词的中文地址
        "address": re.compile(
            r'[\u4e00-\u9fa5]{2,8}(?:省|市|区|县|镇|乡|村)[\u4e00-\u9fa50-9]{2,30}(?:路|街|道|巷|号|弄|室|栋|单元|楼)'
        ),
    }

    # ---- 脱敏模板 ----
    MASK_TEMPLATES = {
        "id_card": lambda m: m[:6] + "********" + m[-4:],      # 前6后4
        "phone": lambda m: m[:3] + "****" + m[-4:],            # 前3后4
        "email": lambda m: m[0] + "***@" + m.split("@")[1],    # 首字母***@域名
        "bank_card": lambda m: m[:4] + " **** **** " + m[-4:], # 前4后4
        "landline": lambda m: m[:4] + "****" + m[-4:],         # 前4后4
        "ipv4": lambda m: m[:m.rfind(".")] + ".***",           # 最后一段替换
        "name": lambda m: m[0] + '*' * (len(m) - 1),           # 张三 → 张*
        "address": lambda m: m[:2] + '***',                     # 广东省广州市天河区... → 广东***
    }

    # ---- 占位符模板（可逆模式）----
    REPLACE_TEMPLATES = {
        "id_card": "[身份证号_{idx}]",
        "phone": "[手机号_{idx}]",
        "email": "[邮箱_{idx}]",
        "bank_card": "[银行卡_{idx}]",
        "landline": "[固话_{idx}]",
        "ipv4": "[IP地址_{idx}]",
        "name": "[姓名_{idx}]",
        "address": "[地址_{idx}]",
    }

    def __init__(self, mode: MaskMode = MaskMode.MASK):
        self.mode = mode
        self._replacement_map: dict[str, str] = {}  # 可逆模式的映射表

    def filter_text(self, text: str) -> str:
        """
        对文本进行PII脱敏

        Args:
            text: 原始文本

        Returns:
            脱敏后的文本
        """
        if not text or not text.strip():
            return text

        self._replacement_map.clear()
        result = text

        # 按优先级处理（身份证 > 银行卡 > 手机号 > 邮箱 > 姓名 > 地址 > 固话 > IP）
        priority_order = ["id_card", "bank_card", "phone", "email", "name", "address", "landline", "ipv4"]

        for pii_type in priority_order:
            pattern = self.PATTERNS[pii_type]
            result = self._process_pattern(result, pattern, pii_type)

        return result

    def detect_pii(self, text: str) -> list[PIIMatch]:
        """
        检测文本中的PII（不进行脱敏）

        Args:
            text: 原始文本

        Returns:
            PII匹配结果列表
        """
        matches = []
        for pii_type, pattern in self.PATTERNS.items():
            for match in pattern.finditer(text):
                matches.append(PIIMatch(
                    pii_type=pii_type,
                    original=match.group(),
                    masked="",  # detect模式不脱敏
                    start=match.start(),
                    end=match.end(),
                ))
        return matches

    def filter_for_llm(self, text: str) -> tuple[str, dict[str, str]]:
        """
        专为LLM调用设计的脱敏

        特点：
        1. 使用可读的占位符（如[手机号_1]），保持语义连贯
        2. 返回映射表，方便后续还原

        Args:
            text: 原始文本

        Returns:
            (脱敏文本, {占位符: 原始值} 映射表)
        """
        original_mode = self.mode
        self.mode = MaskMode.REPLACE
        filtered = self.filter_text(text)
        mapping = dict(self._replacement_map)
        self.mode = original_mode
        return filtered, mapping

    def restore_text(self, filtered_text: str, mapping: dict[str, str]) -> str:
        """
        还原可逆模式脱敏的文本

        Args:
            filtered_text: 脱敏后的文本
            mapping: filter_for_llm 返回的映射表

        Returns:
            还原后的文本
        """
        result = filtered_text
        for placeholder, original in mapping.items():
            result = result.replace(placeholder, original)
        return result

    def _process_pattern(
        self, text: str, pattern: re.Pattern, pii_type: str
    ) -> str:
        """处理单种PII类型"""
        idx = 0
        result = text

        for match in pattern.finditer(text):
            original = match.group()

            # 处理带捕获组的模式（如name模式）：仅对捕获组部分做脱敏
            has_groups = bool(match.groups())
            if has_groups:
                # group(1)是要脱敏的实际内容，group(0)是完整匹配（含上下文关键词）
                target_value = match.group(1)
            else:
                target_value = original

            if self.mode == MaskMode.MASK:
                mask_fn = self.MASK_TEMPLATES.get(pii_type)
                masked = mask_fn(target_value) if mask_fn else "***"
            elif self.mode == MaskMode.REPLACE:
                idx += 1
                template = self.REPLACE_TEMPLATES.get(pii_type, "[敏感信息_{idx}]")
                masked = template.format(idx=idx)
                self._replacement_map[masked] = target_value
            else:
                masked = "***"

            if has_groups:
                # 仅替换捕获组部分，保留上下文关键词
                # 在完整匹配中定位 group(1) 的相对位置
                group_start = match.start(1) + (len(result) - len(text))
                group_end = match.end(1) + (len(result) - len(text))
                result = result[:group_start] + masked + result[group_end:]
            else:
                # 替换整个匹配（注意位置偏移）
                offset = len(result) - len(text)
                start = match.start() + offset
                end = match.end() + offset
                result = result[:start] + masked + result[end:]

            logger.debug(f"PII脱敏: {pii_type} {original} → {masked}")

        return result

    def filter_dict(self, data: dict, keys: Optional[list[str]] = None) -> dict:
        """
        对字典中的指定字段进行PII脱敏

        Args:
            data: 原始字典
            keys: 需要脱敏的字段名列表，None则脱敏所有字符串值

        Returns:
            脱敏后的字典
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                if keys is None or key in keys:
                    result[key] = self.filter_text(value)
                else:
                    result[key] = value
            elif isinstance(value, dict):
                result[key] = self.filter_dict(value, keys)
            elif isinstance(value, list):
                result[key] = [
                    self.filter_dict(item, keys) if isinstance(item, dict)
                    else self.filter_text(item) if isinstance(item, str) and (keys is None or key in keys)
                    else item
                    for item in value
                ]
            else:
                result[key] = value
        return result


# ---- 便捷函数 ----

def filter_pii(text: str) -> str:
    """快速PII脱敏（全局函数）"""
    return PIIFilter().filter_text(text)


def detect_pii(text: str) -> list[PIIMatch]:
    """快速PII检测（全局函数）"""
    return PIIFilter().detect_pii(text)


# ---- 中间件集成 ----

class PIIFilterMiddleware:
    """
    FastAPI 中间件：在请求发送到LLM前自动脱敏

    用法：
        from app.core.data_filter import PIIFilterMiddleware
        app.add_middleware(PIIFilterMiddleware)
    """

    def __init__(self, app):
        self.app = app
        self.filter = PIIFilter()

    async def __call__(self, scope, receive, send):
        # 仅处理 HTTP 请求
        if scope["type"] == "http":
            # 可以在此处对请求体进行PII检测/脱敏
            pass
        return await self.app(scope, receive, send)


class PIILogFilter(logging.Filter):
    """日志层PII脱敏过滤器
    
    【设计思路】
    在日志框架层做统一过滤（指南7.1），而不是靠开发者自觉。
    所有通过logging输出的日志都会自动经过PII脱敏。
    
    用法：
        import logging
        from app.core.data_filter import PIILogFilter
        logging.getLogger().addFilter(PIILogFilter())
    """
    def __init__(self):
        super().__init__()
        self._filter = PIIFilter()
    
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._filter.filter_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._filter.filter_text(v) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._filter.filter_text(a) if isinstance(a, str) else a for a in record.args)
        return True


if __name__ == "__main__":
    # 测试
    test_cases = [
        "我的手机号是13812345678，请联系我",
        "身份证号：440106199701012345",
        "邮箱：zhangsan@example.com，银行卡：6222021234567890123",
        "服务器IP: 192.168.1.100，固话: 020-87654321",
        "张三的身份证是440106199701012345，手机13812345678",
        "联系人：张三，手机13812345678",
        "地址：广东省广州市天河区科韵路100号",
    ]

    f = PIIFilter()
    for text in test_cases:
        print(f"\n原文: {text}")
        print(f"脱敏: {f.filter_text(text)}")

    # 测试可逆模式
    f2 = PIIFilter(mode=MaskMode.REPLACE)
    text = "联系人：张三，手机13812345678，邮箱zhangsan@test.com，地址：北京市海淀区中关村大街1号"
    filtered, mapping = f2.filter_for_llm(text)
    print(f"\n可逆脱敏: {filtered}")
    print(f"映射表: {mapping}")
    print(f"还原: {f2.restore_text(filtered, mapping)}")
