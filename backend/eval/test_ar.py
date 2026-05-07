"""快速测试 AnswerRelevancy strictness=1 是否能正常工作"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from ragas.dataset_schema import SingleTurnSample
from ragas.metrics._answer_relevance import AnswerRelevancy
from langchain_openai import ChatOpenAI
from app.core.rag_engine import rag_engine
from app.core.milvus_client import milvus_manager


async def test():
    milvus_manager.connect()
    rag_engine.embedding.init()

    judge_llm = ChatOpenAI(
        model="qwen2.5-7b-instruct-q3_k_m.gguf",
        base_url="http://localhost:8081/v1",
        api_key="sk-no-key-needed",
        temperature=0.0,
        max_tokens=256,
        max_retries=3,
    )

    metric = AnswerRelevancy()
    metric.strictness = 1
    metric.llm = judge_llm
    metric.embeddings = rag_engine.embedding._model

    sample = SingleTurnSample(
        user_input="VPN服务器地址是什么？",
        response="VPN服务器地址是 vpn.example.com，端口号为 443。",
    )

    print("Testing AnswerRelevancy with strictness=1...")
    score = await metric._ascore(sample.to_dict(), callbacks=[])
    print(f"Score: {score}")


if __name__ == "__main__":
    asyncio.run(test())
