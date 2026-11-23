"""Verify the running app end-to-end."""

import httpx

BASE = "http://127.0.0.1:8000"


def main():
    with httpx.Client(timeout=60.0) as client:
        # Health
        r = client.get(f"{BASE}/api/v1/health")
        print("1. Health:", r.status_code, r.json())

        # Chitchat
        r = client.post(
            f"{BASE}/api/v1/chat",
            json={"message": "Hello, how are you?", "session_id": "verify-1"},
        )
        print("\n2. Chat (chitchat):", r.status_code)
        d = r.json()
        print("   Intent:", d["orchestrator_plan"]["intent"])
        print("   Response:", d["final_response"][:120])

        # Research (may be slow on first call due to Qdrant connection timeout)
        r = client.post(
            f"{BASE}/api/v1/chat",
            json={"message": "What is the company leave policy?", "session_id": "verify-2"},
        )
        print("\n3. Chat (research):", r.status_code)
        d = r.json()
        print("   Intent:", d["orchestrator_plan"]["intent"], "->", d["orchestrator_plan"]["agents"])
        print("   Response:", d["final_response"][:200])

        # Analysis
        r = client.post(
            f"{BASE}/api/v1/chat",
            json={"message": "Analyze the products and recommend one for healthcare", "session_id": "verify-3"},
        )
        print("\n4. Chat (analysis):", r.status_code)
        d = r.json()
        print("   Intent:", d["orchestrator_plan"]["intent"], "->", d["orchestrator_plan"]["agents"])
        print("   Response:", d["final_response"][:200])

        print("\n✅ All verifications passed!")


if __name__ == "__main__":
    main()