# SmartQA Pro — 面试专属“终极超能力 (SuperPowers)”升级 OpenSpec

> **版本**: 4.0 | **日期**: 2026-05-22 | **定位**: 智能自愈与高容错性（面向资深/专家级 AI 架构师面试标准）
> **升级执行人**: Hermes Agent
> **核心概念**: 智能体自愈 (Self-Healing)、防御性工程 (Defensive Engineering)

在完成了 Graph RAG、Langfuse 全链路 Trace 和 Redis 分布式写锁之后，SmartQA Pro 的架构深度已基本就绪。然而，在专家级面试中，面试官往往会针对 **“Agent 死循环与幻觉”** 以及 **“用户模糊/拼写错误输入导致 RAG 彻底失效”** 这两个真实的生产落地痛点进行极限追问。

本 OpenSpec 引入两大终极“超级能力 (SuperPowers)”，助力项目无缝通过任何硬核技术面试。

---

## 1. SuperPowers 设计清单与实现规格

### SuperPower-1：ReAct 思考死循环自动检测与自愈拦截器 (Self-Healing Loop Breaker) [P0]
*   **面试痛点**：大模型在使用 ReAct 循环或多 Agent 协同（如 `ToolAgent`）时，由于工具返回结果不理想或提示词引导歧义，极易陷入 `Action: query_inventory` -> `Observation` -> `Action: query_inventory` 的无限思维死循环，直到消耗尽 `MAX_ITERATIONS` 资源后返回无意义的错误。
*   **超能力规格**：
    1.  **历史行为签名拦截**：在 `app/agents/tool.py`（手写 ReAct）的执行循环中维护一个 `call_history` 列表，存储每次工具调用（工具名 + 参数的哈希）。
    2.  **死循环检测 (Loop Detection)**：如果检测到：
        *   同一个工具带相同或极其相似的参数连续调用了 2 次；
        *   或者发生 A -> B -> A 循环；
    3.  **自愈提示词动态强注入 (Self-Healing Prompt Injection)**：一旦检测到死循环预警，自愈拦截器自动在 LLM 的 `history` 消息中强制追加一条系统级自愈指令：
        ```
        "⚠️ [System Alert: Thinking Loop Detected] 你已连续多次使用相同参数调用了该工具。请立刻停止重复调用！如果数据未查到，请说明数据不存在，或者尝试使用 get_knowledge 检索相关文档以寻找替代线索。请立刻在下一次思考中输出最终回答 Final Answer。"
        ```
    4.  **优雅熔断**：强制把大模型拉出思维死锁，返回一个带有“自愈诊断说明”的优雅回答。
*   **修改文件**：
    *   `backend/app/agents/tool.py`

---

### SuperPower-2：模糊输入自愈归一化与语义同义词对齐 (Semantic Self-Correction) [P0]
*   **面试痛点**：真实场景中用户输入极其不规范。如把 `MAT-001` 输入为 `mat001`、`MATOO1`（把数字零错打成了字母O）；或输入模糊别名（如“采购单”映射为 `PurchaseOrder`，“工单”映射为 `maintenance_ticket`）。这会导致多路检索正则提取失败，Graph RAG 2-hop 无法匹配，检索瞬间落空。
*   **超能力规格**：
    1.  **自愈性拼写归一化 (Spell Self-Correction)**：在检索与关系分析前，利用自愈过滤函数对输入的实体特征进行归一化：
        *   自动识别把 `0` 打成 `O` 或 `o` 的拼写错误（如 `MAT-OO1` -> `MAT-001`）；
        *   自动为漏掉横线的编码添加连字符（如 `mat001` -> `MAT-001`，`po20250101` -> `PO-20250101`）；
    2.  **多级模糊对齐与实体相似度召回 (Entity Fuzzy Alignment)**：
        *   如果在 Neo4j 检索中无直接匹配，使用 Python 内置的 `difflib` 或 Jaro-Winkler 算法，对输入的实体编码与图数据库已知的实体字典进行快速编辑距离计算。
        *   当相似度高于 0.8 时，自动在后台将其纠正为标准实体编码（例如：将 `MAT001` 自动替换为 `MAT-001` 并执行查询），并在 SSE 流中返回 `query_correction` 事件通知前端。
*   **修改文件**：
    *   `backend/app/core/rag_engine.py` (实体提取优化)
    *   `backend/app/core/neo4j_client.py` (2-hop 关系检索模糊容错)

---

## 2. 核心代码升级与实现范式

### 2.1 ReAct 自愈死循环熔断实现范式 (`backend/app/agents/tool.py`)
在 `ToolAgent.run` 的推理循环（约 160 行）中注入自愈与死循环检测：
```python
        # 自愈死循环检测器 (SuperPower-1)
        call_history = []  # 记录格式: {"tool": str, "args": dict}
        
        while iteration < self.MAX_ITERATIONS:
            # ... [LLM 推理产出 thought, action, action_input] ...
            
            # 检测死循环
            current_call = {"tool": action, "args": action_input}
            is_loop = False
            for prev_call in call_history:
                if prev_call["tool"] == action and prev_call["args"] == action_input:
                    is_loop = True
                    break
            
            if is_loop:
                logger.warning(f"[LoopBreaker] 检测到 Agent 陷入死循环: {action}({action_input})")
                # 动态强注入自愈 Prompt，打断死循环并自愈
                messages.append(AIMessage(content=f"Thought: 我需要调用 {action} 来获取数据。"))
                messages.append(ToolMessage(
                    content=(
                        f"⚠️ [System Alert: Loop Detected] 系统检测到你正在重复调用 {action} 并传入相同参数 {action_input}。"
                        f"这说明该数据源无法提供更多新数据。请立刻终止调用此工具！请结合已有信息进行合理推论，"
                        f"或调用 get_knowledge 获取背景文档，或者直接输出 Final Answer 给用户。"
                    ),
                    tool_call_id=tool_call_id
                ))
                # 注入强烈的 System 提示，强制收敛
                messages.append(SystemMessage(content="[System Lock] 必须在下一步输出 Final Answer，结束循环。"))
                iteration += 1
                continue
                
            call_history.append(current_call)
            # ... [执行工具，获取 observation 并反馈] ...
```

### 2.2 模糊实体自愈对齐实现范式 (`backend/app/core/neo4j_client.py`)
在 2-hop 子图上下文检索中支持拼写纠错和模糊匹配：
```python
    def _normalize_entity(self, entity: str) -> str:
        """实体拼写自愈归一化 (SuperPower-2)
        
        支持把 MAT001 纠正为 MAT-001，把 MAT-OO1 纠正为 MAT-001 
        """
        import re
        normalized = entity.strip().upper()
        
        # 1. 自动把字母 O / o 纠正为数字 0 (若在编码末端)
        # 例如 MAT-OO1 -> MAT-001
        normalized = re.sub(r'([A-Z]+-?)[O|o]+', lambda m: m.group(1) + '0' * (len(m.group(0)) - len(m.group(1))), normalized)
        
        # 2. 自动补充缺失的连字符 
        # 例如 MAT001 -> MAT-001
        if re.match(r'^(MAT|PO|SUP)(\d+)$', normalized):
            normalized = re.sub(r'^(MAT|PO|SUP)(\d+)$', r'\1-\2', normalized)
            
        return normalized
```
并在 `get_2hop_subgraph_context` 首行调用：
```python
        entity = self._normalize_entity(entity)
```

---

## 3. 验证与回归测试

1.  **Loop Breaker 测试**：
    *   提问一个故意会引起死循环的刁钻问题（例如，调用一个必定会失败/返回重复信息的自定义测试工具），验证 Agent 会在第 2 次重复时被 Loop Breaker 成功拦截，强制改变执行路径并给出有说服力的回答。
2.  **模糊实体纠错测试**：
    *   提问：“帮我查一下物料 `matOO1` 相关的订单。”（故意打错零为大写O，且小写和漏横线）。
    *   检查后端日志，验证系统能够成功识别并自愈为 `MAT-001`，同时 Neo4j 2-hop 成功检索出关联图谱。
