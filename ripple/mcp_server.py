import json

from mcp.server.fastmcp import FastMCP

from . import store

mcp = FastMCP(
    "ripple",
    instructions=(
        "Tools for navigating an indexed codebase. Symbols can be referred to by bare name "
        "(`send`), dotted name (`Session.send`) or `path/to/file.py:send`. "
        "Omit `repo` to use the most recently indexed one."
    ),
)


def _dump(obj) -> str:
    return json.dumps(obj, default=str, indent=1)


def _repo(repo: str | None) -> int:
    rid = store.repo_id_for(repo)
    if rid is None:
        raise ValueError(f"no indexed repo named {repo!r}; run `ripple index <url>` first")
    return rid


@mcp.tool()
def list_repos() -> str:
    """List indexed repositories with file, symbol and call-edge counts."""
    return _dump(store.list_repos())


@mcp.tool()
def search_code(query: str, repo: str | None = None, k: int = 8) -> str:
    """Semantic search over functions, methods and classes. Returns the closest symbols
    with file path and line range. Good first step when you don't know where something lives."""
    return _dump(store.search_code(_repo(repo), query, k=k))


@mcp.tool()
def get_symbol(name: str, repo: str | None = None) -> str:
    """Fetch the full source of a function/class by name, with file path and line numbers."""
    return _dump(store.get_symbol(_repo(repo), name))


@mcp.tool()
def find_callers(fn: str, repo: str | None = None) -> str:
    """Who calls this function? Direct callers only, with the line of each call site."""
    return _dump(store.find_callers(_repo(repo), fn))


@mcp.tool()
def find_callees(fn: str, repo: str | None = None) -> str:
    """What does this function call? Direct callees only, with the line of each call site."""
    return _dump(store.find_callees(_repo(repo), fn))


@mcp.tool()
def impact_of(fn: str, depth: int = 3, repo: str | None = None, min_confidence: float = 0.0) -> str:
    """Walk the call graph upward `depth` levels from `fn` and return everything that could
    break if its behaviour or signature changes. Includes entry points (callers nobody else
    calls, usually public API or tests) and the files touched. Edges carry a confidence:
    1.0 for resolved-by-import/type/self, lower for name-only matches; raise `min_confidence`
    (e.g. 0.5) to drop the guessy ones."""
    return _dump(store.impact_of(_repo(repo), fn, depth=max(1, min(depth, 8)),
                                 min_confidence=min_confidence))


@mcp.tool()
def file_outline(path: str, repo: str | None = None) -> str:
    """Symbols defined in a file, in order. Accepts a path suffix like `requests/sessions.py`."""
    return _dump(store.file_outline(_repo(repo), path))


def main():
    mcp.run()


if __name__ == "__main__":
    main()
