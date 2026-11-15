import asyncio
import json

import typer
from rich import print as rprint
from rich.table import Table

app = typer.Typer(help="ripple: ask questions about a codebase, see what breaks before you change it")


@app.command()
def init_db():
    """Create tables and the pgvector extension."""
    from . import db
    db.init_schema()
    rprint("[green]schema ready[/green]")


@app.command()
def index(source: str, name: str = typer.Option(None, help="repo name, defaults to the last URL segment"),
          no_embed: bool = typer.Option(False, "--no-embed", help="skip embeddings (keyword search only)")):
    """Clone (or reuse) a repo, parse it and store symbols, call graph and embeddings."""
    from .indexer.run import index_repo
    index_repo(source, name, embed=not no_embed, log=rprint)


@app.command()
def repos():
    from . import store
    t = Table("name", "files", "symbols", "edges", "commit", "indexed")
    for r in store.list_repos():
        t.add_row(r["name"], str(r["files"]), str(r["symbols"]), str(r["edges"]),
                  (r["commit_sha"] or "")[:8], str(r["indexed_at"] or "")[:19])
    rprint(t)


@app.command()
def ask(question: str, repo: str = typer.Option(None), mode: str = typer.Option("graph", help="graph | vector"),
        show_trace: bool = typer.Option(False, "--trace"), no_cache: bool = typer.Option(False, "--no-cache")):
    """Run the agent on a question."""
    from . import cache
    from .agent.graph import ask as run

    if not no_cache:
        hit = cache.get(repo, mode, question)
        if hit:
            rprint(hit["answer"])
            rprint("[dim](cached)[/dim]")
            return
    out = asyncio.run(run(question, repo=repo, mode=mode))
    rprint(out["answer"])
    rprint(f"\n[dim]{out['timings']['total']:.1f}s, tokens in/out "
           f"{out['tokens']['input']}/{out['tokens']['output']}, "
           f"{len(out['tool_calls'])} tool calls[/dim]")
    if show_trace:
        rprint(json.dumps({"plan": out["plan"], "tool_calls": out["tool_calls"]}, indent=1))
    if not no_cache:
        cache.put(repo, mode, question, out)


@app.command()
def impact(fn: str, depth: int = 3, repo: str = typer.Option(None),
           min_confidence: float = typer.Option(0.0, help="drop edges below this; 0.5 keeps only typed/import/self/inherited"),
           no_tests: bool = typer.Option(False, "--no-tests")):
    """Call-graph walk only, no LLM. Prints what depends on `fn`."""
    from . import store
    from .indexer.graph import is_test_path
    rid = store.repo_id_for(repo)
    if rid is None:
        raise typer.Exit("no indexed repo")
    res = store.impact_of(rid, fn, depth=depth, min_confidence=min_confidence)
    if not res["found"]:
        rprint(f"[red]{fn} not found[/red]")
        raise typer.Exit(1)
    t = Table("depth", "symbol", "location", "conf", "entry")
    for a in res["affected"]:
        if no_tests and is_test_path(a["path"]):
            continue
        t.add_row(str(a["depth"]), a["qualname"], f"{a['path']}:{a['start_line']}",
                  f"{a['confidence']:.2f}", "yes" if a["is_entry"] else "")
    rprint(t)
    rprint(f"{len(res['affected'])} symbols across {len(res['files_touched'])} files")


@app.command()
def mcp():
    """Run the MCP server on stdio (for Claude Desktop, Cursor, ...)."""
    from .mcp_server import main
    main()


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    uvicorn.run("ripple.api:app", host=host, port=port)


@app.command()
def evals(repo: str = typer.Option(None, help="only run questions for this repo"),
          modes: str = typer.Option("vector,graph")):
    """Run the benchmark and write evals/results/."""
    from evals.run_evals import main
    main(repo_filter=repo, modes=modes.split(","))


if __name__ == "__main__":
    app()
