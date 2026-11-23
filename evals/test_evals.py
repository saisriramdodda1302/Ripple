"""pytest view of the benchmark. Needs a running index of both repos and an LLM; run with
`pytest evals -m llm`."""

import os

import pytest

from evals.run_evals import load_questions, run_one

pytestmark = pytest.mark.llm

if not (os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("CHAT_MODEL", "").startswith("ollama")):
    pytest.skip("no LLM configured", allow_module_level=True)


@pytest.mark.parametrize("q", load_questions(), ids=lambda q: q["id"])
async def test_graph_mode_answers(q):
    r = await run_one(q, "graph")
    assert r["error"] is None, r["error"]
    assert r["passed"], f"missing: {r['missing']}"
