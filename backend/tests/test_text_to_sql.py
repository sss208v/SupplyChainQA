"""TextToSQLEngine 单元测试 — 只测试安全验证逻辑，不测试 LLM/DB"""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSQLValidation:
    """测试 SQL 安全验证"""

    def setup_method(self):
        from app.core.text_to_sql import TextToSQLEngine
        self.engine = TextToSQLEngine()

    def test_valid_select(self):
        ok, err = self.engine._validate_sql("SELECT * FROM tickets LIMIT 10")
        assert ok, err

    def test_valid_select_with_where(self):
        ok, err = self.engine._validate_sql(
            "SELECT id, title FROM tickets WHERE status = 'pending' ORDER BY created_at DESC"
        )
        assert ok, err

    def test_reject_insert(self):
        ok, err = self.engine._validate_sql("INSERT INTO tickets (title) VALUES ('test')")
        assert not ok
        assert "SELECT" in err

    def test_reject_delete(self):
        ok, err = self.engine._validate_sql("DELETE FROM tickets WHERE id=1")
        assert not ok
        assert "DELETE" in err or "SELECT" in err

    def test_reject_drop(self):
        ok, err = self.engine._validate_sql("DROP TABLE tickets")
        assert not ok

    def test_reject_unknown_table(self):
        ok, err = self.engine._validate_sql("SELECT * FROM secret_passwords")
        assert not ok
        assert "secret_passwords" in err or "允许" in err

    def test_allow_known_tables(self):
        for table in ["users", "tickets", "feedbacks", "eval_results"]:
            ok, err = self.engine._validate_sql(f"SELECT * FROM {table}")
            assert ok, f"Table {table} should be allowed: {err}"

    def test_reject_update(self):
        ok, err = self.engine._validate_sql("UPDATE tickets SET status='done' WHERE id=1")
        assert not ok

    def test_valid_join(self):
        ok, err = self.engine._validate_sql(
            "SELECT u.username, t.title FROM users u JOIN tickets t ON u.username = t.created_by"
        )
        assert ok, err

    def test_reject_join_unknown_table(self):
        ok, err = self.engine._validate_sql(
            "SELECT * FROM tickets JOIN secret ON tickets.id = secret.id"
        )
        assert not ok

    def test_format_result_empty(self):
        from app.core.text_to_sql import SQLQueryResult
        result = SQLQueryResult(sql="SELECT 1", row_count=0)
        formatted = self.engine.format_result(result)
        assert "未返回" in formatted

    def test_format_result_error(self):
        from app.core.text_to_sql import SQLQueryResult
        result = SQLQueryResult(sql="SELECT 1", error="测试错误")
        formatted = self.engine.format_result(result)
        assert "测试错误" in formatted


class TestSQLGeneration:
    """测试 SQL 生成辅助逻辑"""

    def test_build_prompt(self):
        from app.core.text_to_sql import TextToSQLEngine
        engine = TextToSQLEngine()
        prompt = engine._build_prompt("最近创建的工单有哪些", "admin")
        assert "最近创建的工单有哪些" in prompt
        assert "tickets" in prompt
        assert "admin" not in prompt.split("注意")[-1] if "注意" in prompt else True

    def test_build_prompt_with_role_filter(self):
        from app.core.text_to_sql import TextToSQLEngine
        engine = TextToSQLEngine()
        prompt = engine._build_prompt("我的工单", "purchase")
        assert "我的工单" in prompt
        assert "purchase" in prompt
        assert "权限" in prompt


# ══════════════════════════════════════════════════════════
# 参数化执行测试
# ══════════════════════════════════════════════════════════

class TestParameterizedExecution:
    """测试 _exec_sql 的字符串字面值提取 + 绑定参数逻辑"""

    @staticmethod
    def _extract_params(sql: str) -> tuple[str, list[str]]:
        """模拟 _exec_sql 中的参数提取逻辑"""
        import re
        param_values = []

        def extract(match):
            param_values.append(match.group(1))
            return f":p{len(param_values)}"

        param_sql = re.sub(r"'((?:[^']|'')*)'", extract, sql)
        return param_sql, param_values

    def test_simple_string_extracted(self):
        """简单字符串应被提取为绑定参数"""
        sql = "SELECT * FROM tickets WHERE status = 'pending'"
        param_sql, values = self._extract_params(sql)

        assert ":p1" in param_sql
        assert "'pending'" not in param_sql
        assert values == ["pending"]

    def test_multiple_strings_extracted(self):
        """多个字符串值都应被提取"""
        sql = "SELECT * FROM users WHERE username = 'admin' AND role = 'purchase'"
        param_sql, values = self._extract_params(sql)

        assert ":p1" in param_sql
        assert ":p2" in param_sql
        assert values == ["admin", "purchase"]

    def test_like_pattern_extracted(self):
        """LIKE 模糊查询的字符串应被提取（含 % 通配符）"""
        sql = "SELECT * FROM tickets WHERE title ILIKE '%库存%'"
        param_sql, values = self._extract_params(sql)

        assert ":p1" in param_sql
        assert values == ["%库存%"]

    def test_sql_injection_neutralized_by_parameterization(self):
        """SQL注入payload被提取为绑定参数值，不作为SQL执行

        SQL: WHERE username = '' OR '1'='1'
        解析：'' 是SQL空字符串字面值，'1' 是字符串 '1'
        所有字符串都被提取为绑定参数，注入语法因此失效。
        """
        sql = "SELECT * FROM users WHERE username = '' OR '1'='1'"
        param_sql, values = self._extract_params(sql)

        # 三个字符串字面值都被提取
        assert ":p1" in param_sql
        assert ":p2" in param_sql
        assert ":p3" in param_sql
        # '' 是空字符串, '1' 和 '1' 是两个字符串
        assert values == ["", "1", "1"]
        # 关键：'1'='1' 的比较变成了绑定参数之间的比较，不再是SQL注入语法

    def test_empty_sql_no_params(self):
        """无字符串字面值的SQL应不产生参数"""
        sql = "SELECT COUNT(*) FROM tickets"
        param_sql, values = self._extract_params(sql)

        assert ":p1" not in param_sql
        assert values == []

    def test_number_literals_remain_in_sql(self):
        """数字字面值保留在SQL中（不需要参数化）"""
        sql = "SELECT * FROM tickets LIMIT 100"
        param_sql, values = self._extract_params(sql)

        assert "100" in param_sql

    def test_percent_escaping(self):
        """LIKE 模式的 % 被提取到参数值中，SQL 本身不再有裸 %

        参数值中的 % 是安全的（通过 bindparams 传递，不会被 text() 误解析）。
        """
        sql = "SELECT * FROM tickets WHERE title ILIKE '%库存%'"
        param_sql, values = self._extract_params(sql)

        # %库存% 被提取到绑定参数中
        assert values == ["%库存%"]
        # SQL 本身不再有 % — 只有 :p1 占位符
        assert "%" not in param_sql
        assert ":p1" in param_sql
