CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS repos (
    id          serial PRIMARY KEY,
    name        text UNIQUE NOT NULL,
    url         text,
    root        text NOT NULL,
    commit_sha  text,
    indexed_at  timestamptz
);

CREATE TABLE IF NOT EXISTS files (
    id        serial PRIMARY KEY,
    repo_id   int NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path      text NOT NULL,
    language  text NOT NULL,
    sha       text NOT NULL,
    UNIQUE (repo_id, path)
);

CREATE TABLE IF NOT EXISTS symbols (
    id          serial PRIMARY KEY,
    repo_id     int NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    file_id     int NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name        text NOT NULL,
    qualname    text NOT NULL,
    kind        text NOT NULL,
    parent      text,
    bases       text[] NOT NULL DEFAULT '{}',
    start_line  int NOT NULL,
    end_line    int NOT NULL,
    signature   text,
    docstring   text,
    code        text NOT NULL,
    embedding   vector({DIM})
);
CREATE INDEX IF NOT EXISTS symbols_repo_name ON symbols (repo_id, name);
CREATE INDEX IF NOT EXISTS symbols_repo_qualname ON symbols (repo_id, qualname);
CREATE INDEX IF NOT EXISTS symbols_file ON symbols (file_id);
CREATE INDEX IF NOT EXISTS symbols_embedding ON symbols
    USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS imports (
    id       serial PRIMARY KEY,
    file_id  int NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    module   text NOT NULL,
    name     text,
    alias    text,
    line     int NOT NULL
);
CREATE INDEX IF NOT EXISTS imports_file ON imports (file_id);

-- raw call sites as written in source; resolved into `calls` afterwards
CREATE TABLE IF NOT EXISTS call_refs (
    id         serial PRIMARY KEY,
    caller_id  int NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    name       text NOT NULL,
    expr       text NOT NULL,
    line       int NOT NULL,
    receiver_type text
);
CREATE INDEX IF NOT EXISTS call_refs_caller ON call_refs (caller_id);

CREATE TABLE IF NOT EXISTS calls (
    caller_id    int NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    callee_id    int NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    line         int NOT NULL,
    resolved_by  text NOT NULL,
    confidence   real NOT NULL DEFAULT 1.0,
    PRIMARY KEY (caller_id, callee_id, line)
);
CREATE INDEX IF NOT EXISTS calls_callee ON calls (callee_id);

CREATE TABLE IF NOT EXISTS query_cache (
    key         text PRIMARY KEY,
    repo_id     int REFERENCES repos(id) ON DELETE CASCADE,
    question    text NOT NULL,
    answer      jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
