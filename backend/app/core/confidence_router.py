"""
SmartQA - 三层置信度路由策略

【学习要点】
根据 RAG 检索置信度，动态选择不同的处理策略：
1. 低置信度 (<0.3)：知识库覆盖不足，用 Web 搜索补充
2. 中置信度 (0.3-0.7)：可能有错别字或表述不清，改写 query 重试
3. 高置信度 (>0.7)：知识库命中准确，直接生成

面试话术："置信度不只是展示给用户的标签，它驱动了不同的处理路径。
低置信度时系统会主动搜索外部信息补充，中置信度时会尝试改写用户问题，
只有高置信度时才直接回答。这比一刀切的 RAG 更智能。"
"""
import logging
import hashlib
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceDecision:
    """置信度路由决策"""
    tier: str  # "high" / "medium" / "low"
    strategy: str  # "direct" / "rewrite" / "web_search"
    confidence: float
    description: str


@dataclass
class WebSearchResult:
    """Web 搜索结果"""
    title: str
    link: str
    snippet: str
    date: str = ""


class ConfidenceRouter:
    """三层置信度路由器"""

    LOW_THRESHOLD = 0.3
    HIGH_THRESHOLD = 0.7

    def decide(self, confidence: float, query: str) -> ConfidenceDecision:
        """根据置信度决定处理策略"""
        if confidence >= self.HIGH_THRESHOLD:
            return ConfidenceDecision(
                tier="high", strategy="direct",
                confidence=confidence,
                description="知识库命中准确，直接生成回答"
            )
        elif confidence >= self.LOW_THRESHOLD:
            return ConfidenceDecision(
                tier="medium", strategy="rewrite",
                confidence=confidence,
                description="置信度中等，尝试改写查询后重新检索"
            )
        else:
            return ConfidenceDecision(
                tier="low", strategy="web_search",
                confidence=confidence,
                description="知识库覆盖不足，搜索外部信息补充"
            )

    async def rewrite_query(self, query: str, llm_factory) -> list[str]:
        """用 LLM 改写查询，生成多个变体"""
        from langchain_core.messages import SystemMessage, HumanMessage

        rewrite_prompt = """你是查询改写专家。用户的问题可能存在错别字、表述不清或过于简短。
请生成 2-3 个改写版本，要求：
1. 修正错别字
2. 补充缺失的上下文
3. 使用标准术语（供应链/采购/库存/质检/物流领域）
4. 保持原意不变

输出格式：每行一个改写版本，不要编号。"""

        try:
            llm = llm_factory.get_llm(temperature=0.3, streaming=False)
            response = await llm.ainvoke([
                SystemMessage(content=rewrite_prompt),
                HumanMessage(content=f"原始问题：{query}"),
            ])
            rewrites = [
                line.strip()
                for line in response.content.strip().split('\n')
                if line.strip() and line.strip() != query
            ]
            logger.info(f"[QueryRewrite] 原始: {query} → 改写: {rewrites}")
            return rewrites[:3]
        except Exception as e:
            logger.warning(f"[QueryRewrite] 改写失败: {e}")
            return []

    async def web_search(self, query: str, api_key: str = "") -> list[WebSearchResult]:
        """Web 搜索：调用 MiniMax API 获取外部搜索结果
        
        使用 MiniMax 的 OpenAI 兼容接口进行 web search。
        如果 API 不可用，返回空列表（优雅降级）。
        """
        if not api_key:
            logger.warning("[WebSearch] MiniMax API key 未配置")
            return []

        try:
            import httpx

            # 构造供应链相关的搜索 query
            search_query = f"供应链管理 {query}"
            logger.info(f"[WebSearch] 搜索: {search_query}")

            # 调用 MiniMax Chat API，让它基于搜索结果回答
            # MiniMax 的 ChatCompletion 支持 web_search 功能
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://api.minimax.chat/v1/text/chatcompletion_v2",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "MiniMax-M2.7",
                        "messages": [
                            {
                                "role": "system",
                                "content": "你是供应链管理专家。请基于搜索结果回答用户问题。如果没有相关信息，直接说'未找到相关信息'。"
                            },
                            {
                                "role": "user",
                                "content": search_query
                            }
                        ],
                        "tools": [
                            {
                                "type": "web_search",
                                "web_search": {
                                    "enabled": True
                                }
                            }
                        ],
                        "max_tokens": 1024,
                        "temperature": 0.3,
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    # 解析 MiniMax 响应
                    content = ""
                    if "choices" in data and data["choices"]:
                        content = data["choices"][0].get("message", {}).get("content", "")
                    
                    if content:
                        logger.info(f"[WebSearch] 获取到结果: {content[:100]}...")
                        return [WebSearchResult(
                            title="Web搜索结果",
                            link="",
                            snippet=content,
                        )]
                else:
                    logger.warning(f"[WebSearch] API 返回 {response.status_code}: {response.text[:200]}")

        except httpx.TimeoutException:
            logger.warning("[WebSearch] 请求超时")
        except Exception as e:
            logger.warning(f"[WebSearch] 搜索失败: {e}")

        # 降级：用简单的 httpx 调用公开搜索 API
        try:
            return await self._fallback_search(query)
        except Exception as e:
            logger.warning(f"[WebSearch] 降级搜索也失败: {e}")
            return []

    async def _fallback_search(self, query: str) -> list[WebSearchResult]:
        """降级搜索：用 DuckDuckGo Instant Answer API（免费，无需 key）"""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://api.duckduckgo.com/",
                    params={
                        "q": f"供应链 {query}",
                        "format": "json",
                        "no_html": 1,
                        "skip_disambig": 1,
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    results = []
                    # Abstract（摘要）
                    if data.get("Abstract"):
                        results.append(WebSearchResult(
                            title=data.get("Heading", ""),
                            link=data.get("AbstractURL", ""),
                            snippet=data.get("Abstract", ""),
                        ))
                    # RelatedTopics
                    for topic in data.get("RelatedTopics", [])[:3]:
                        if isinstance(topic, dict) and topic.get("Text"):
                            results.append(WebSearchResult(
                                title=topic.get("Text", "")[:50],
                                link=topic.get("FirstURL", ""),
                                snippet=topic.get("Text", ""),
                            ))
                    if results:
                        logger.info(f"[WebSearch-Fallback] 获取到 {len(results)} 条结果")
                        return results
        except Exception as e:
            logger.warning(f"[WebSearch-Fallback] 失败: {e}")

        return []

    def format_web_results_for_context(self, results: list[WebSearchResult]) -> str:
        """将 Web 搜索结果格式化为 context 字符串，拼入 LLM prompt"""
        if not results:
            return ""
        
        lines = ["以下是从外部搜索获取的补充信息（非内部知识库）：\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[Web-{i}] {r.title}")
            if r.snippet:
                lines.append(f"  {r.snippet}")
            if r.link:
                lines.append(f"  来源: {r.link}")
            lines.append("")
        
        return "\n".join(lines)


# 单例
_confidence_router: Optional[ConfidenceRouter] = None

def get_confidence_router() -> ConfidenceRouter:
    global _confidence_router
    if _confidence_router is None:
        _confidence_router = ConfidenceRouter()
    return _confidence_router
