# RBAC 权限与数据隔离架构

> 一页纸：把分散在 6 个文件的权限逻辑（UserRole / JWT / 向量库安全组 / SQL 表级白名单 / 端点拦截 / 会话隔离）整合成一张图。
> 面试可直接打印对照讲解。

## 1. 三层防御模型

```
┌─────────────────────────────────────────────────────────────┐
│  L1  身份认证 (Authentication)                              │
│      JWT Token → verify_token() → user dict                │
│      文件: app/core/auth.py:90                              │
├─────────────────────────────────────────────────────────────┤
│  L2  角色授权 (Authorization, RBAC)                         │
│      @require_role(*allowed_roles) 装饰器 / check_role()   │
│      文件: app/core/auth.py:190, 215                        │
├─────────────────────────────────────────────────────────────┤
│  L3  数据隔离 (Data Isolation)                              │
│      ├─ 向量库: build_visibility_expr() 行级过滤            │
│      │        文件: app/core/milvus_client.py:326           │
│      ├─ 关系库: text_to_sql ALLOWED_TABLES 表级白名单       │
│      │        文件: app/core/text_to_sql.py:79              │
│      └─ 会话:   Redis key 按 user_id 隔离                   │
│               文件: app/core/redis_client.py:104            │
└─────────────────────────────────────────────────────────────┘
```

任意一层失败 → 请求被拦截，**不会**漏到下游数据源。

## 2. 7 种角色

`app/models/user.py:18` 定义 `UserRole(str, Enum)`：

| 角色 | value | 部门 | 数据权限 |
|------|-------|------|---------|
| `admin` | admin | — | 看全部 |
| `manager` | manager | 部门字段 | 部门内全部 |
| `employee` | employee | — | 仅 public |
| `purchase` | purchase | 采购 | 采购表 + 公开 |
| `warehouse` | warehouse | 仓库 | 库存表 + 公开 |
| `quality` | quality | 质量 | 质检表 + 公开 |
| `finance` | finance | 财务 | 财务表 + 公开 |
| `logistics` | logistics | 物流 | 物流表 + 公开 |
| `production` | production | 生产 | 生产表 + 公开 |

## 3. L1 - JWT 身份认证

> JWT（HS256 签名）+ Redis 黑名单（登出撤销）。签发时本地运算，无需查存储；登出时 jti 加入 Redis 黑名单。

```python
# app/core/auth.py:103
async def verify_token(token: str) -> Optional[dict]:
    """JWT 签名验证 + Redis 黑名单检查 → {user_id, username}"""
    # 1. JWT 解码（纯本地，不查 Redis）
    payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    # 2. 检查 Redis 黑名单（登出撤销）
    blacklisted = await redis.get(f"scqa:blacklist:{payload['jti']}")
    if blacklisted:
        return None
    return {"user_id": payload["user_id"], "username": payload["username"]}
```

调用链：
- `get_current_user_optional(request)` → token 无效返 None（不抛错）
- `get_current_user_required(request)` → token 无效抛 401
- `get_current_user_full(request)` → 校验后**从 DB 重新查 user**，保证 role 是最新的

## 4. L2 - RBAC 装饰器

**两种用法**：

```python
# 装饰器（端点签名）
@require_role(UserRole.ADMIN, UserRole.MANAGER)
async def delete_document(request: Request, doc_id: str):
    # 权限不足 → 403，无需手动检查
    ...

# 函数内（动态判断）
user = await get_current_user_full(request)
check_role(user, [UserRole.ADMIN])  # 抛 403
```

**应用点**：
- `app/api/knowledge.py:69` — 知识库管理（admin/manager）
- `app/api/knowledge.py:119, 157` — 文档上传/删除
- `app/api/tool.py:111, 136, 168` — 工具调用
- `app/api/chat.py:196` — 角色权限检查

## 5. L3 - 三种数据隔离实现

### 5.1 向量库（行级 Milvus 过滤）

每个 chunk 入库时绑定 `security_group` 字段（角色数组）。检索时按用户角色动态生成过滤表达式：

```python
# app/core/milvus_client.py:326
def build_visibility_expr(self, role: str, user_doc_ids: Optional[list] = None) -> str:
    # admin: 不加 filter
    if role == "admin":
        return ""
    # 其他角色: array_contains(security_group, "finance") OR array_contains(...,"public")
    return f'(array_contains(security_group, "{role}") '
           f'or array_contains(security_group, "public"))'
```

**`"public"` 标记**是公开文档的"通配符"——任何角色都能看。**admin 看全部**——不应用任何过滤。

### 5.2 关系库（表级白名单）

Text-to-SQL 接口防止 LLM 写出危险 SQL：

```python
# app/core/text_to_sql.py:79
ALLOWED_TABLES = {"users", "tickets", "feedbacks", "eval_results"}
FORBIDDEN_KEYWORDS = ["INSERT", "UPDATE", "DELETE", "DROP", "GRANT", ...]

# 四重安全检查
1. SQL 解析出的表名必须在 ALLOWED_TABLES
2. SQL 不能含 FORBIDDEN_KEYWORDS（正则 \b 阻断）
3. 参数化执行（提取字面值 → :pN 绑定参数，防注入）
4. 5s 超时 + LIMIT 100 限制
```

**鉴权加固**（最近修复）：
```python
# app/api/chat.py /sql endpoint
- 无 Authorization token → 强制 role="employee"（最低权限）
- 有 token 但解析失败 → 401（不允许 body 提权）
- body.user_role 不在白名单 → 403
```

### 5.3 会话隔离（Redis key 命名空间）

```python
# app/core/redis_client.py:104
def _key(self, session_id: str, user_id: str = "") -> str:
    if user_id:
        return f"scqa:chat:{user_id}:{session_id}"  # 隔离
    return f"scqa:chat:{session_id}"              # 匿名（测试用）
```

**效果**：用户 A 无法通过 `GET /chat/history?session_id=X` 读到用户 B 的会话——因为 B 的 key 命名是 `scqa:chat:userB:X`，A 查自己的 namespace 找不到。

## 6. 完整鉴权调用链示例

用户请求 `POST /api/v1/chat/ask`：

```
1. FastAPI 路由层
   ↓
2. L1: get_current_user_full(request) [chat.py:196]
   - 解析 Authorization Header
   - JWT 解码 → {user_id: 42, role: "finance"}
   - SELECT * FROM users WHERE id=42 → 验证用户存在且 active
   ↓
3. L2: check_role(user, [UserRole.ADMIN, UserRole.FINANCE, ...]) [auth.py:215]
   - role="finance" 在白名单 → 通过
   ↓
4. rag_agent.answer(query, session_id)
   ↓
5. L3a: milvus.search(query_embedding, filter=build_visibility_expr("finance"))
   - 生成的表达式: (array_contains(security_group, "finance")
                    OR array_contains(security_group, "public"))
   - Milvus 服务端只返回匹配的 chunk（行级隔离）
   ↓
6. LLM 生成答案
   ↓
7. L3c: redis.setex(f"scqa:chat:42:{session_id}", history)
   - 会话存到用户 42 的 namespace
```

## 7. 测试覆盖

| 权限点 | 测试文件 | 覆盖 |
|--------|---------|------|
| JWT verify | `test_auth.py` | 有效/过期/伪造 token |
| `require_role` | `test_knowledge_api.py` | admin vs 非 admin |
| `build_visibility_expr` | `test_milvus_client.py` | admin 跳过 / 非 admin 过滤 / public 通配 / user_doc_ids 组合 |
| `text_to_sql` 白名单 | `test_text_to_sql.py` | 表名/关键字/注入/超时 |
| `/sql` 鉴权 | `test_chat_api.py` | 无 token 强制 employee / 无效 token 401 / 非法 role 403 |
| 会话隔离 | `test_redis_client.py` | `_key` 命名空间 / 加锁 / 幂等 |

## 8. 面试讲解套路（90 秒）

> **"我们项目分三层做权限：**
>
> **L1 身份认证用 JWT**，HS256 签名，每次请求都从 DB 重新查 user，保证 role 实时；
>
> **L2 RBAC 用装饰器**，`@require_role(UserRole.ADMIN, UserRole.MANAGER)` 一行解决，缺权限直接 403；
>
> **L3 数据隔离有三层兜底**：
> 1. **向量库行级** —— 每个 chunk 入库带 `security_group` 数组，检索时按角色动态生成 `array_contains` 过滤表达式；
> 2. **关系库表级** —— text-to-SQL 限制了 4 张允许表、禁用 INSERT/UPDATE/DELETE 关键字，5s 超时；
> 3. **会话 Redis 按 user_id 命名空间** —— 用户 A 拿不到用户 B 的会话。
>
> 三层任一失败就拦截，最严重的兜底是 SQL 表级白名单——LLM 即使幻觉写错也写不出 DROP TABLE。"

## 9. 已知安全考虑（生产加固项）

| 风险 | 当前实现 | 生产建议 |
|------|---------|---------|
| SQL `text_to_sql` 限制 4 张表 | 实际业务可能不够 | 动态表白名单（按角色配置） |
| 角色硬编码 7 种 | `app/models/user.py` | 改为 DB 驱动配置 |
| JWT secret 硬编码 `.env` | OK（环境变量隔离） | 加 KMS 密钥轮转 |
| `security_group` 入库由调用方传 | `knowledge.upload` 接收 `security_group` 参数 | 由后端按上传者角色自动推导 |
| `get_current_user_full` 每次查 DB | 简单但 N+1 | 加 Redis 缓存 + 短 TTL |

---

**相关源码**：
- `app/core/auth.py` (L1 + L2)
- `app/core/milvus_client.py` (L3a 行级)
- `app/core/text_to_sql.py` (L3b 表级)
- `app/core/redis_client.py` (L3c 会话)
- `app/models/user.py` (UserRole 枚举)
- `docs/architecture/architecture-agentic.svg` (整体 Agent 架构图)
