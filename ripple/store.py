"""Read side: everything the MCP tools and the agent ask of postgres."""

from . import db
from .indexer.embed import embed_query

SYMBOL_COLS = (
    "s.id, s.name, s.qualname, s.kind, s.parent, s.start_line, s.end_line, "
    "s.signature, s.docstring, f.path"
)


def repo_id_for(repo: str | None) -> int | None:
    if repo is None:
        row = db.fetch_one("SELECT id FROM repos ORDER BY indexed_at DESC NULLS LAST LIMIT 1")
    else:
        row = db.fetch_one("SELECT id FROM repos WHERE name = %s", (repo,))
    return row["id"] if row else None


def list_repos():
    return db.fetch_all(
        "SELECT r.name, r.url, r.commit_sha, r.indexed_at, "
        "(SELECT count(*) FROM files WHERE repo_id = r.id) AS files, "
        "(SELECT count(*) FROM symbols WHERE repo_id = r.id) AS symbols, "
        "(SELECT count(*) FROM calls c JOIN symbols s ON s.id = c.caller_id "
        " WHERE s.repo_id = r.id) AS edges "
        "FROM repos r ORDER BY r.name")


def _strip(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "embedding"}


def resolve_symbol_ids(repo_id: int, name: str) -> list[int]:
    """Accepts `Session.send`, `send`, `sessions.Session.send` or `path/to/file.py:send`."""
    if ":" in name:
        path, _, sym = name.rpartition(":")
        rows = db.fetch_all(
            "SELECT s.id FROM symbols s JOIN files f ON f.id = s.file_id "
            "WHERE s.repo_id = %s AND f.path LIKE %s AND (s.qualname = %s OR s.name = %s)",
            (repo_id, f"%{path}", sym, sym))
        return [r["id"] for r in rows]
    rows = db.fetch_all(
        "SELECT id FROM symbols WHERE repo_id = %s AND qualname = %s", (repo_id, name))
    if not rows:
        rows = db.fetch_all(
            "SELECT id FROM symbols WHERE repo_id = %s AND name = %s", (repo_id, name))
    if not rows and "." in name:
        rows = db.fetch_all(
            "SELECT id FROM symbols WHERE repo_id = %s AND qualname LIKE %s",
            (repo_id, f"%.{name}"))
    if not rows and "." in name:
        return resolve_symbol_ids(repo_id, name.rsplit(".", 1)[-1])
    return [r["id"] for r in rows]


def get_symbol(repo_id: int, name: str, with_code: bool = True) -> list[dict]:
    ids = resolve_symbol_ids(repo_id, name)
    if not ids:
        return []
    cols = SYMBOL_COLS + (", s.code" if with_code else "")
    rows = db.fetch_all(
        f"SELECT {cols} FROM symbols s JOIN files f ON f.id = s.file_id "
        "WHERE s.id = ANY(%s) ORDER BY f.path, s.start_line", (ids,))
    return [_strip(r) for r in rows]


def search_code(repo_id: int, query: str, k: int = 8, kinds: tuple | None = None) -> list[dict]:
    kind_filter = "AND s.kind = ANY(%s)" if kinds else ""
    params: list = [repo_id]
    if kinds:
        params.append(list(kinds))
    has_vectors = db.fetch_one(
        "SELECT 1 FROM symbols WHERE repo_id = %s AND embedding IS NOT NULL LIMIT 1", (repo_id,))
    if has_vectors:
        vec = embed_query(query)
        rows = db.fetch_all(
            f"SELECT {SYMBOL_COLS}, 1 - (s.embedding <=> %s::vector) AS score "
            f"FROM symbols s JOIN files f ON f.id = s.file_id "
            f"WHERE s.repo_id = %s AND s.embedding IS NOT NULL {kind_filter} "
            f"ORDER BY s.embedding <=> %s::vector LIMIT %s",
            [vec] + params + [vec, k])
    else:
        #keyword fallback so the tools still work on an index built with --no-embed
        words = [w for w in query.replace(",", " ").split() if len(w) > 2][:6] or [query]
        clauses = " OR ".join(["s.code ILIKE %s"] * len(words))
        rows = db.fetch_all(
            f"SELECT {SYMBOL_COLS}, 0.0 AS score FROM symbols s JOIN files f ON f.id = s.file_id "
            f"WHERE s.repo_id = %s {kind_filter} AND ({clauses}) "
            f"ORDER BY length(s.code) LIMIT %s",
            params + [f"%{w}%" for w in words] + [k])
    return [_strip(r) for r in rows]


def find_callers(repo_id: int, name: str) -> list[dict]:
    ids = resolve_symbol_ids(repo_id, name)
    if not ids:
        return []
    rows = db.fetch_all(
        f"SELECT DISTINCT {SYMBOL_COLS}, c.line AS call_line, c.resolved_by, c.confidence, "
        "t.qualname AS target FROM calls c "
        "JOIN symbols s ON s.id = c.caller_id JOIN files f ON f.id = s.file_id "
        "JOIN symbols t ON t.id = c.callee_id "
        "WHERE c.callee_id = ANY(%s) ORDER BY f.path, c.line", (ids,))
    return [_strip(r) for r in rows]


def find_callees(repo_id: int, name: str) -> list[dict]:
    ids = resolve_symbol_ids(repo_id, name)
    if not ids:
        return []
    rows = db.fetch_all(
        f"SELECT DISTINCT {SYMBOL_COLS}, c.line AS call_line, c.resolved_by, c.confidence, "
        "src.qualname AS source FROM calls c "
        "JOIN symbols s ON s.id = c.callee_id JOIN files f ON f.id = s.file_id "
        "JOIN symbols src ON src.id = c.caller_id "
        "WHERE c.caller_id = ANY(%s) ORDER BY c.line", (ids,))
    return [_strip(r) for r in rows]


def impact_of(repo_id: int, name: str, depth: int = 3, limit: int = 200,
              min_confidence: float = 0.0) -> dict:
    ids = resolve_symbol_ids(repo_id, name)
    if not ids:
        return {"target": name, "found": False, "affected": [], "entry_points": []}
    rows = db.fetch_all(
        """
        WITH RECURSIVE up AS (
            SELECT c.caller_id AS id, 1 AS depth, ARRAY[c.callee_id, c.caller_id] AS path,
                   c.confidence AS conf
            FROM calls c WHERE c.callee_id = ANY(%s) AND c.confidence >= %s
          UNION ALL
            SELECT c.caller_id, up.depth + 1, up.path || c.caller_id,
                   LEAST(up.conf, c.confidence)
            FROM calls c JOIN up ON c.callee_id = up.id
            WHERE up.depth < %s AND c.confidence >= %s AND NOT (c.caller_id = ANY(up.path))
        )
        SELECT s.id, s.name, s.qualname, s.kind, s.start_line, s.end_line, f.path,
               MIN(up.depth) AS depth, COUNT(*) AS paths, MAX(up.conf) AS confidence,
               NOT EXISTS (SELECT 1 FROM calls c2 WHERE c2.callee_id = s.id) AS is_entry
        FROM up JOIN symbols s ON s.id = up.id JOIN files f ON f.id = s.file_id
        GROUP BY s.id, f.path
        ORDER BY depth, confidence DESC, f.path, s.start_line
        LIMIT %s
        """, (ids, min_confidence, depth, min_confidence, limit))
    affected = [dict(r) for r in rows]
    targets = get_symbol(repo_id, name, with_code=False)
    by_depth: dict[int, int] = {}
    for a in affected:
        by_depth[a["depth"]] = by_depth.get(a["depth"], 0) + 1
    return {
        "target": name,
        "found": True,
        "targets": targets,
        "depth": depth,
        "affected": affected,
        "entry_points": [a for a in affected if a["is_entry"]],
        "count_by_depth": by_depth,
        "files_touched": sorted({a["path"] for a in affected}),
    }


def file_outline(repo_id: int, path: str) -> list[dict]:
    rows = db.fetch_all(
        f"SELECT {SYMBOL_COLS} FROM symbols s JOIN files f ON f.id = s.file_id "
        "WHERE s.repo_id = %s AND f.path LIKE %s ORDER BY s.start_line",
        (repo_id, f"%{path}"))
    return [_strip(r) for r in rows]
