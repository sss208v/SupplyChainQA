"""
Comprehensive tests for the PII data filter module (app.core.data_filter).

Covers:
- All PII types: phone, id_card, email, bank_card, landline, ipv4, name, address
- Edge cases: empty strings, whitespace, no-PII text, PII at boundaries
- Detection: detect_pii returns correct PIIMatch objects
- LLM integration: filter_for_llm placeholders + restore_text round-trip
- Dict filtering: key selection, nested dicts, lists
- Log filtering: PIILogFilter on msg and args
"""

import logging
import logging.handlers
import pytest

from app.core.data_filter import (
    PIIFilter,
    PIILogFilter,
    PIIMatch,
    MaskMode,
)


# ===================================================================
# 1. TestPIIFilterPhone - phone number masking
# ===================================================================
class TestPIIFilterPhone:
    """Phone number masking: 1[3-9]X XXXX XXXX -> 1XX****XXXX"""

    def setup_method(self):
        self.f = PIIFilter()

    def test_standard_phone(self):
        """Standard 11-digit Chinese mobile number."""
        result = self.f.filter_text("我的手机号是13812345678")
        assert result == "我的手机号是138****5678"

    def test_phone_at_start(self):
        result = self.f.filter_text("13812345678是我的手机号")
        assert result == "138****5678是我的手机号"

    def test_phone_at_end(self):
        result = self.f.filter_text("请拨打13812345678")
        assert result == "请拨打138****5678"

    def test_phone_standalone(self):
        """Phone number as the entire string."""
        result = self.f.filter_text("13812345678")
        assert result == "138****5678"

    def test_12_digit_should_not_match(self):
        """12-digit number must NOT be matched as a phone."""
        text = "编号138123456789"
        assert self.f.filter_text(text) == text

    def test_10_digit_should_not_match(self):
        """10-digit number must NOT be matched as a phone."""
        text = "编号1381234567"
        assert self.f.filter_text(text) == text

    def test_phone_starting_with_12_should_not_match(self):
        """12XXXXXXXXX is not a valid phone (1[3-9] pattern)."""
        text = "编号12812345678"
        assert self.f.filter_text(text) == text

    def test_phone_starting_with_10_should_not_match(self):
        text = "编号10812345678"
        assert self.f.filter_text(text) == text

    def test_multiple_phones(self):
        result = self.f.filter_text("手机13812345678和13987654321")
        assert "138****5678" in result
        assert "139****4321" in result

    def test_phone_with_surrounding_digits_not_matched(self):
        """Digits adjacent to the phone number prevent matching (lookbehind/lookahead)."""
        text = "编号913812345678"
        assert self.f.filter_text(text) == text


# ===================================================================
# 2. TestPIIFilterIdCard - ID card masking
# ===================================================================
class TestPIIFilterIdCard:
    """ID card masking: 18-digit -> first 6 + ******** + last 4."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_standard_18_digit(self):
        result = self.f.filter_text("身份证号：440106199701012345")
        assert result == "身份证号：440106********2345"

    def test_id_card_with_x_suffix(self):
        """Last digit can be X (checksum)."""
        result = self.f.filter_text("身份证：11010119900307123X")
        assert result == "身份证：110101********123X"

    def test_id_card_with_lowercase_x(self):
        """Lowercase x is also accepted by the regex."""
        result = self.f.filter_text("身份证：11010119900307123x")
        assert result == "身份证：110101********123x"

    def test_15_digit_old_format_not_matched(self):
        """
        15-digit old-format ID card does NOT match the current regex.
        The regex requires a 4-digit year (19xx/20xx) and 18 total digits,
        so a 15-digit string like 440106970101234 cannot match.
        """
        text = "旧身份证：440106970101234"
        assert self.f.filter_text(text) == text

    def test_id_card_standalone(self):
        result = self.f.filter_text("440106199701012345")
        assert result == "440106********2345"

    def test_id_card_preserves_prefix_and_suffix(self):
        """Verify the mask: first 6 chars + 8 asterisks + last 4 chars."""
        result = self.f.filter_text("320106200012251234")
        assert result == "320106********1234"


# ===================================================================
# 3. TestPIIFilterEmail - email masking
# ===================================================================
class TestPIIFilterEmail:
    """Email masking: first_char + ***@ + domain."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_standard_email(self):
        result = self.f.filter_text("邮箱：zhangsan@example.com")
        assert result == "邮箱：z***@example.com"

    def test_email_standalone(self):
        result = self.f.filter_text("zhangsan@example.com")
        assert result == "z***@example.com"

    def test_email_single_char_local(self):
        result = self.f.filter_text("a@test.com")
        assert result == "a***@test.com"

    def test_email_with_dots_in_local(self):
        result = self.f.filter_text("john.doe@company.org")
        assert result == "j***@company.org"

    def test_email_with_subdomain(self):
        result = self.f.filter_text("user@mail.example.com")
        assert result == "u***@mail.example.com"

    def test_email_in_sentence(self):
        result = self.f.filter_text("请发邮件到admin@test.com确认")
        assert result == "请发邮件到a***@test.com确认"


# ===================================================================
# 4. TestPIIFilterBankCard - bank card masking
# ===================================================================
class TestPIIFilterBankCard:
    """Bank card masking (16-19 digits starting with 3-6):
    first4 + ' **** **** ' + last4."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_16_digit_card(self):
        result = self.f.filter_text("银行卡：6222021234567890")
        assert result == "银行卡：6222 **** **** 7890"

    def test_19_digit_card(self):
        result = self.f.filter_text("卡号6222021234567890123")
        assert result == "卡号6222 **** **** 0123"

    def test_17_digit_card(self):
        result = self.f.filter_text("卡号41234567890123456")
        assert result == "卡号4123 **** **** 3456"

    def test_15_digit_not_matched(self):
        """15-digit number is below the bank card minimum (16)."""
        text = "编号622202123456789"
        assert self.f.filter_text(text) == text

    def test_20_digit_not_matched(self):
        """20-digit number exceeds the bank card maximum (19)."""
        text = "编号62220212345678901234"
        assert self.f.filter_text(text) == text

    def test_card_starting_with_7_not_matched(self):
        """Bank card regex requires the leading digit to be 3-6."""
        text = "编号7222021234567890"
        assert self.f.filter_text(text) == text


# ===================================================================
# 5. TestPIIFilterLandline - landline phone masking
# ===================================================================
class TestPIIFilterLandline:
    """Landline masking: 0XX-XXXXXXXX -> first4 + **** + last4."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_standard_landline_with_dash(self):
        result = self.f.filter_text("固话：020-87654321")
        assert result == "固话：020-****4321"

    def test_landline_without_separator(self):
        result = self.f.filter_text("电话02087654321")
        assert result == "电话0208****4321"

    def test_landline_4_digit_area_code(self):
        result = self.f.filter_text("电话0755-12345678")
        assert result == "电话0755****5678"

    def test_landline_standalone(self):
        result = self.f.filter_text("020-87654321")
        assert result == "020-****4321"


# ===================================================================
# 6. TestPIIFilterIPv4 - IP address masking
# ===================================================================
class TestPIIFilterIPv4:
    """IPv4 masking: replace last octet with ***."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_standard_ipv4(self):
        result = self.f.filter_text("服务器IP：192.168.1.100")
        assert result == "服务器IP：192.168.1.***"

    def test_ipv4_standalone(self):
        result = self.f.filter_text("10.0.0.1")
        assert result == "10.0.0.***"

    def test_ipv4_in_sentence(self):
        result = self.f.filter_text("访问172.16.254.1失败")
        assert result == "访问172.16.254.***失败"

    def test_ipv4_all_octets_high(self):
        result = self.f.filter_text("地址255.255.255.0")
        assert result == "地址255.255.255.***"


# ===================================================================
# 7. TestPIIFilterName - Chinese name with context keyword
# ===================================================================
class TestPIIFilterName:
    """Name masking (requires context keyword):
    keyword + Chinese name -> keyword + first_char + *..."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_contact_person_2char_name(self):
        result = self.f.filter_text("联系人：张三")
        assert result == "联系人：张*"

    def test_name_keyword_3char(self):
        result = self.f.filter_text("姓名：欧阳明华")
        assert result == "姓名：欧***"

    def test_responsible_person(self):
        result = self.f.filter_text("负责人：李四")
        assert result == "负责人：李*"

    def test_applicant(self):
        result = self.f.filter_text("申请人：王五")
        assert result == "申请人：王*"

    def test_patient(self):
        result = self.f.filter_text("患者：赵六")
        assert result == "患者：赵*"

    def test_employee(self):
        result = self.f.filter_text("员工：陈晓明")
        assert result == "员工：陈**"

    def test_user_keyword(self):
        result = self.f.filter_text("用户：刘伟")
        assert result == "用户：刘*"

    def test_name_without_context_not_matched(self):
        """A Chinese name without a preceding keyword is NOT masked."""
        text = "今天张三来了"
        assert self.f.filter_text(text) == text

    def test_name_keyword_with_space(self):
        result = self.f.filter_text("姓名 张三")
        assert result == "姓名 张*"

    def test_name_keyword_with_colon(self):
        result = self.f.filter_text("姓名:李四")
        assert result == "姓名:李*"


# ===================================================================
# 8. TestPIIFilterAddress - Chinese address masking
# ===================================================================
class TestPIIFilterAddress:
    """Address masking: first 2 characters + ***."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_standard_address(self):
        # The regex greedily matches through "号" (a valid ending keyword),
        # so the full address "广东省广州市天河区科韵路100号" is consumed.
        # mask: m[:2] + "***" replaces the entire match.
        result = self.f.filter_text("地址：广东省广州市天河区科韵路100号")
        assert result == "地址：广东***"

    def test_address_with_shi(self):
        # Same greedy behavior: "北京市海淀区中关村大街1号" fully matched.
        result = self.f.filter_text("北京市海淀区中关村大街1号")
        assert result == "北京***"

    def test_address_standalone(self):
        result = self.f.filter_text("广东省广州市天河区科韵路100号")
        assert result == "广东***"

    def test_plain_text_not_matched(self):
        text = "今天天气很好"
        assert self.f.filter_text(text) == text


# ===================================================================
# 9. TestPIIFilterMultiple - text with multiple PII types
# ===================================================================
class TestPIIFilterMultiple:
    """Multiple PII types in a single text should all be masked."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_phone_and_id_card(self):
        text = "张三的身份证是440106199701012345，手机13812345678"
        result = self.f.filter_text(text)
        assert "440106********2345" in result
        assert "138****5678" in result
        assert "440106199701012345" not in result
        assert "13812345678" not in result

    def test_email_and_bank_card(self):
        text = "邮箱：zhangsan@example.com，银行卡：6222021234567890123"
        result = self.f.filter_text(text)
        assert "z***@example.com" in result
        assert "6222 **** **** 0123" in result

    def test_landline_and_ipv4(self):
        text = "服务器IP: 192.168.1.100，固话: 020-87654321"
        result = self.f.filter_text(text)
        assert "192.168.1.***" in result
        assert "020-****4321" in result

    def test_name_and_phone(self):
        text = "联系人：张三，手机13812345678"
        result = self.f.filter_text(text)
        assert "张*" in result
        assert "138****5678" in result
        assert "张三" not in result
        assert "13812345678" not in result

    def test_address_and_phone(self):
        # Address regex greedily matches through "号", consuming the full address.
        text = "地址：广东省广州市天河区科韵路100号，电话13812345678"
        result = self.f.filter_text(text)
        assert "广东***" in result
        assert "138****5678" in result

    def test_all_originals_removed(self):
        """No raw PII should survive filtering."""
        text = "联系人：张三，手机13812345678，邮箱test@mail.com"
        result = self.f.filter_text(text)
        assert "13812345678" not in result
        assert "test@mail.com" not in result


# ===================================================================
# 10. TestPIIFilterEdgeCases
# ===================================================================
class TestPIIFilterEdgeCases:
    """Edge cases: empty, whitespace, no PII, PII at boundaries."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_empty_string(self):
        assert self.f.filter_text("") == ""

    def test_whitespace_only(self):
        assert self.f.filter_text("   ") == "   "

    def test_none_like_empty(self):
        """An empty string (falsy) is returned as-is."""
        assert self.f.filter_text("") == ""

    def test_no_pii_text(self):
        text = "今天天气真好，适合出去散步"
        assert self.f.filter_text(text) == text

    def test_pii_at_very_start(self):
        result = self.f.filter_text("13812345678联系我")
        assert result.startswith("138****5678")

    def test_pii_at_very_end(self):
        result = self.f.filter_text("联系我13812345678")
        assert result.endswith("138****5678")

    def test_only_pii(self):
        assert self.f.filter_text("13812345678") == "138****5678"

    def test_newlines_preserved(self):
        text = "第一行\n手机号13812345678\n第三行"
        result = self.f.filter_text(text)
        assert "138****5678" in result
        assert "\n" in result

    def test_tab_preserved(self):
        text = "手机号\t13812345678"
        result = self.f.filter_text(text)
        assert "138****5678" in result

    def test_single_newline_only(self):
        """A single newline is not whitespace-only per strip(), but has no PII."""
        assert self.f.filter_text("\n") == "\n"


# ===================================================================
# 11. TestDetectPII - detect_pii returns correct PIIMatch objects
# ===================================================================
class TestDetectPII:
    """detect_pii should identify PII without modifying the text."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_detect_phone(self):
        matches = self.f.detect_pii("请拨打13812345678联系我")
        phones = [m for m in matches if m.pii_type == "phone"]
        assert len(phones) == 1
        m = phones[0]
        assert m.original == "13812345678"
        assert m.start == 3
        assert m.end == 14

    def test_detect_id_card(self):
        matches = self.f.detect_pii("身份证号440106199701012345")
        ids = [m for m in matches if m.pii_type == "id_card"]
        assert len(ids) == 1
        m = ids[0]
        assert m.original == "440106199701012345"
        assert m.start == 4
        assert m.end == 22

    def test_detect_email(self):
        matches = self.f.detect_pii("邮箱zhangsan@example.com")
        emails = [m for m in matches if m.pii_type == "email"]
        assert len(emails) == 1
        m = emails[0]
        assert m.original == "zhangsan@example.com"
        assert m.start == 2
        assert m.end == 22

    def test_detect_name(self):
        matches = self.f.detect_pii("联系人：张三")
        names = [m for m in matches if m.pii_type == "name"]
        assert len(names) == 1
        m = names[0]
        # Full match includes the context keyword
        assert m.original == "联系人：张三"
        assert m.start == 0
        assert m.end == 6

    def test_detect_no_pii(self):
        matches = self.f.detect_pii("今天天气很好")
        assert matches == []

    def test_detect_multiple_types(self):
        text = "身份证440106199701012345，手机13812345678，邮箱zhangsan@example.com"
        matches = self.f.detect_pii(text)
        types_found = {m.pii_type for m in matches}
        assert "id_card" in types_found
        assert "phone" in types_found
        assert "email" in types_found

    def test_detect_returns_pii_match_instances(self):
        matches = self.f.detect_pii("手机13812345678")
        assert all(isinstance(m, PIIMatch) for m in matches)

    def test_detect_masked_is_empty(self):
        """In detect mode, masked field is always empty string."""
        matches = self.f.detect_pii("手机13812345678")
        for m in matches:
            assert m.masked == ""

    def test_detect_ipv4(self):
        matches = self.f.detect_pii("IP: 10.0.0.1")
        ips = [m for m in matches if m.pii_type == "ipv4"]
        assert len(ips) == 1
        assert ips[0].original == "10.0.0.1"


# ===================================================================
# 12. TestFilterForLLM - placeholder text + mapping + restore
# ===================================================================
class TestFilterForLLM:
    """filter_for_llm uses readable placeholders; restore_text reverses them."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_phone_placeholder(self):
        filtered, mapping = self.f.filter_for_llm("手机13812345678")
        assert "[手机号_1]" in filtered
        assert mapping["[手机号_1]"] == "13812345678"

    def test_email_placeholder(self):
        filtered, mapping = self.f.filter_for_llm("邮箱test@example.com")
        assert "[邮箱_" in filtered
        assert "test@example.com" in mapping.values()

    def test_restore_single(self):
        original = "手机13812345678"
        filtered, mapping = self.f.filter_for_llm(original)
        restored = self.f.restore_text(filtered, mapping)
        assert restored == original

    def test_restore_multiple(self):
        original = "联系人：张三，手机13812345678，邮箱zhangsan@test.com"
        filtered, mapping = self.f.filter_for_llm(original)
        restored = self.f.restore_text(filtered, mapping)
        assert restored == original

    def test_mapping_keys_are_placeholders(self):
        _, mapping = self.f.filter_for_llm("手机13812345678，邮箱a@b.com")
        for key in mapping:
            assert key.startswith("[")
            assert key.endswith("]")

    def test_empty_mapping_when_no_pii(self):
        filtered, mapping = self.f.filter_for_llm("今天天气很好")
        assert filtered == "今天天气很好"
        assert mapping == {}

    def test_mode_is_restored_after_call(self):
        """filter_for_llm temporarily uses REPLACE mode, then restores."""
        assert self.f.mode == MaskMode.MASK
        self.f.filter_for_llm("手机13812345678")
        assert self.f.mode == MaskMode.MASK

    def test_id_card_placeholder(self):
        filtered, mapping = self.f.filter_for_llm("身份证440106199701012345")
        assert "[身份证号_" in filtered
        assert "440106199701012345" in mapping.values()

    def test_name_placeholder_preserves_keyword(self):
        filtered, mapping = self.f.filter_for_llm("联系人：张三")
        assert "联系人：" in filtered
        assert "[姓名_" in filtered
        assert "张三" in mapping.values()


# ===================================================================
# 13. TestFilterDict - filter specified keys, nested dicts, lists
# ===================================================================
class TestFilterDict:
    """filter_dict processes dict values selectively."""

    def setup_method(self):
        self.f = PIIFilter()

    def test_filter_specific_keys(self):
        data = {
            "name": "张三",
            "phone": "13812345678",
            "title": "工程师",
        }
        result = self.f.filter_dict(data, keys=["phone"])
        assert result["phone"] == "138****5678"
        assert result["title"] == "工程师"
        # "name" is not in keys, so not filtered (and no context keyword)
        assert result["name"] == "张三"

    def test_filter_all_keys_none(self):
        """When keys=None, all string values are filtered."""
        data = {
            "phone": "13812345678",
            "info": "邮箱test@example.com",
        }
        result = self.f.filter_dict(data)
        assert "138****5678" in result["phone"]
        assert "t***@example.com" in result["info"]

    def test_nested_dict(self):
        data = {
            "user": {
                "phone": "13812345678",
                "name": "普通文本",
            }
        }
        result = self.f.filter_dict(data)
        assert result["user"]["phone"] == "138****5678"
        assert result["user"]["name"] == "普通文本"

    def test_list_of_dicts(self):
        data = {
            "contacts": [
                {"phone": "13812345678"},
                {"phone": "13987654321"},
            ]
        }
        result = self.f.filter_dict(data)
        assert result["contacts"][0]["phone"] == "138****5678"
        assert result["contacts"][1]["phone"] == "139****4321"

    def test_list_of_strings(self):
        data = {"messages": ["手机13812345678", "没有问题"]}
        result = self.f.filter_dict(data)
        assert "138****5678" in result["messages"][0]
        assert result["messages"][1] == "没有问题"

    def test_non_string_values_preserved(self):
        data = {
            "phone": "13812345678",
            "age": 30,
            "active": True,
            "score": 3.14,
        }
        result = self.f.filter_dict(data)
        assert result["phone"] == "138****5678"
        assert result["age"] == 30
        assert result["active"] is True
        assert result["score"] == 3.14

    def test_empty_dict(self):
        assert self.f.filter_dict({}) == {}

    def test_mixed_list(self):
        """Lists can contain dicts, strings, and other types."""
        data = {
            "items": [
                {"phone": "13812345678"},
                "邮箱a@b.com",
                42,
                True,
            ]
        }
        result = self.f.filter_dict(data)
        assert result["items"][0]["phone"] == "138****5678"
        assert "a***@b.com" in result["items"][1]
        assert result["items"][2] == 42
        assert result["items"][3] is True

    def test_filter_dict_with_keys_nested(self):
        """keys parameter propagates to nested dicts."""
        data = {
            "contact": {"phone": "13812345678", "note": "手机13987654321"},
        }
        result = self.f.filter_dict(data, keys=["phone"])
        assert result["contact"]["phone"] == "138****5678"
        # "note" is not in keys, so it stays as-is
        assert result["contact"]["note"] == "手机13987654321"

    def test_none_value_preserved(self):
        data = {"phone": "13812345678", "email": None}
        result = self.f.filter_dict(data)
        assert result["phone"] == "138****5678"
        assert result["email"] is None


# ===================================================================
# 14. TestPIILogFilter - log record msg and args are filtered
# ===================================================================
class TestPIILogFilter:
    """PIILogFilter masks PII in logging record msg and args."""

    def setup_method(self):
        self.log_filter = PIILogFilter()

    def _make_record(self, msg, args=None):
        """Helper to create a LogRecord."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg=msg,
            args=args,
            exc_info=None,
        )
        return record

    def test_filter_msg(self):
        # Note: avoid "用户" before Chinese chars, as it triggers the name pattern.
        record = self._make_record("编号13812345678已处理")
        self.log_filter.filter(record)
        assert record.msg == "编号138****5678已处理"

    def test_filter_tuple_args(self):
        record = self._make_record("用户%s登录", ("13812345678",))
        self.log_filter.filter(record)
        assert record.args[0] == "138****5678"

    def test_filter_dict_args(self):
        # logging.LogRecord constructor cannot accept a bare dict as args
        # (it tries args[0] which raises KeyError), so we set args post-construction.
        record = self._make_record("用户%(phone)s登录")
        record.args = {"phone": "13812345678"}
        self.log_filter.filter(record)
        assert record.args["phone"] == "138****5678"
        # Verify the formatted message also contains the masked value
        assert "138****5678" in record.getMessage()

    def test_non_string_msg_unchanged(self):
        """Non-string msg (e.g. an Exception) is not modified."""
        exc = ValueError("some error")
        record = self._make_record(exc)
        self.log_filter.filter(record)
        assert record.msg is exc

    def test_non_string_in_tuple_args_preserved(self):
        record = self._make_record("值%d和%s", (42, "13812345678"))
        self.log_filter.filter(record)
        assert record.args[0] == 42
        assert record.args[1] == "138****5678"

    def test_always_returns_true(self):
        """filter() always returns True so the log record is emitted."""
        record = self._make_record("no pii here")
        assert self.log_filter.filter(record) is True

    def test_no_args_unchanged(self):
        record = self._make_record("手机13812345678", args=None)
        self.log_filter.filter(record)
        assert record.msg == "手机138****5678"
        assert record.args is None

    def test_empty_tuple_args(self):
        record = self._make_record("手机13812345678", args=())
        self.log_filter.filter(record)
        assert record.msg == "手机138****5678"
        assert record.args == ()

    def test_dict_args_non_string_values_preserved(self):
        record = self._make_record("msg")
        record.args = {"phone": "13812345678", "count": 5}
        self.log_filter.filter(record)
        assert record.args["phone"] == "138****5678"
        assert record.args["count"] == 5

    def test_email_in_msg_filtered(self):
        record = self._make_record("发送到了user@example.com")
        self.log_filter.filter(record)
        assert "u***@example.com" in record.msg
        assert "user@example.com" not in record.msg

    def test_works_with_real_logger(self):
        """Integration test: PIILogFilter with a real logging.Logger."""
        test_logger = logging.getLogger("test_pii_log_filter_integration")
        pii_filter = PIILogFilter()
        test_logger.addFilter(pii_filter)
        test_logger.setLevel(logging.DEBUG)
        test_logger.propagate = False

        handler = logging.handlers.MemoryHandler(capacity=100)
        test_logger.addHandler(handler)

        try:
            test_logger.info("编号13812345678已处理")
            handler.flush()
            assert len(handler.buffer) == 1
            emitted = handler.buffer[0]
            msg = emitted.getMessage()
            assert "138****5678" in msg
            assert "13812345678" not in msg
        finally:
            test_logger.removeFilter(pii_filter)
            test_logger.removeHandler(handler)
            handler.close()
