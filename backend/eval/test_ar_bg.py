"""后台测试 AnswerRelevancy strictness=1"""
import asyncio
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from ragas.dataset_schema import SingleTurnSample
from ragas.metrics._answer_relevance import AnswerRelevancy
from langchain_openai import ChatOpenAI
from app.core.rag_engine import rag_engine
from app.core.milvus_client import milvus_manager

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_ar_result.txt")


async def test():
    results = []

    t0 = time.time()
    results.append(f"[{time.time()-t0:.1f}s] Starting test...")

    milvus_manager.connect()
    results.append(f"[{time.time()-t0:.1f}s] Milvus connected")

    rag_engine.embedding.init()
    results.append(f"[{time.time()-t0:.1f}s] Embedding model loaded")

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

    results.append(f"[{time.time()-t0:.1f}s] Testing AnswerRelevancy strictness=1...")

    try:
        score = await metric._ascore(sample.to_dict(), callbacks=[])
        results.append(f"[{time.time()-t0:.1f}s] Score: {score}")
    except Exception as e:
        results.append(f"[{time.time()-t0:.1f}s] ERROR: {type(e).__name__}: {e}")

    results.append(f"[{time.time()-t0:.1f}s] Done!")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(results))

    print("\n".join(results))


if __name__ == "__main__":
    asyncio.run(test())
