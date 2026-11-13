import hashlib
import json

from . import db, store


def _key(repo: str | None, mode: str, question: str) -> str:
    raw = f"{repo or ''}|{mode}|{' '.join(question.lower().split())}"
    return hashlib.sha1(raw.encode()).hexdigest()


def get(repo: str | None, mode: str, question: str) -> dict | None:
    row = db.fetch_one("SELECT answer FROM query_cache WHERE key = %s", (_key(repo, mode, question),))
    return row["answer"] if row else None


def put(repo: str | None, mode: str, question: str, result: dict):
    repo_id = store.repo_id_for(repo)
    payload = {k: result.get(k) for k in ("answer", "citations", "tokens", "timings", "tool_calls")}
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO query_cache (key, repo_id, question, answer) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (key) DO UPDATE SET answer = EXCLUDED.answer, created_at = now()",
            (_key(repo, mode, question), repo_id, question, json.dumps(payload, default=str)))
        conn.commit()
