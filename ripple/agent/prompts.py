PLAN = """You are planning how to answer a question about a codebase using these tools:
search_code (semantic), get_symbol, find_callers, find_callees, impact_of (multi-level callers).

Question: {question}

Reply with JSON only, no prose:
{{
  "intent": "explain" | "impact" | "locate",
  "targets": ["symbol names mentioned or implied, e.g. Session.send, merge_setting"],
  "queries": ["1-3 short semantic search queries describing the code you need to find"],
  "depth": 1-5   // how many caller levels matter; 3 is typical for impact questions
}}

"impact" means the user asks what breaks / what depends on / what is affected if something
changes. "locate" means they only want to know where something is. Otherwise "explain"."""

ANSWER = """You answer questions about a codebase. Use only the context below; do not invent code.
Every claim about code must cite its location as `path:line` or `path:start-end`
(for example `src/requests/sessions.py:752`). Prefer precise lines over whole ranges.

If the question is about impact, structure the answer as: what the function does, direct callers,
transitive callers grouped by depth, and which public entry points or tests would notice.
If something is not in the context, say so instead of guessing.

Question: {question}

{context}"""

EXPLAIN_FALLBACK = "No relevant code was found in the index for this question."
