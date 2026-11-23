"""Direct test using FastAPI TestClient (no HTTP server needed)."""

import asyncio
import json

from fastapi.testclient import TestClient

from app.main import app


def main():
    client = TestClient(app)

    # Health
    r = client.get("/api/v1/health")
    print("HEALTH:", r.status_code, r.json())

    # Chat - chitchat (should not need Qdrant)
    r = client.post(
        "/api/v1/chat",
        json={"message": "Hello, how are you?", "session_id": "test-1"},
    )
    print("\nCHAT (chitchat):", r.status_code)
    if r.status_code == 200:
        data = r.json()
        print("Plan:", json.dumps(data.get("orchestrator_plan", {}), indent=2))
        print("Final response:\n", data.get("final_response", ""))
    else:
        print("Error:", r.text)

    # Chat - research
    r = client.post(
        "/api/v1/chat",
        json={"message": "What is the company leave policy?", "session_id": "test-2"},
    )
    print("\nCHAT (research):", r.status_code)
    if r.status_code == 200:
        data = r.json()
        print("Plan:", json.dumps(data.get("orchestrator_plan", {}), indent=2))
        print("Final response:\n", data.get("final_response", ""))
    else:
        print("Error:", r.text)

    print("\nAll direct tests completed!")


if __name__ == "__main__":
    main()