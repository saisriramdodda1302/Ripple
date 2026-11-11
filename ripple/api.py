import hashlib
import hmac
import subprocess
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from . import cache, db, store
from .agent.graph import ask
from .config import settings
from .indexer.run import changed_files_between, index_repo

app = FastAPI(title="ripple", version="0.1.0")


class AskBody(BaseModel):
    question: str
    repo: str | None = None
    mode: str = "graph"
    use_cache: bool = True


class IndexBody(BaseModel):
    source: str
    name: str | None = None
    embed: bool = True


@app.get("/health")
def health():
    db.fetch_one("SELECT 1")
    return {"ok": True}


@app.get("/repos")
def repos():
    return store.list_repos()


@app.post("/ask")
async def ask_endpoint(body: AskBody):
    if body.use_cache:
        hit = cache.get(body.repo, body.mode, body.question)
        if hit:
            return {**hit, "cached": True}
    result = await ask(body.question, repo=body.repo, mode=body.mode)
    out = {k: result.get(k) for k in ("answer", "citations", "tokens", "timings", "tool_calls")}
    if body.use_cache:
        cache.put(body.repo, body.mode, body.question, out)
    return {**out, "cached": False}


@app.get("/impact/{fn}")
def impact(fn: str, repo: str | None = None, depth: int = 3):
    rid = store.repo_id_for(repo)
    if rid is None:
        raise HTTPException(404, "no such repo")
    return store.impact_of(rid, fn, depth=depth)


@app.post("/index")
def index(body: IndexBody, tasks: BackgroundTasks):
    tasks.add_task(index_repo, body.source, body.name, None, body.embed)
    return {"queued": body.source}


def _verify(secret: str, body: bytes, signature: str | None):
    if not secret:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.split("=", 1)[1])


@app.post("/webhook/push")
async def github_push(request: Request, tasks: BackgroundTasks,
                      x_hub_signature_256: str | None = Header(default=None)):
    raw = await request.body()
    if not _verify(settings.github_webhook_secret, raw, x_hub_signature_256):
        raise HTTPException(401, "bad signature")
    payload = await request.json()
    name = payload.get("repository", {}).get("name")
    clone_url = payload.get("repository", {}).get("clone_url")
    before, after = payload.get("before"), payload.get("after")
    if not (name and clone_url):
        raise HTTPException(400, "not a push payload")

    row = db.fetch_one("SELECT root, commit_sha FROM repos WHERE name = %s", (name,))
    if row is None:
        tasks.add_task(index_repo, clone_url, name)
        return {"queued": "full index"}

    def partial():
        root = Path(row["root"])
        # pulling first so the diff below sees the new commits
        subprocess.run(["git", "-C", str(root), "pull", "--ff-only", "-q"], check=False)
        base = row["commit_sha"] or before
        try:
            changed = changed_files_between(root, base, after) if base else None
        except Exception:
            changed = None
        index_repo(clone_url, name, only_paths=changed)

    tasks.add_task(partial)
    return {"queued": "incremental", "before": before, "after": after}
