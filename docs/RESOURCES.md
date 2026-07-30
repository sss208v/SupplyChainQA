# RESOURCES — 高质量、可信的学习源（按需深入）

> 教练原则：知识先来自高信任源，不依赖参数记忆。以下为扩展深度用。

## 项目本体（最高信任）
- `backend/app/core/rag/engine.py` — 混合检索 5 步流水线（向量/BM25/Graph/RRF/Reranker）。
- `backend/app/core/text_to_sql.py` — NL2SQL（Few-shot + 自纠正 + 结果验证 + 表白名单）。
- `eval/` — RAGAS 四指标 + optuna TPE 调优 ablation 报告（面试"已解决"证据）。
- `.understand-anything/knowledge-graph.json` — 上手指南的数据源。

## 论文 / 权威文档（高频深挖点背书）
- RRF：Cormack et al., 2009, "Reciprocal Rank Fusion outperforms Condorcet"（K=60 经典值，项目用 K=90 经调优）。
- BGE / BAAI Embedding：用于向量检索与 Reranker（中文 SOTA）。
- DIN-SQL (2023) / CHASE-SQL (2024) — NL2SQL 多步分解 / 多候选投票（项目未部署，诚实说）。
- RAGAS — RAG 评估四指标（faithfulness / answer_relevance / context_precision / context_recall）。
- LangChain / FastAPI Depends / Pydantic v2 — 通用后端基础。

## 外部教程（手册 4.x 已指定）
- nanobot（HKUDS/nanobot）— agent 原理主用。
- easy-langent（datawhalechina）— 框架内部可选。

## 社区（拿真实问题练手）
- 牛客网 "晓多科技 / DeepSeek" 面经 — 高频通用题来源。
- 掘金 / 机器之心 — RAG / Agent 工程实践文章。
