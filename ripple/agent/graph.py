import json
import re
import time
from typing import Literal, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from ..config import settings
from . import prompts, tools

Mode = Literal["graph", "vector"]


class AgentState(TypedDict, total=False):
    question: str
    repo: str | None
    mode: Mode
    plan: dict
    hits: list[dict]
    neighborhood: dict
    code: list[dict]
    answer: str
    citations: list[str]
    tokens: dict
    tool_calls: list[str]
    timings: dict


_llm = None


def llm():
    global _llm
    if _llm is None:
        _llm = init_chat_model(settings.chat_model, temperature=0)
    return _llm


def use_llm(model):
    """Swap the chat model, mostly so tests can plug in a fake one."""
    global _llm
    _llm = model


def _usage(msg) -> dict:
    u = getattr(msg, "usage_metadata", None) or {}
    return {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0)}


def _add_usage(state: AgentState, u: dict) -> dict:
    cur = state.get("tokens") or {"input": 0, "output": 0}
    return {"input": cur["input"] + u["input"], "output": cur["output"] + u["output"]}


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


#nodes.

async def plan(state: AgentState) -> AgentState:
    t = time.time()
    msg = await llm().ainvoke([HumanMessage(prompts.PLAN.format(question=state["question"]))])
    p = _parse_json(msg.content if isinstance(msg.content, str) else str(msg.content))
    p.setdefault("intent", "explain")
    p.setdefault("targets", [])
    p.setdefault("queries", [state["question"]])
    p["depth"] = int(p.get("depth") or 3)
    return {
        "plan": p,
        "tokens": _add_usage(state, _usage(msg)),
        "timings": {**state.get("timings", {}), "plan": time.time() - t},
    }


async def search(state: AgentState) -> AgentState:
    t = time.time()
    repo = state.get("repo")
    calls = list(state.get("tool_calls", []))
    seen, hits = set(), []

    for name in state["plan"].get("targets", [])[:4]:
        rows = await tools.call("get_symbol", name=name, repo=repo)
        calls.append(f"get_symbol({name})")
        for r in rows if isinstance(rows, list) else []:
            if r["id"] not in seen:
                seen.add(r["id"])
                r.pop("code", None)
                hits.append({**r, "via": "name"})

    for q in state["plan"].get("queries", [])[:3]:
        rows = await tools.call("search_code", query=q, repo=repo, k=6)
        calls.append(f"search_code({q!r})")
        for r in rows if isinstance(rows, list) else []:
            if r["id"] not in seen:
                seen.add(r["id"])
                hits.append({**r, "via": "vector"})

    return {"hits": hits, "tool_calls": calls,
            "timings": {**state.get("timings", {}), "search": time.time() - t}}


async def expand(state: AgentState) -> AgentState:
    t = time.time()
    repo = state.get("repo")
    calls = list(state.get("tool_calls", []))
    plan_ = state["plan"]
    hood: dict = {"callers": {}, "callees": {}, "impact": {}}

    focus = [h["qualname"] for h in state["hits"] if h.get("via") == "name"]
    if not focus:
        focus = [h["qualname"] for h in state["hits"][:3]]

    if plan_["intent"] == "impact":
        for name in focus[:2]:
            hood["impact"][name] = await tools.call(
                "impact_of", fn=name, depth=plan_["depth"], repo=repo)
            calls.append(f"impact_of({name}, depth={plan_['depth']})")
    for name in focus[:3]:
        hood["callees"][name] = await tools.call("find_callees", fn=name, repo=repo)
        calls.append(f"find_callees({name})")
        if plan_["intent"] != "impact":
            hood["callers"][name] = await tools.call("find_callers", fn=name, repo=repo)
            calls.append(f"find_callers({name})")

    return {"neighborhood": hood, "tool_calls": calls,
            "timings": {**state.get("timings", {}), "expand": time.time() - t}}


async def read(state: AgentState) -> AgentState:
    t = time.time()
    repo = state.get("repo")
    calls = list(state.get("tool_calls", []))
    hood = state.get("neighborhood") or {}

    want: list[str] = []
    for h in state["hits"]:
        want.append(h["qualname"])
    for by_name in hood.get("callees", {}).values():
        want += [r["qualname"] for r in by_name[:4]]
    for by_name in hood.get("callers", {}).values():
        want += [r["qualname"] for r in by_name[:4]]
    for imp in hood.get("impact", {}).values():
        want += [a["qualname"] for a in imp.get("affected", [])[:6]]

    budget = 24_000
    code, seen, used = [], set(), 0
    for name in dict.fromkeys(want):
        if used > budget or len(code) >= 14:
            break
        rows = await tools.call("get_symbol", name=name, repo=repo)
        calls.append(f"get_symbol({name})")
        for r in rows if isinstance(rows, list) else []:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            snippet = r["code"]
            if len(snippet) > 4000:
                snippet = snippet[:4000] + "\n# ... truncated"
            r["code"] = snippet
            used += len(snippet)
            code.append(r)
    return {"code": code, "tool_calls": calls,
            "timings": {**state.get("timings", {}), "read": time.time() - t}}


def _render_context(state: AgentState) -> str:
    parts = []
    hood = state.get("neighborhood") or {}

    for name, imp in hood.get("impact", {}).items():
        if not imp.get("found"):
            continue
        parts.append(f"## Impact walk from {name} (depth {imp['depth']})")
        parts.append(f"affected by depth: {imp.get('count_by_depth')}")
        for a in imp["affected"][:60]:
            tag = " [entry point]" if a.get("is_entry") else ""
            parts.append(f"- d{a['depth']} {a['kind']} {a['qualname']} "
                         f"({a['path']}:{a['start_line']}-{a['end_line']}){tag}")
        parts.append(f"files touched: {', '.join(imp['files_touched'][:30])}")

    for label in ("callers", "callees"):
        for name, rows in hood.get(label, {}).items():
            if not rows:
                continue
            parts.append(f"## {label} of {name}")
            for r in rows[:25]:
                parts.append(f"- {r['qualname']} ({r['path']}:{r['start_line']}) "
                             f"call at line {r['call_line']} [{r['resolved_by']}]")

    parts.append("## Source")
    for c in state.get("code", []):
        parts.append(f"### {c['qualname']}  —  {c['path']}:{c['start_line']}-{c['end_line']}")
        parts.append("```\n" + _number_lines(c["code"], c["start_line"]) + "\n```")
    return "\n".join(parts)


def _number_lines(code: str, start: int) -> str:
    return "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(code.splitlines()))


CITE_RE = re.compile(r"`?([\w./\\-]+\.(?:py|ts|tsx|js|jsx)):(\d+)(?:-(\d+))?`?")


async def answer(state: AgentState) -> AgentState:
    t = time.time()
    if not state.get("code"):
        return {"answer": prompts.EXPLAIN_FALLBACK, "citations": [],
                "timings": {**state.get("timings", {}), "answer": time.time() - t}}
    prompt = prompts.ANSWER.format(question=state["question"], context=_render_context(state))
    msg = await llm().ainvoke([HumanMessage(prompt)])
    text = msg.content if isinstance(msg.content, str) else str(msg.content)
    cites = sorted({f"{m.group(1)}:{m.group(2)}" for m in CITE_RE.finditer(text)})
    return {
        "answer": text, "citations": cites,
        "tokens": _add_usage(state, _usage(msg)),
        "timings": {**state.get("timings", {}), "answer": time.time() - t},
    }


def route_after_search(state: AgentState) -> str:
    if state.get("mode") == "vector":
        return "read"
    return "expand"


def build():
    g = StateGraph(AgentState)
    g.add_node("plan", plan)
    g.add_node("search", search)
    g.add_node("expand", expand)
    g.add_node("read", read)
    g.add_node("answer", answer)
    g.set_entry_point("plan")
    g.add_edge("plan", "search")
    g.add_conditional_edges("search", route_after_search, {"expand": "expand", "read": "read"})
    g.add_edge("expand", "read")
    g.add_edge("read", "answer")
    g.add_edge("answer", END)
    return g.compile()


_graph = None


async def ask(question: str, repo: str | None = None, mode: Mode = "graph") -> AgentState:
    global _graph
    if _graph is None:
        _graph = build()
    t0 = time.time()
    out = await _graph.ainvoke({"question": question, "repo": repo, "mode": mode,
                                "tokens": {"input": 0, "output": 0}, "tool_calls": []})
    out["timings"]["total"] = time.time() - t0
    return out
