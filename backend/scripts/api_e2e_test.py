# -*- coding: utf-8 -*-
"""
SupplyChainRAG - API-level E2E test
=================================
Simulates browser flow via API calls (no browser needed).
Tests: Login -> RAG Query -> Tool Call -> SSE Response
"""
import sys, os, json, time, httpx, asyncio
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_URL = "http://localhost:8001"
results = []

def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    results.append({"name": name, "status": status, "detail": detail})
    emoji = "\u2705" if ok else "\u274c"
    print(f"  {emoji} {name}: {detail}")
    return ok


async def main():
    print("=" * 60)
    print("SupplyChainRAG - API-level E2E Test")
    print("=" * 60)
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        # 1. Health check
        print("\n[1] Health Check")
        try:
            r = await client.get("/health")
            check("Health endpoint", r.status_code == 200, f"Status: {r.status_code}")
        except Exception as e:
            check("Health endpoint", False, str(e))
            print("\nBackend not running! Start with: uvicorn app.main:app --port 8001")
            return
        
        # 2. Login
        print("\n[2] Authentication")
        try:
            r = await client.post("/api/v1/auth/login", json={
                "username": "admin",
                "password": "admin123"
            })
            if r.status_code == 200:
                token = r.json().get("access_token", "")
                check("Login", True, f"Token received ({len(token)} chars)")
                headers = {"Authorization": f"Bearer {token}"}
            else:
                check("Login", False, f"Status: {r.status_code}, Body: {r.text[:100]}")
                headers = {}
        except Exception as e:
            check("Login", False, str(e))
            headers = {}
        
        # 3. RAG Query
        print("\n[3] RAG Query")
        try:
            r = await client.post("/api/v1/chat/query", 
                json={"question": "供应商准入需要哪些资质文件？"},
                headers=headers
            )
            if r.status_code == 200:
                data = r.json()
                answer = data.get("answer", "")
                sources = data.get("sources", [])
                confidence = data.get("confidence", 0)
                has_citation = "[" in answer and "]" in answer
                check("RAG Query", True, f"Confidence: {confidence:.3f}, Sources: {len(sources)}")
                check("Citation marks", has_citation, f"Has [1][2]: {has_citation}")
                check("Answer length", len(answer) > 50, f"Length: {len(answer)} chars")
            else:
                check("RAG Query", False, f"Status: {r.status_code}")
        except Exception as e:
            check("RAG Query", False, str(e))
        
        # 4. Tool Call - Query Inventory
        print("\n[4] Tool Calls")
        try:
            r = await client.post("/api/v1/chat/query",
                json={"question": "MAT-001 的库存有多少？"},
                headers=headers
            )
            if r.status_code == 200:
                data = r.json()
                answer = data.get("answer", "")
                check("Inventory Query", True, f"Answer: {answer[:100]}...")
            else:
                check("Inventory Query", False, f"Status: {r.status_code}")
        except Exception as e:
            check("Inventory Query", False, str(e))
        
        # 5. Tool Call - Query Order
        try:
            r = await client.post("/api/v1/chat/query",
                json={"question": "查询采购订单 PO-2025-001 的状态"},
                headers=headers
            )
            if r.status_code == 200:
                data = r.json()
                answer = data.get("answer", "")
                check("Order Query", True, f"Answer: {answer[:100]}...")
            else:
                check("Order Query", False, f"Status: {r.status_code}")
        except Exception as e:
            check("Order Query", False, str(e))
        
        # 6. Knowledge Base Query
        print("\n[5] Knowledge Base")
        try:
            r = await client.post("/api/v1/chat/query",
                json={"question": "库存ABC分类法中A类物料的标准是什么？"},
                headers=headers
            )
            if r.status_code == 200:
                data = r.json()
                answer = data.get("answer", "")
                sources = data.get("sources", [])
                check("KB Query", True, f"Sources: {len(sources)}, Answer: {answer[:80]}...")
            else:
                check("KB Query", False, f"Status: {r.status_code}")
        except Exception as e:
            check("KB Query", False, str(e))
    
    # Summary
    passed = sum(1 for r in results if r["status"] == "PASS")
    total = len(results)
    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed}/{total} passed")
    print(f"{'='*60}")
    
    # Save results
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                            "eval", "api_e2e_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), 
                   "passed": passed, "total": total, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
