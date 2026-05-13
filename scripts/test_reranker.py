import sys, os
sys.path.insert(0, r"C:\Users\sss208\Desktop\agent\supply-chain-qa\backend")
os.chdir(r"C:\Users\sss208\Desktop\agent\supply-chain-qa\backend")

print("Testing reranker model loading...")
from app.config import get_settings
settings = get_settings()
print(f"Model: {settings.RERANKER_MODEL}, Device: {settings.RERANKER_DEVICE}")

from app.core.rag_engine import RerankerEngine
reranker = RerankerEngine()
print("Engine created, calling init()...")
reranker.init()
print(f"Model loaded: {reranker._model is not None}")
if reranker._model:
    print("Testing rerank with sample data...")
    docs = [
        {"content": "供应商准入需要提供营业执照和ISO认证", "score": 0.8},
        {"content": "今天天气很好适合出去散步", "score": 0.75},
        {"content": "ISO 9001质量管理体系认证是供应商准入的必须条件", "score": 0.7},
    ]
    result = reranker.rerank("供应商需要什么认证", docs, top_k=2)
    for r in result:
        print(f"  score={r['rerank_score']:.4f} content={r['content'][:50]}...")
print("DONE!")
