"""
SupplyChainRAG - Text-to-SQL 引擎

将自然语言转换为 SQL 查询，用于查询 PostgreSQL 中的结构化数据。
供应链典型场景：库存查询、订单统计、供应商分析等。

安全措施：
1. 只允许 SELECT 语句（禁止 INSERT/UPDATE/DELETE/DROP）
2. 表名白名单
3. 关键字黑名单（正则阻断）
4. 参数化执行（提取字面值 → :pN 绑定参数）
5. 结果行数限制（默认 100 行）
6. 查询超时（默认 5 秒）

面试亮点：展示 NL2SQL 能力，说明结构化 + 非结构化双检索架构。
"""
import re
import time
import logging
import asyncio
import hashlib
from typing import Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


# 表结构 Schema（供 LLM 理解数据库结构）
SUPPLY_CHAIN_SCHEMA = """
-- 供应链数据库表结构

-- 用户表
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL,       -- admin/purchase/warehouse/quality/production/finance/logistics
    department VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- 工单表
CREATE TABLE tickets (
    id SERIAL PRIMARY KEY,
    ticket_code VARCHAR(30) UNIQUE NOT NULL,  -- TK-20250519000001
    title VARCHAR(500) NOT NULL,
    description TEXT,
    status VARCHAR(50) DEFAULT 'pending',     -- pending/in_progress/resolved/closed
    priority VARCHAR(20) DEFAULT 'normal',    -- low/normal/high/critical
    created_by VARCHAR(100),
    assigned_to VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    resolved_at TIMESTAMP
);

-- 反馈表
CREATE TABLE feedbacks (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,   -- 会话追踪(同会话多次交互)
    query TEXT NOT NULL,               -- 用户问题
    answer TEXT NOT NULL,              -- RAG 返回的完整回答
    sources JSON,                      -- 参考来源 [{"source": "...", "page": 0}]
    rating INTEGER NOT NULL,           -- 1=正面(thumbs up), -1=负面(thumbs down), 二值化设计
    comment TEXT,                      -- 用户留言(可选)
    confidence FLOAT,                  -- RAG 回答置信度
    intent VARCHAR(32),                -- 意图分类标签
    user_id INTEGER,                   -- 关联用户(可匿名)
    created_at TIMESTAMP DEFAULT NOW(),
    client_info JSON                   -- 浏览器UA、IP等
);

-- 评估结果表
CREATE TABLE eval_results (
    id SERIAL PRIMARY KEY,
    query TEXT,
    generated_answer TEXT,
    reference_answer TEXT,
    context_precision FLOAT,
    faithfulness FLOAT,
    answer_relevance FLOAT,
    overall_score FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);
"""

# 允许的表名白名单
ALLOWED_TABLES = {"users", "tickets", "feedbacks", "eval_results"}

# Few-shot 示例(给 LLM 看样本,显著提升 SQL 质量)
# 选 3 条覆盖:简单查询、聚合查询、模糊查询
FEW_SHOT_EXAMPLES = """
示例 1 - 简单条件查询:
Q: 查询所有未解决的工单
A: SELECT ticket_code, title, status, priority, created_at FROM tickets WHERE status = 'pending' LIMIT 100;

示例 2 - 聚合查询:
Q: 统计每个部门的用户数
A: SELECT department, COUNT(*) AS user_count FROM users GROUP BY department ORDER BY user_count DESC LIMIT 100;

示例 3 - 模糊查询:
Q: 查询标题里有"库存"的工单
A: SELECT ticket_code, title, status, created_at FROM tickets WHERE title ILIKE '%库存%' ORDER BY created_at DESC LIMIT 100;
"""

# 禁止的 SQL 关键词（安全检查）
FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "CREATE", "GRANT", "REVOKE", "EXEC", "EXECUTE",
]


@dataclass
class SQLQueryResult:
    """SQL 查询结果"""
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    error: str = ""
    execution_ms: float = 0.0
    # 进阶字段(自纠正/结果验证用)
    correction_attempts: int = 0        # 自纠正重试次数
    validation_warnings: list[str] = field(default_factory=list)  # 结果验证警告
    original_question: str = ""         # 原始问题(用于上下文)


class TextToSQLEngine:
    """Text-to-SQL 引擎"""

    def __init__(self):
        pass

    def _validate_sql(self, sql: str) -> tuple[bool, str]:
        """验证 SQL 安全性
        
        Returns:
            (is_valid, error_message)
        """
        sql_upper = sql.upper().strip()

        # 1. 必须以 SELECT 开头
        if not sql_upper.startswith("SELECT"):
            return False, "只允许 SELECT 查询"

        # 2. 禁止危险关键词
        for keyword in FORBIDDEN_KEYWORDS:
            pattern = r'\b' + keyword + r'\b'
            if re.search(pattern, sql_upper):
                return False, f"禁止使用 {keyword}"

        # 3. 检查表名是否在白名单中
        table_pattern = re.compile(r'\bFROM\s+(\w+)', re.IGNORECASE)
        tables = table_pattern.findall(sql)
        for table in tables:
            if table.lower() not in ALLOWED_TABLES:
                return False, f"表 {table} 不在允许列表中（允许: {', '.join(ALLOWED_TABLES)}）"

        # 4. 检查 JOIN 中的表名
        join_pattern = re.compile(r'\bJOIN\s+(\w+)', re.IGNORECASE)
        join_tables = join_pattern.findall(sql)
        for table in join_tables:
            if table.lower() not in ALLOWED_TABLES:
                return False, f"JOIN 表 {table} 不在允许列表中"

        return True, ""

    def _build_prompt(self, question: str, user_role: str = "admin") -> str:
        """构建 Text-to-SQL 提示词"""
        role_filter = ""
        if user_role != "admin":
            role_filter = f"\n注意：当前用户角色是 {user_role}，如果查询涉及权限敏感数据，请添加过滤条件。"

        return f"""你是一个 SQL 专家。请根据以下数据库 Schema，将用户的自然语言问题转换为 PostgreSQL SQL 查询。

{SUPPLY_CHAIN_SCHEMA}
{FEW_SHOT_EXAMPLES}
{role_filter}

用户问题：{question}

要求：
1. 只输出一行纯 SQL 语句，不要有任何解释、注释或 markdown 标记
2. 只使用 SELECT 查询
3. 限制返回行数为 100（使用 LIMIT 100）
4. 如果用户问题中出现了具体的值（如用户名、工单号），直接使用
5. 对模糊查询使用 LIKE 或 ILIKE
6. 如果需要排序，默认按 created_at DESC
7. 参考上面的示例,模仿其风格和结构

SQL:"""

    async def generate_sql(self, question: str, user_role: str = "admin") -> str:
        """使用 LLM 生成 SQL"""
        from app.core.llm_router import LLMFactory
        from langchain_core.messages import SystemMessage, HumanMessage

        prompt = self._build_prompt(question, user_role)

        try:
            llm = LLMFactory.get_llm(temperature=0.0, streaming=False)
            response = await llm.ainvoke([
                SystemMessage(content="你是SQL专家。只输出SQL语句，不要任何解释。"),
                HumanMessage(content=prompt),
            ])

            sql = response.content.strip()

            # 清理 LLM 输出的 markdown 和多余内容
            sql = re.sub(r'^```sql\s*', '', sql)
            sql = re.sub(r'^```\s*', '', sql)
            sql = re.sub(r'\s*```$', '', sql)
            sql = sql.strip().rstrip(';')

            return sql
        except Exception as e:
            logger.error(f"[Text2SQL] LLM 生成 SQL 失败: {e}")
            raise

    async def _generate_sql_with_feedback(
        self, question: str, prev_sql: str, error_msg: str, user_role: str = "admin"
    ) -> str:
        """自纠正:把上一次的错误反馈给 LLM,让它改 SQL

        关键设计:
        - 把 prev_sql + 错误信息拼成新的 prompt
        - 让 LLM 看到错误"反思"再生成
        - 不修改原 prompt 结构,只在最后追加反思段
        """
        from app.core.llm_router import LLMFactory
        from langchain_core.messages import SystemMessage, HumanMessage

        base_prompt = self._build_prompt(question, user_role)
        feedback_prompt = f"""{base_prompt}

---

[上一轮自纠正]
你上次生成的 SQL:
```sql
{prev_sql}
```
执行错误:
```
{error_msg}
```

请仔细分析错误原因,只输出修正后的 SQL(不要解释)。常见错误:
1. 表名/字段名拼写错误 → 重新对照 Schema
2. JOIN 条件不明确 → 加 ON 条件
3. 列名歧义 → 用 table.column 明确限定
4. LIMIT/语法错误 → 严格 PostgreSQL 语法

修正后的 SQL:"""

        try:
            llm = LLMFactory.get_llm(temperature=0.0, streaming=False)
            response = await llm.ainvoke([
                SystemMessage(content="你是SQL专家。你会看到上次生成的 SQL 和错误,请反思后只输出修正后的 SQL。"),
                HumanMessage(content=feedback_prompt),
            ])

            sql = response.content.strip()
            sql = re.sub(r'^```sql\s*', '', sql)
            sql = re.sub(r'^```\s*', '', sql)
            sql = re.sub(r'\s*```$', '', sql)
            sql = sql.strip().rstrip(';')
            return sql
        except Exception as e:
            logger.error(f"[Text2SQL] 自纠正生成 SQL 失败: {e}")
            raise

    def _validate_result(self, result: SQLQueryResult, question: str) -> list[str]:
        """结果验证:0 行 / 满行 / 异常值 → 警告

        不是阻断,而是给用户/调用方提示。
        """
        warnings = []

        # 1. 0 行结果 → 可能查询条件太严 / 数据缺失
        if result.row_count == 0 and not result.error:
            warnings.append(
                "查询返回 0 行,可能是:(a) 查询条件太严 (b) 数据未录入 (c) 表名/字段名拼写错。"
                "建议:检查 status 字段取值(pending/in_progress/resolved/closed),或放宽 WHERE 条件。"
            )

        # 2. 满行 → 可能没加精确 WHERE
        if result.row_count >= 100 and not result.error:
            warnings.append(
                "返回 100 行(LIMIT 上限),可能需要更精确的 WHERE 条件。"
                "建议:加上具体用户/时间/部门过滤,或用 GROUP BY 聚合。"
            )

        # 3. 异常值检测:数字列含负数 / 异常大值
        if result.rows and result.columns:
            for col_idx, col_name in enumerate(result.columns):
                if not col_name: continue
                col_lower = col_name.lower()
                # 库存/数量类字段不能为负
                if any(k in col_lower for k in ['count', 'quantity', 'stock', 'amount', 'qty', 'num']):
                    for row in result.rows:
                        if col_idx >= len(row): continue
                        val = row[col_idx]
                        try:
                            num = float(val) if val is not None else None
                            if num is not None and num < 0:
                                warnings.append(
                                    f"列 {col_name} 出现负数 ({val}),供应链场景下异常,可能是数据录入错误。"
                                )
                                break
                        except (ValueError, TypeError):
                            pass

        # 4. 评估分数超合理范围
        if result.rows and result.columns:
            for col_idx, col_name in enumerate(result.columns):
                if 'score' in (col_name or '').lower() and 'context_precision' not in (col_name or '').lower():
                    for row in result.rows:
                        if col_idx >= len(row): continue
                        val = row[col_idx]
                        try:
                            num = float(val) if val is not None else None
                            if num is not None and (num < 0 or num > 1):
                                warnings.append(
                                    f"评估分数 {col_name} = {val},超出 [0,1] 正常范围,可能是计算错误。"
                                )
                                break
                        except (ValueError, TypeError):
                            pass

        return warnings

    async def execute(self, question: str, user_role: str = "admin") -> SQLQueryResult:
        """完整的 Text-to-SQL 执行流程(进阶版:含自纠正 + 结果验证)

        流程:
        1. LLM 生成 SQL(第 1 次)
        2. 安全验证(表白名单/关键词)
        3. 执行 + 捕获错误
        4. 如果失败 → 自纠正重试 1 次(把错误反馈给 LLM)
        5. 结果验证(0 行/满行/异常值警告)
        6. 返回结果
        """
        import time
        t0 = time.perf_counter()
        MAX_RETRIES = 1  # 最多自纠正 1 次(总共 2 次 LLM 调用)

        # 1+2+3. 第一次尝试:生成 → 验证 → 执行
        try:
            sql = await self.generate_sql(question, user_role)
        except Exception as e:
            logger.warning(f"[Text2SQL] SQL 生成失败: {type(e).__name__}: {e}")
            return SQLQueryResult(
                sql="", error=f"SQL生成失败: {e}",
                original_question=question
            )

        is_valid, error_msg = self._validate_sql(sql)
        if not is_valid:
            logger.warning(f"[Text2SQL] SQL 安全验证失败: {sql} - {error_msg}")
            return SQLQueryResult(
                sql=sql, error=f"SQL安全验证失败: {error_msg}",
                original_question=question
            )

        if "LIMIT" not in sql.upper():
            sql += " LIMIT 100"

        result = await self._exec_sql(sql, t0, question)
        if not result.error:
            # 第一次就成功
            result.correction_attempts = 0
            result.validation_warnings = self._validate_result(result, question)
            return result

        # 4. 自纠正:把错误反馈给 LLM,再试一次
        logger.info(f"[Text2SQL] 第 1 次失败({result.error[:80]}),启动自纠正...")
        for attempt in range(MAX_RETRIES):
            try:
                corrected_sql = await self._generate_sql_with_feedback(
                    question, sql, result.error, user_role
                )
                logger.info(f"[Text2SQL] 自纠正第 {attempt+1} 次生成: {corrected_sql[:100]}")

                # 重新走验证 + 执行
                is_valid, error_msg = self._validate_sql(corrected_sql)
                if not is_valid:
                    logger.warning(f"[Text2SQL] 自纠正 SQL 仍验证失败: {error_msg}")
                    sql = corrected_sql
                    result = SQLQueryResult(sql=corrected_sql, error=f"自纠正后仍验证失败: {error_msg}",
                                            correction_attempts=attempt+1, original_question=question)
                    continue  # 再试

                if "LIMIT" not in corrected_sql.upper():
                    corrected_sql += " LIMIT 100"

                result = await self._exec_sql(corrected_sql, t0, question)
                result.correction_attempts = attempt + 1
                if not result.error:
                    logger.info(f"[Text2SQL] 自纠正第 {attempt+1} 次成功!")
                    result.validation_warnings = self._validate_result(result, question)
                    return result
                sql = corrected_sql  # 准备下一轮重试用
            except Exception as e:
                logger.warning(f"[Text2SQL] 自纠正生成失败: {e}")
                # 用第一次的错误作为最终结果
                result.correction_attempts = attempt + 1
                break

        # 自纠正也失败,返回最后的 error
        result.original_question = question
        return result

    async def _exec_sql(self, sql: str, t0: float, question: str) -> SQLQueryResult:
        """执行单条 SQL（L3 结果缓存 + 参数化执行）

        L3 缓存：只读 SELECT 结果按 md5(sql) 缓存 L3_CACHE_TTL_SQL 秒，
        错误结果不缓存（cache_if 谓词）；Redis 不可用时直查。
        """
        from app.core.cache_manager import cache_manager
        from app.config import get_settings

        async def _load() -> dict:
            r = await self._exec_sql_uncached(sql, t0, question)
            return asdict(r)

        key = hashlib.md5(sql.encode("utf-8")).hexdigest()
        data = await cache_manager.l3_get_or_set(
            "t2sql",
            key,
            get_settings().L3_CACHE_TTL_SQL,
            _load,
            cache_if=lambda v: not v.get("error"),
        )
        return SQLQueryResult(**data)

    async def _exec_sql_uncached(self, sql: str, t0: float, question: str) -> SQLQueryResult:
        """执行单条 SQL（参数化执行，防注入）

        安全措施：
        1. 提取 SQL 中的字符串字面值，替换为 :pN 绑定参数
        2. 通过 SQLAlchemy text().bindparams() 安全执行
        3. 5 秒超时保护
        """
        logger.info(f"[Text2SQL] 执行: {sql[:120]}")
        try:
            from app.core.database import async_session
            from sqlalchemy import text as sa_text

            # ── 参数化执行 ──
            # 提取 SQL 中的单引号字符串字面值，替换为绑定参数
            param_values: list[str] = []

            def _extract_literal(match: re.Match) -> str:
                """提取字符串字面值内容，替换为 :pN 占位符"""
                param_values.append(match.group(1))
                return f":p{len(param_values)}"

            # 匹配单引号字符串（处理 SQL 内转义 ''）
            param_sql = re.sub(r"'((?:[^']|'')*)'", _extract_literal, sql)

            # 构建绑定参数字典
            bindparams = {f"p{i+1}": v for i, v in enumerate(param_values)}

            # 转义 % 防止 SQLAlchemy text() 误解析为参数标记
            param_sql = param_sql.replace("%", "%%")

            async with async_session() as session:
                if bindparams:
                    result = await session.execute(
                        sa_text(param_sql).bindparams(**bindparams).execution_options(timeout=5)
                    )
                else:
                    result = await session.execute(
                        sa_text(param_sql).execution_options(timeout=5)
                    )
                rows = result.fetchall()
                columns = list(result.keys())

            elapsed = time.perf_counter() - t0
            logger.info(f"[Text2SQL] 完成: {len(rows)} 行, {elapsed*1000:.0f}ms")

            return SQLQueryResult(
                sql=sql,
                columns=columns,
                rows=[[str(v) if v is not None else None for v in row] for row in rows],
                row_count=len(rows),
                execution_ms=round(elapsed * 1000),
                original_question=question,
            )
        except asyncio.TimeoutError:
            return SQLQueryResult(sql=sql, error="查询超时(5秒)", original_question=question)
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"[Text2SQL] 执行失败 [{error_type}]: {e}")
            # 保留完整错误信息(自纠正时反馈给 LLM)
            return SQLQueryResult(
                sql=sql,
                error=f"{error_type}: {str(e)[:200]}",
                original_question=question
            )

    def format_result(self, result: SQLQueryResult) -> str:
        """将查询结果格式化为可读文本(进阶版:含自纠正状态 + 验证警告)"""
        if result.error:
            correction_note = ""
            if result.correction_attempts > 0:
                correction_note = f"\n(已尝试 {result.correction_attempts} 次自纠正,均失败)"
            return (
                f"查询失败:{result.error}\n"
                f"生成的SQL:`{result.sql}`{correction_note}"
            )

        if result.row_count == 0:
            return "查询未返回任何结果。"

        lines = [f"查询返回 {result.row_count} 条结果:\n"]
        lines.append("| " + " | ".join(result.columns) + " |")
        lines.append("|" + "|".join(["---" for _ in result.columns]) + "|")

        for row in result.rows[:20]:
            lines.append("| " + " | ".join(str(v) if v is not None else "-" for v in row) + " |")

        if result.row_count > 20:
            lines.append(f"\n... 还有 {result.row_count - 20} 行未显示")

        # 元信息
        meta = []
        meta.append(f"执行耗时: {result.execution_ms}ms")
        if result.correction_attempts > 0:
            meta.append(f"自纠正: {result.correction_attempts} 次后成功")
        if result.validation_warnings:
            meta.append(f"验证警告: {len(result.validation_warnings)} 条")
        lines.append(f"\n*({'; '.join(meta)})*")

        # 验证警告(在底部独立显示)
        if result.validation_warnings:
            lines.append("\n\n⚠️ **结果验证警告**:")
            for w in result.validation_warnings:
                lines.append(f"- {w}")

        return "\n".join(lines)


# 模块级单例
_text_to_sql: Optional[TextToSQLEngine] = None


def get_text_to_sql() -> TextToSQLEngine:
    global _text_to_sql
    if _text_to_sql is None:
        _text_to_sql = TextToSQLEngine()
    return _text_to_sql
