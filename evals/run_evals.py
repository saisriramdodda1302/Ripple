"""Runs every question in evals/questions/ through the agent in each mode and writes
results/<timestamp>.json plus results/latest.md with the comparison table."""

import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.scoring import score_answer
from ripple.agent.graph import ask

HERE = Path(__file__).resolve().parent
QUESTIONS_DIR = HERE / "questions"
RESULTS_DIR = HERE / "results"


def load_questions(repo_filter=None):
    out = []
    for path in sorted(QUESTIONS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        if repo_filter and data["repo"] != repo_filter:
            continue
        for q in data["questions"]:
            out.append({**q, "repo": data["repo"]})
    return out


async def run_one(q, mode):
    t = time.time()
    try:
        res = await ask(q["question"], repo=q["repo"], mode=mode)
        err = None
    except Exception as e:  # keep going, record the failure
        res, err = {"answer": "", "citations": [], "tokens": {"input": 0, "output": 0},
                    "timings": {}, "tool_calls": []}, repr(e)
    elapsed = time.time() - t
    sc = score_answer(res["answer"], res.get("citations", []), q["expect"], q.get("files", []))
    return {
        "id": q["id"], "repo": q["repo"], "type": q["type"], "mode": mode,
        "question": q["question"], "answer": res["answer"], "citations": res.get("citations", []),
        "passed": sc.passed, "coverage": round(sc.coverage, 3), "missing": sc.missing,
        "seconds": round(elapsed, 2),
        "tokens_in": res["tokens"]["input"], "tokens_out": res["tokens"]["output"],
        "tool_calls": len(res.get("tool_calls", [])), "error": err,
    }


def summarise(rows):
    by_mode = {}
    for mode in sorted({r["mode"] for r in rows}):
        rs = [r for r in rows if r["mode"] == mode]
        impact = [r for r in rs if r["type"] == "impact"]
        other = [r for r in rs if r["type"] != "impact"]
        by_mode[mode] = {
            "n": len(rs),
            "accuracy": round(sum(r["passed"] for r in rs) / len(rs), 3),
            "accuracy_impact": round(sum(r["passed"] for r in impact) / len(impact), 3) if impact else None,
            "accuracy_explain": round(sum(r["passed"] for r in other) / len(other), 3) if other else None,
            "coverage": round(statistics.mean(r["coverage"] for r in rs), 3),
            "p50_seconds": round(statistics.median(r["seconds"] for r in rs), 2),
            "mean_seconds": round(statistics.mean(r["seconds"] for r in rs), 2),
            "mean_tokens": round(statistics.mean(r["tokens_in"] + r["tokens_out"] for r in rs)),
            "mean_tool_calls": round(statistics.mean(r["tool_calls"] for r in rs), 1),
        }
    return by_mode


def render_markdown(summary, rows, model):
    lines = [f"Model: `{model}`  ·  {datetime.now():%Y-%m-%d %H:%M}", "",
             "| mode | accuracy | impact qs | explain qs | mean coverage | p50 latency | mean tokens | tool calls |",
             "|---|---|---|---|---|---|---|---|"]
    for mode, s in summary.items():
        lines.append(
            f"| {mode} | {s['accuracy']:.0%} ({round(s['accuracy'] * s['n'])}/{s['n']}) "
            f"| {s['accuracy_impact']:.0%} | {s['accuracy_explain']:.0%} | {s['coverage']:.0%} "
            f"| {s['p50_seconds']}s | {s['mean_tokens']} | {s['mean_tool_calls']} |")
    lines += ["", "| id | type | " + " | ".join(summary) + " |", "|---|---|" + "---|" * len(summary)]
    ids = list(dict.fromkeys(r["id"] for r in rows))
    for qid in ids:
        cells = []
        for mode in summary:
            r = next((x for x in rows if x["id"] == qid and x["mode"] == mode), None)
            if r is None:
                cells.append("–")
            else:
                mark = "pass" if r["passed"] else f"miss: {'; '.join(r['missing'])[:60]}"
                cells.append(f"{mark} ({r['seconds']}s)")
        qtype = next(x["type"] for x in rows if x["id"] == qid)
        lines.append(f"| {qid} | {qtype} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


async def run_all(repo_filter, modes):
    questions = load_questions(repo_filter)
    rows = []
    for mode in modes:
        for q in questions:
            r = await run_one(q, mode)
            rows.append(r)
            flag = "ok " if r["passed"] else "MISS"
            print(f"[{mode:6}] {flag} {r['id']}  {r['seconds']:5.1f}s  "
                  f"{r['tokens_in'] + r['tokens_out']:6d} tok  {r['missing'] or ''}")
    return rows


def main(repo_filter=None, modes=("vector", "graph")):
    from ripple.config import settings
    rows = asyncio.run(run_all(repo_filter, list(modes)))
    summary = summarise(rows)
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (RESULTS_DIR / f"{stamp}.json").write_text(
        json.dumps({"model": settings.chat_model, "summary": summary, "rows": rows}, indent=1))
    md = render_markdown(summary, rows, settings.chat_model)
    (RESULTS_DIR / "latest.md").write_text(md)
    print("\n" + md)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo")
    ap.add_argument("--modes", default="vector,graph")
    a = ap.parse_args()
    main(a.repo, a.modes.split(","))
