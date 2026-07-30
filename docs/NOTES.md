# NOTES — 教练备忘录（用户偏好 / 工作记录）

## 用户画像
- 语言：中文（回答一律中文）。
- 技术栈：Python / RAG / LangChain / Agent / Dify；熟悉 Ollama 本地模型、Docker、FastAPI。
- 项目：supply-chain-qa（供应链知识库问答：混合检索 + Graph RAG + 三级路由 + NL2SQL + 在线护栏）。

## 已知诚实短板（面试要主动说，别等被戳）
- `multiprocessing` / `ProcessPoolExecutor`：没在生产用过，只用 `ThreadPoolExecutor`。
- Django ORM：完全没用过，只看过源码。
- Postman 手动用过，Newman 没集成过 CI。
- NL2SQL：没在 BIRD/Spider 跑过，生产指标没统计（预期 70%+）。
- Hybrid 检索：两条路径（SQL / RAG）未真正串联。
- 企业级：无 K8s / CI-CD / 多租户隔离 / 任务队列。

## 本手册已修复的缺陷（2026-07-06）
- line 895：K=90 笔误（"相比 K=90，K=90" → 改为对比更小 K），并统一 TPE 搜索口径。
- section 13 NL2SQL 行号引用全部对齐真实 `backend/app/core/text_to_sql.py`（524 行）：
  - _build_prompt → 161-184；FEW_SHOT_EXAMPLES → 85-97；
  - _generate_sql_with_feedback → 213-264；execute → 326；
  - _validate_result → 266-324；_validate_sql → 127-159；FORBIDDEN_KEYWORDS → 100。
- 验证：所有内部锚点链接（#part1/#mod-N/#deepseek-map 等）均存在，导航完好。

## ✅ 已修复的项目级 bug（原"feedback/feedbacks 表名不一致"）
- 真相来源：`models/feedback.py:33 __tablename__="feedbacks"` + `alembic/versions/001_initial.py:30` 建表用 `"feedbacks"` → 真实表名是 **feedbacks（复数）**，白名单 `ALLOWED_TABLES` 正确。
- 错误点：`text_to_sql.py:56` 的 DDL 写成了 `CREATE TABLE feedback (`（漏 s），且 `rating` 注释误写 `1-5`（真实是 1/-1 二值化）。
- 修复（2026-07-06）：
  1. DDL 表名 `feedback` → `feedbacks`，与白名单/真实表一致，NL2SQL 不再被拦截。
  2. DDL 对齐真实模型：补全 `sources/confidence/intent/user_id/client_info` 字段，`rating` 注释改为 `1=正面/-1=负面`。
  3. 手册 INTERVIEW_STUDY_GUIDE.html:3723 `users/tickets/feedback/eval_results` → `feedbacks`（复数）。

## 后续可做（用户同意再做）
- 把 section 13 之外的文件级行号（retry.py / auth.py / milvus_client.py / engine.py）也逐一核对当前代码。
- 生成可交互 quiz lesson（HTML）固化高频题。
- 把 73 题自测导出成闪卡，做间隔重复。

## 模拟面试 Coaching Log
- **Q1 热身（RAG 主链路）**：骨架已贴出当送分，用户未口述；默认过。
- **追问：RRF 为什么 K=90？**
  - 用户原答："K默认60，optuna网格搜索得90；K小→分数接近；K大→不知道"。
  - 纠正 1（术语）：是 **optuna TPE 贝叶斯优化**，不是"网格搜索"（tune_all_weights.py:29 用 TPESampler；rrf_k=trial.suggest_int(30,120,step=10)）。说"网格搜索"会被懂 Optuna 的面试官抓。
  - 纠正 2（方向反了）：**K 越小 → 分数越分散**（头部占优、尾部压成近 0，丢召回）；**K 越大 → 分数越压平**（各排名近等权，低质尾文档污染融合、精度降）。手册 line 895 已改写为精确版（含 K=30/60 vs K=1000 极端对比）。
  - 正确基线：默认 RRF_K=60（eval_ragas_full.py OLD 配置）是基线，TPE 调到 90——用户这点数对。
  - 标准 30s 答案骨架已给，待用户复述确认。
  - **用户复述仍把方向说反**：写成"K小→低质尾文档和头部等权重→精度下降；K大→文档都相关→低召回"。实际正好相反（K小→头重脚轻丢召回；K大→等权污染降精度）。已给数字示例（K=10 rank1≈0.091/rank90≈0.010；K=1000 rank1≈0.0010/rank100≈0.0009）+ 记法"K小饿死尾部(召回低)，K大当老好人(精度低)"，并在手册 line 895 后加 ⚠️ 易错 trap。需用户再复述一次确认纠偏。
