"""
FastAPI 依赖注入 providers

统一管理所有全局单例的注入，便于测试时通过 dependency_overrides 替换。
"""
from typing import Optional
from app.agents.rag import RAGAgent, rag_agent
from app.agents.router import RouterAgent, router_agent
from app.agents.orchestrator import Orchestrator, orchestrator
from app.agents.tool import ToolAgent, tool_agent
from app.core.graph_engine import GraphEngine, graph_engine
from app.core.milvus_client import MilvusManager, milvus_manager
from app.core.neo4j_client import Neo4jClient, neo4j_client
from app.core.redis_client import RedisManager, ChatMemory, redis_manager, chat_memory
from app.core.rag.engine import RAGEngine, rag_engine as rag_engine_impl
from app.core.data_filter import PIIFilter
from app.core.query_analyzer import QueryComplexityAnalyzer, query_analyzer


def get_rag_agent() -> RAGAgent:
    """返回 RAG 问答 Agent 单例"""
    return rag_agent


def get_rag_engine() -> RAGEngine:
    """返回 RAG 检索核心引擎单例"""
    return rag_engine_impl


def get_router_agent() -> RouterAgent:
    """返回意图路由 Agent 单例"""
    return router_agent


def get_orchestrator_service() -> Orchestrator:
    """返回跨域工作流编排器单例"""
    return orchestrator


def get_tool_agent() -> ToolAgent:
    """返回工具调用 Agent 单例"""
    return tool_agent


def get_graph_engine() -> GraphEngine:
    """返回图谱查询引擎单例"""
    return graph_engine


def get_milvus_manager() -> MilvusManager:
    """返回 Milvus 向量数据库管理器单例"""
    return milvus_manager


def get_neo4j_client() -> Neo4jClient:
    """返回 Neo4j 图数据库客户端单例"""
    return neo4j_client


def get_redis_manager() -> RedisManager:
    """返回 Redis 连接管理器单例"""
    return redis_manager


def get_chat_memory() -> Optional[ChatMemory]:
    """返回对话记忆管理器（可能为 None，当 Redis 未连接时）"""
    return chat_memory


def get_pii_filter() -> PIIFilter:
    """返回 PII 脱敏过滤器实例"""
    return PIIFilter()


def get_query_analyzer() -> QueryComplexityAnalyzer:
    """返回查询复杂度分析器单例"""
    return query_analyzer


def get_cache_manager():
    """返回多层缓存统一门面单例（L1/L2/L3 + 命中率指标）"""
    from app.core.cache_manager import cache_manager
    return cache_manager
