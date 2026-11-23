"""Quick end-to-end test of the Multi-Agent Enterprise Assistant API."""

import asyncio
import json

import httpx

BASE = "http://127.0.0.1:8000"


async def main():
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Health
        r = await client.get(f"{BASE}/api/v1/health")
        print("HEALTH:", r.status_code, r.json())

        # Chat - research query
        r = await client.post(
            f"{BASE}/api/v1/chat",
            json={"message": "What is the company leave policy?", "session_id": "test-1"},
        )
        print("\nCHAT (research):", r.status_code)
        data = r.json()
        print("Plan:", json.dumps(data.get("orchestrator_plan", {}), indent=2))
        print("Final response:\n", data.get("final_response", ""))
        print("Agent results:", len(data.get("agent_results", [])))
        for ar in data.get("agent_results", []):
            print(f"  - {ar['agent']}: {list(ar['data'].keys())}")

        # Chat - analysis query
        r = await client.post(
            f"{BASE}/api/v1/chat",
            json={"message": "Analyze the product catalog and recommend which product is best for healthcare", "session_id": "test-2"},
        )
        print("\nCHAT (analysis):", r.status_code)
        data = r.json()
        print("Plan:", json.dumps(data.get("orchestrator_plan", {}), indent=2))
        print("Final response:\n", data.get("final_response", ""))

        # Chat - chitchat
        r = await client.post(
            f"{BASE}/api/v1/chat",
            json={"message": "Hello, how are you?", "session_id": "test-3"},
        )
        print("\nCHAT (chitchat):", r.status_code)
        data = r.json()
        print("Final response:\n", data.get("final_response", ""))

        # Ingest text
        r = await client.post(
            f"{BASE}/api/v1/ingest",
            json={"text": "Our refund policy allows returns within 30 days with a receipt.", "source": "refund-policy.txt"},
        )
        print("\nINGEST:", r.status_code, r.json())

        # Search
        r = await client.post(
            f"{BASE}/api/v1/search",
            json={"query": "refund policy", "top_k": 3},
        )
        print("\nSEARCH:", r.status_code)
        data = r.json()
        print("Count:", data.get("count"))
        for res in data.get("results", []):
            print(f"  - {res['source']} (score: {res.get('score')}): {res['text'][:80]}...")

        print("\nAll tests passed!")


if __name__ == "__main__":
    asyncio.run(main())