"""Review Agent deterministic checks and verdict behaviour."""

import asyncio

from app.agents.reviewer import ReviewAgent
from app.memory.store import MemoryStore


def run(answer, chunks=None, question="What is the policy?"):
    agent = ReviewAgent()
    return asyncio.run(
        agent.run(
            task_id=MemoryStore.generate_id(),
            session_id="test-review",
            input_data={"question": question, "answer": answer, "chunks": chunks or []},
        )
    )


def test_empty_answer_flagged():
    r = run("")
    assert r["verdict"] == "revise"
    assert any("empty" in i.lower() for i in r["issues"])


def test_missing_citation_flagged():
    answer = (
        "Employees receive twenty paid days of leave every calendar year, "
        "and unused days may be carried forward into the next year according to policy."
    )
    chunks = [{"source": "handbook.md", "text": "Employees receive 20 paid days per year."}]
    r = run(answer, chunks)
    assert r["verdict"] == "revise"
    assert any("cite" in i.lower() for i in r["issues"])


def test_fabricated_source_flagged():
    answer = "See [source: made-up.md] for details of the policy that is long enough to be checked."
    chunks = [{"source": "handbook.md", "text": "Policy details here."}]
    r = run(answer, chunks)
    assert r["verdict"] == "revise"
    assert any("made-up.md" in i for i in r["issues"])


def test_grounded_answer_passes():
    answer = "Employees get 20 paid days. [source: handbook.md]"
    chunks = [{"source": "handbook.md", "text": "Employees get 20 paid days per year."}]
    r = run(answer, chunks)
    assert r["verdict"] == "pass"
    assert r["issues"] == []
    assert r["llm_checked"] is False  # REVIEW_LLM_ENABLED=false in tests


def test_review_result_persisted_in_task_context():
    agent = ReviewAgent()
    tid = MemoryStore.generate_id()
    r = asyncio.run(
        agent.run(
            task_id=tid,
            session_id="test-review",
            input_data={"question": "Q?", "answer": "A long enough answer without issues.", "chunks": []},
        )
    )
    ctx = MemoryStore().get_task_context(tid)
    assert ctx["review"]["verdict"] == r["verdict"]
