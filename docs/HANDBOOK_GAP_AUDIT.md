# 面试手册差距审计报告（Interview Handbook Gap Audit）

> 由 `interview-coach` 技能的 Mode C 自动跑出 · 审计对象：`docs/INTERVIEW_STUDY_GUIDE.html`
> 审计方法：提取手册内全部 `*.py:行号` 引用 → 在真实仓库 `backend/app/` 逐条核对（文件存在 + 行号在范围内 + 内容匹配）；人工比对标准 AI Agent / RAG 岗面试清单找覆盖盲区。

---

## 一、核心结论先行

**手册的诚实性经得起核对，质量很高。** 这不是一份"看起来很全但一问就崩"的资料——所有 24 处代码引用都能对应到真实仓库，且 `@require_role` 确实在 `core/auth.py:301`、RAG 流水线确实在 `rag/engine.py`、三级路由确实在 `agents/router.py`。

**真正的两类"不足"是：**
1. **它是静态只读的**（无主动练习 / 模拟面试评分 / 进度追踪）——这正是 `interview-coach` 教练技能要补的核心缺口。
2. **少数高频面试话题覆盖偏薄或缺失**（见第三节），且有一处描述性行数陈旧（已修，见第二节）。

---

## 二、已修复的事实漂移（安全修正，本次已应用）

| 位置 | 原内容 | 修正 | 说明 |
|------|--------|------|------|
| `text_to_sql.py` 描述 ×5 处（如 L3719 / L3874 / L1874 / L4082 / L4108） | "492 行" | **"523 行"** | 手册写于文件 492 行时，现文件已增至 523 行。注意：手册里引用的具体行号（如 `:79`、`:189-260`、`:308-385`）本身都 ≤523，**仍然有效**，只是散文描述的数字陈旧。 |

> 代码引用行号完整性核对结果：**24 处引用全部有效**，无"引用了不存在的文件/越界行号"的情况。`auth.py` 与 `tool.py` 仓库各存在两份（`api/` 与 `core/`/`agents/`），均按内容正确归位（`@require_role`→`core/auth.py`、`api/tool.py` 实为 197 行）。

---

## 三、覆盖盲区（高频但偏薄 / 缺失的话题）

按"面试出现频率 × 你仓库是否有真实证据"排序。

### 🔴 P0 — 高价值、仓库有真证据、建议尽快补

**1. Agent 框架对比：LangChain vs LangGraph vs 自研**
- 为什么重要：Agent 岗最高频开放题之一，且最容易露怯。
- 仓库证据（强）：`backend/app/agents/langchain_agent.py`、`langgraph_agent.py`、`base_agent.py`、`orchestrator.py` 全部存在。你其实**两种都落地过**，这是差异化卖点。
- 建议形式：新增 1 条 Q&A（草稿见第四节），放进「横向能力」或独立成「m11 Agent 框架」。

**2. 大模型推理 / 部署优化（你本地模型是真跑的）**
- 为什么重要：DeepSeek / 字节等 Agent 岗常问"怎么降本提速"。
- 仓库证据（强）：`llama.cpp-cuda13/`、`models/`、`llama-server.log` 说明你用 llama.cpp 跑本地模型。可讲量化、上下文长度、并发。
- 建议形式：在「13 大模块」补一小节或 1 条 Q&A（诚实边界：你用的是现成 llama.cpp server，未自研推理优化）。

### 🟡 P1 — 中价值，按需补

**3. 系统设计主动框架（不是只会"诚实说没做过"）**
- 现状：手册「企业级架构差距」是**防守型**（被问"从零搭"时怎么诚实答）。但高频题还会问"请设计一个 RAG 系统"，需要**主动**的端到端框架（数据层→检索层→生成层→评测层→成本层）。
- 建议形式：新增 1 个「系统设计答题模板」（类比 + 分层 + tradeoff + 诚实边界）。

**4. RAG 评测指标深入**
- 现状：`evaluator.py` 存在，Q11 讲了 RAGAS 教训，但缺"离线 vs 在线评测""faithfulness / answer relevancy / context recall 怎么算"的体系化讲解。
- 建议形式：补 1 条 Q&A 锚定 `core/evaluator.py`。

**5. 线上故障 / 生产事故讲故事**
- 现状：手册「常见报错速查」只覆盖**启动期**错误。面试常问"讲一次你排查的线上问题"。
- 建议形式：补 1 个 STAR（诚实版：用你部署/调优中真实遇到的，如 Milvus 索引参数、Redis 缓存击穿）。

### 🟢 P2 — 低优先级 / 边缘

**6. 多模态**：`core/multimodal_embedding.py` 存在但手册几乎未提。若目标岗位不涉及，可只准备一句话诚实话术。
**7. 行为/HR 深挖**：现有 3 个 STAR 都是项目技术故事，缺"冲突 / 失败 / 带人"类软故事。可从 2 段实习里提炼 1 个。
**8. 安全/越权**：`core/data_filter.py` 三层权限已覆盖（m9），充分。

---

## 四、建议新增 Q&A 草稿（已写好，待你确认后由教练插入手册）

### 草稿 A — Agent 框架对比（P0，推荐直接插入「横向能力」区块）

```html
<div class="qa" data-qa-id="Q49" data-cat="Agent框架">
  <div class="qa-q" onclick="toggleQA(this)"><span class="qmark">Q</span>LangChain / LangGraph / 自研 Agent 框架，你用过哪些？怎么选？</div>
  <div class="qa-a">
    <div class="qa-versions">
      <div class="qa-ver-bar"><button class="qa-ver-btn active" onclick="qaVer(this,0)">标准 30-45s</button><button class="qa-ver-btn" onclick="qaVer(this,1)">精简 15s</button><button class="qa-ver-btn" onclick="qaVer(this,2)">深挖</button></div>
      <div class="qa-ver-pane active"><span class="amark">A</span> 项目两种都落地过：<code class="inline">langchain_agent.py</code> 用 LCEL 串检索+工具，<code class="inline">langgraph_agent.py</code> 用状态图做多步规划（条件分支/回退）。选型：单轮工具调用用 LangChain 够快；需要循环/人工介入/分支决策用 LangGraph。自研仅限轻量路由（<code class="inline">agents/router.py</code> 三级路由），没重造轮子。tradeoff：框架熟，但 LangGraph 复杂状态机的并发边界没深踩。</div>
      <div class="qa-ver-pane"><span class="amark">精</span> 两种都落地：LangChain 单轮、LangGraph 多步图；自研只做路由层。</div>
      <div class="qa-ver-pane"><span class="amark">深</span> 详细：<code class="inline">langchain_agent.py</code> 用 LCEL 把 retriever+tool+LLM 编成链，适合"检索→答"单轮；<code class="inline">langgraph_agent.py</code> 把"规划→执行→反思"建模成有环状态图，支持条件边（低置信度回退 critic）和人工 breakpoint。选 LangGraph 的判据：是否需要循环/分支/中断恢复。自研只在 <code class="inline">agents/router.py</code> 做三级路由（规则→语义→LLM），因为路由逻辑简单且要零 token 快路径。tradeoff：框架 API 熟，但 LangGraph checkpoint/并发执行没压测过，被追会诚实说只单机跑过。</div>
    </div>
    <div class="qa-follow">
      <div class="follow-title">常见追问</div>
      <ul>
        <li><strong>LangGraph 和 LangChain 本质区别?</strong> LangChain 是"链"(DAG, 一次执行)；LangGraph 是"图"(可有环/分支/状态持久化)，适合 agentic 循环。</li>
        <li><strong>为什么不直接全用 LangGraph?</strong> 单轮场景上 LangGraph 偏重，启动/编排开销不如 LCEL 直接；按场景选型。</li>
        <li><strong>自研路由为什么不用框架?</strong> 路由要 &lt;1ms 零 token 快路径，框架 overhead 不划算，规则+embedding 足够。</li>
      </ul>
    </div>
    <div class="qa-meta">
      <span class="meta-tag cat">Agent框架</span>
      <span class="meta-tag trap">⚠️ 别说"精通 LangGraph 并发"，被追 checkpoint/分布式执行会露怯</span>
      <span class="meta-tag limit">诚实: 两种都落地过，但 LangGraph 仅单机跑过，未压测并发</span>
      <span class="meta-tag ref">agents/langchain_agent.py / agents/langgraph_agent.py / agents/router.py</span>
    </div>
  </div>
</div>
```

> 其余草稿（P0 本地推理优化、P1 系统设计模板、P1 RAG 评测、P1 线上故障 STAR）可按需由教练生成并插入。

---

## 五、诚实风险扫描

未发现"前后硬矛盾"的夸大（手册一致性好）。唯一风险点是 **行数描述陈旧**已修复。其余建议：所有新增内容严格沿用手册现有的 `meta-tag trap / limit` 诚实标签，避免引入新夸大。

---

## 六、下一步（由你决定）

1. ✅ 已应用：行数事实修正（492→523）。
2. ⬜ 待确认：是否让 `interview-coach` 把「草稿 A（Agent 框架对比）」插入手册「横向能力」区块？
3. ⬜ 待确认：是否补 P0 本地推理优化 / P1 系统设计模板等其余草稿？
4. 🔁 日常：随时对我说"模拟面试""讲讲 m3""检查手册"，教练会持续帮你练 + 查漏。

> 跑 `python scripts/verify_doc_integrity.py` 可复核手册完整性（手册内已引用该脚本）。
