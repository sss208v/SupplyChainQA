"""
Orchestrator Agent — 跨域工作流编排

将用户目标型查询拆解为专域 Agent 调用序列，顺序执行并传递上下文。

流程：
  1. Plan: LLM 分析目标 → 生成 [Step1, Step2, ...] 序列
  2. Execute: 按序调用专域 Agent，每步结果注入下一步上下文
  3. Summarize: 汇总所有结果，生成最终回答
"""
import json
import logging
import time
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage

from app.core.llm_router import LLMFactory
from app.agents.agent_router import get_agent_by_name, get_agent_for_tool

logger = logging.getLogger(__name__)

PLAN_SYSTEM_PROMPT = """你是一个供应链编排专家。用户会给你一个供应链管理目标，你需要拆解为任务步骤。

可用 Agent 及其能力：
- InventoryAgent: 查物料库存（query_inventory）。输入物料编码如 MAT-001。
- PurchaseAgent: 查采购订单（query_order）、查供应商（query_supplier）。输入订单号如 PO-20250601。
- QualityAgent: 查知识库（get_knowledge）、创建工单（create_ticket）。
- ProductionAgent: 创建工单（create_ticket）、查物料库存。

规则：
1. 每个步骤指定一个 agent 名称和具体任务描述
2. 步骤之间可以有依赖（后续步骤需要前面步骤的结果）
3. 不要创造不存在的工具，只用上述 Agent 的能力范围
4. 步骤尽量少但不要遗漏关键信息

输出严格 JSON 格式：
{"goal": "用户目标的一句话概括", "steps": [{"agent": "AgentName", "task": "具体要做什么，包含参数"}]}"""


SUMMARY_PROMPT = """你是一个供应链分析专家。以下是用户目标和各步骤执行结果，请生成综合分析报告。

用户目标: {goal}

执行步骤与结果:
{step_results}

请用中文给出：
1. 现状总结（关键数据）
2. 风险评估（如果有）
3. 行动建议（具体可执行）
4. 是否需要人工决策（如果需要，说明原因）"""


class Orchestrator:
    """跨域工作流编排器"""

    def __init__(self):
        pass

    async def plan(self, goal: str) -> dict:
        """LLM 拆解目标为执行计划（优先 fast model，不可用时回退 main，DEMO_MODE 下本地生成）"""
        from app.config import get_settings
        settings = get_settings()

        try:
            llm = LLMFactory.get_llm(temperature=0.1, model="fast")
        except Exception:
            logger.warning("[Orchestrator] fast model 不可用，回退 main model")
            try:
                llm = LLMFactory.get_llm(temperature=0.1, model="main")
            except Exception:
                if settings.DEMO_MODE:
                    return self._demo_plan(goal)
                raise

        messages = [
            SystemMessage(content=PLAN_SYSTEM_PROMPT),
            HumanMessage(content=goal),
        ]

        try:
            response = await llm.ainvoke(messages)
            content = response.content.strip()

            # 提取 JSON（使用统一工具函数）
            from app.core.utils import parse_llm_json
            try:
                plan = parse_llm_json(content)
                logger.info(f"[Orchestrator] 计划生成: {len(plan.get('steps', []))} 步")
                return plan
            except (ValueError, json.JSONDecodeError) as parse_err:
                logger.warning(f"[Orchestrator] 无法解析计划 JSON: {parse_err}, 内容: {content[:200]}")
                return {"goal": goal, "steps": [], "error": "无法生成执行计划"}
        except json.JSONDecodeError as je:
            logger.warning(f"[Orchestrator] JSON 解析失败: {je}, 内容: {content[:200]}")
            return {"goal": goal, "steps": [], "error": "执行计划格式无效，请重试"}
        except Exception as e:
            logger.error(f"[Orchestrator] 计划生成失败: {e}")
            if settings.DEMO_MODE:
                logger.info("[Orchestrator] ainvoke 失败，回退 demo plan")
                return self._demo_plan(goal)
            return {"goal": goal, "steps": [], "error": str(e)}

    async def execute(
        self,
        goal: str,
        steps: list[dict],
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict:
        """按序执行计划步骤，传递上下文"""
        step_results = []
        context = f"用户目标: {goal}\n"

        for i, step in enumerate(steps):
            agent_name = step.get("agent", "")
            task = step.get("task", "")

            if not agent_name or not task:
                step_results.append({
                    "step": i + 1,
                    "agent": agent_name,
                    "task": task,
                    "result": "跳过：无效步骤",
                    "error": True,
                })
                continue

            agent = get_agent_by_name(agent_name)
            if not agent:
                logger.warning(f"[Orchestrator] 未知 Agent: {agent_name}，回退通用 Agent")
                agent = get_agent_for_tool(None)

            # 构建上下文增强的查询
            enhanced_query = f"{context}\n当前任务: {task}"

            _t0 = time.perf_counter()
            try:
                result = await agent.run(
                    query=enhanced_query,
                    session_id=session_id,
                    user_id=user_id,
                )
                answer = result.get("answer", "")
                tool_calls = result.get("tool_calls", [])
                _t = time.perf_counter() - _t0

                step_results.append({
                    "step": i + 1,
                    "agent": agent_name,
                    "task": task,
                    "result": answer,
                    "tool_calls": tool_calls,
                    "duration_ms": int(_t * 1000),
                    "error": False,
                })

                context += f"\n步骤{i+1} ({agent_name}): {task}\n结果: {answer[:300]}{'...' if len(answer) > 300 else ''}\n"
                logger.info(
                    f"[Orchestrator] 步骤{i+1}/{len(steps)} {agent_name}: "
                    f"耗时={_t*1000:.0f}ms tools={len(tool_calls)}"
                )

            except Exception as e:
                _t = time.perf_counter() - _t0
                step_results.append({
                    "step": i + 1,
                    "agent": agent_name,
                    "task": task,
                    "result": f"执行失败: {e}",
                    "duration_ms": int(_t * 1000),
                    "error": True,
                })
                context += f"\n步骤{i+1} ({agent_name}): {task}\n结果: 执行失败 - {e}\n"
                logger.error(f"[Orchestrator] 步骤{i+1} 失败: {e}")

        # 汇总所有结果
        failures = [s for s in step_results if s.get("error")]
        success_count = len(step_results) - len(failures)

        return {
            "goal": goal,
            "total_steps": len(steps),
            "success_steps": success_count,
            "failed_steps": len(failures),
            "step_results": step_results,
            "context": context,
        }

    async def summarize(self, goal: str, step_results: list[dict]) -> str:
        """汇总生成最终报告 — 少于 4 步直接拼接，4+ 步用 LLM 汇总"""
        # 3 步以内：模板拼接（省一次 LLM 调用，约 5s）
        if len(step_results) <= 3:
            parts = []
            for sr in step_results:
                status = "[失败]" if sr.get("error") else "[OK]"
                result_text = sr.get("result", "")[:300].strip()
                parts.append(f"{status} {sr.get('task', '')}\n{result_text}")
            return "\n\n".join(parts)

        # 4 步以上：LLM 汇总（用 fast model）
        results_text = ""
        for sr in step_results:
            status = "[失败] 失败" if sr.get("error") else "[OK] 完成"
            results_text += f"\n步骤{sr['step']}: [{sr['agent']}] {sr['task']} — {status}\n"
            results_text += f"结果: {sr.get('result', '无')[:300]}\n"

        prompt = SUMMARY_PROMPT.format(goal=goal, step_results=results_text)
        llm = LLMFactory.get_llm(temperature=0.3, model="fast")
        try:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            return response.content.strip()
        except Exception as e:
            logger.error(f"[Orchestrator] 汇总失败: {e}")
            parts = [f"## {goal}"]
            for sr in step_results:
                parts.append(f"\n### 步骤{sr['step']}: {sr.get('task', '')}")
                parts.append(sr.get("result", "无结果")[:800])
            return "\n".join(parts)

    async def run(
        self,
        goal: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> dict:
        """完整流程: Plan → Execute → Summarize"""
        _t0 = time.perf_counter()

        # 1. Plan
        plan = await self.plan(goal)
        steps = plan.get("steps", [])

        if not steps:
            return {
                "answer": "抱歉，我无法为这个目标生成执行计划。请提供更具体的供应链问题。",
                "plan": plan,
                "execution": None,
                "duration_ms": int((time.perf_counter() - _t0) * 1000),
            }

        # 2. Execute
        execution = await self.execute(
            goal=plan.get("goal", goal),
            steps=steps,
            session_id=session_id,
            user_id=user_id,
        )

        # 3. Summarize
        summary = await self.summarize(
            goal=plan.get("goal", goal),
            step_results=execution["step_results"],
        )

        _t_total = time.perf_counter() - _t0
        logger.info(
            f"[Orchestrator] 完成: {len(steps)}步, "
            f"成功={execution['success_steps']}, 失败={execution['failed_steps']}, "
            f"总耗时={_t_total*1000:.0f}ms"
        )

        return {
            "answer": summary,
            "plan": plan,
            "execution": execution,
            "duration_ms": int(_t_total * 1000),
        }

    def _demo_plan(self, goal: str) -> dict:
        """DEMO_MODE 下根据 query 关键词生成 2-4 步动态执行计划（不依赖 LLM）"""
        goal_lower = goal.lower()

        # 关键词 → Agent 映射
        keyword_agents = [
            (["采购", "供应商", "准入", "资质", "比价", "purchase", "supplier"], "purchase"),
            (["库存", "物料", "缺货", "补货", "inventory", "stock", "material"], "inventory"),
            (["质检", "来料", "不合格", "缺陷", "quality", "inspection", "iqc"], "quality"),
            (["生产", "工单", "排产", "bom", "production", "ticket"], "production"),
        ]

        # 匹配 Agent
        matched_agents = []
        for keywords, agent in keyword_agents:
            if any(kw in goal_lower for kw in keywords):
                matched_agents.append(agent)

        if not matched_agents:
            matched_agents = ["purchase", "inventory"]

        steps = []
        for i, agent in enumerate(matched_agents[:4]):
            agent_labels = {"purchase": "采购部", "inventory": "仓库部", "quality": "质量部", "production": "生产部"}
            task_templates = {
                "purchase": f"检索「{goal}」相关的供应商管理与采购流程文档",
                "inventory": f"查询「{goal}」关联的库存数据与物料信息",
                "quality": f"检索「{goal}」相关的质量检验标准与处理流程",
                "production": f"检索「{goal}」相关的生产计划与工单信息",
            }
            steps.append({
                "step": i + 1,
                "agent": agent,
                "task": task_templates.get(agent, f"检索与「{goal}」相关的{agent_labels.get(agent, agent)}文档"),
                "depends_on": [i] if i > 0 else [],
            })

        if len(steps) < 2:
            steps.append({
                "step": len(steps) + 1,
                "agent": "inventory",
                "task": f"补充检索「{goal}」的库存与物料信息",
                "depends_on": [len(steps)],
            })

        return {
            "goal": goal,
            "steps": steps,
            "note": "[演示模式] 此为本地关键词匹配生成的模板计划，LLM 未连接。正式环境由 DeepSeek 动态生成。",
            "demo_info": {
                "mode": "demo",
                "reason": "LLM 不可用，使用本地模板计划",
                "matched_keywords": [kw for kw in goal_lower.split() if any(kw in g for g in [ks[0] for ks in keyword_agents])][:3],
            }
        }


# 全局单例
orchestrator = Orchestrator()
