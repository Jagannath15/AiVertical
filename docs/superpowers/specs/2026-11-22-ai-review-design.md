# Phase 3: AI Review Design

## Intent

Add an AI code review to the PR pipeline: the LLM reviews the diff (plus limited caller/dependency context for Python), writes advisory findings, and high-confidence security findings can block the merge. Fits into the existing `findings/*.json` + `post_comment.py` pattern from Phase 2.

## Goals

- One more `findings/ai_review.json` scanner output, aggregated into the same PR comment.
- Respects `LLM_ALLOW_CLOUD` (default false): refuses cloud calls unless explicitly allowed.
- Never runs on code containing a found secret (already structurally guaranteed — Scan Secrets aborts the pipeline via `error()` before this stage).
- LLM down/timeout/misconfigured → pipeline still completes, comment says "AI review skipped" — this is the one scanner that does NOT fail closed (explicit CLAUDE.md carve-out, unlike Gitleaks/Semgrep/Trivy in Phase 2).
- High-confidence security findings can block; everything else is advisory only.
- Large diffs get chunked to fit the LLM's context window.

## Non-goals

- Multi-language call-graph context (JS, Go, etc.) — Python only this phase.
- Replacing Semgrep/Trivy — this is a second opinion, not a security scanner replacement.
- Any new third-party Python dependency for the graph piece — stdlib `ast` only.

## Anti-goals (would make this a failure even if it "works")

- Sending the whole repo, or full source of unrelated files, to a cloud LLM — diff + small symbol-only context, nothing else.
- AI review blocking a merge on a low-confidence or style finding.
- A flaky/slow LLM provider ever failing the whole build.

## Constraints (from CLAUDE.md, carried over verbatim)

- `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_ALLOW_CLOUD` — OpenAI-compatible, config-only switch cloud↔local.
- Secret found → STOP before any LLM call (already enforced by Jenkinsfile stage order).
- AI findings are advisory, **except** high-confidence security findings, which may block.
- LLM down/rate-limited/timeout → scanners still ran, comment says AI review skipped.
- Large diffs reviewed in chunks.

## Approaches considered

**Call-graph context:** evaluated CodeGraphContext (github.com/CodeGraphContext/CodeGraphContext — verified: MIT, org-owned, 15 months old, 4.4k stars, organic growth, Alpha status) and a tool called "Graphify" (github.com/Graphify-Labs/graphify — verified: Apache-2.0, org-owned, real project, but 151.7k stars in 7.5 months is anomalously fast growth, ships hosted Cloud/MCP endpoints, and an installer that writes into AI-assistant config/hooks). Decided against both: a third-party Alpha-status tool or a fast-growing one with assistant-hook-writing behavior both add risk for a private-code pipeline when the alternative — stdlib `ast` for Python-only call graphs — is maybe 150 lines of code with zero new dependencies and zero supply-chain surface. **Chosen: build our own, Python-only.**

**Diff chunking:** per-file with a size cap, vs. token-budget packing across files. Per-file is simpler to reason about and to test; a file that's too big on its own gets split further by hunk boundaries. **Chosen: per-file, with a secondary hunk-level split for oversized files.**

**Blocking logic:** free-text LLM output parsed with regex/heuristics, vs. a strict JSON schema the LLM must emit (`severity`, `category`, `file`, `line`, `message`). Strict JSON is more reliable to gate on and matches the existing native-JSON pattern every other scanner in this repo already uses. **Chosen: strict JSON schema, response parsed with `json.loads`; a response that fails to parse is treated as "AI review skipped" for that chunk (not a crash, not a block).**

## Design

### `scripts/call_graph.py` (new, stdlib only)

- `build_graph(repo_root: str) -> dict` — walks `*.py` files, parses each with `ast`, records every `def`/class method as a node (`"module.qualname"`) and every `Call` node's resolved target as an edge. Best-effort name resolution (no type inference) — this is a heuristic aid for the LLM prompt, not a precise static analyzer.
- `callers_of(graph: dict, qualname: str) -> list[str]` — returns the list of qualnames that call `qualname`, for feeding into the review prompt.
- Builder note: for calls graphify/CodeGraphContext would resolve via type inference (e.g. a call through `self.x.foo()`), ours only resolves direct name matches — this will under-report callers across classes/modules in ambiguous cases. Acceptable for "extra context," not acceptable if anyone later treats this as a precise dependency map.

### `scripts/ai_review.py` (new)

- `is_cloud_endpoint(base_url: str) -> bool` — not `localhost`/`127.0.0.1`/`::1`. No local LLM setup exists yet (confirmed with the user), so this phase classifies everything else as cloud, deliberately erring toward refusing rather than leaking. **Known follow-up:** once a real local model exists (Ollama as a sibling docker-compose service, or on the Windows host reached via `host.docker.internal` from the Jenkins container), this check needs the actual hostname/compose-service-name added — revisit then, don't guess the topology now.
- Per-call timeout: 30s (matches the existing pattern in `scripts/github_api.py`'s `TIMEOUT_SECONDS`). Total AI-review wall-clock budget: 10 minutes — after that, any remaining unreviewed chunks are marked skipped rather than attempted, never blocking the pipeline's 30-minute `options { timeout }`.
- `get_changed_python_functions(diff_files: list[str], merge_base: str) -> list[tuple[str, str]]` — for each changed `.py` file, which top-level functions/methods changed (by diffing line ranges against `ast` node line spans).
- `build_chunks(diff_files: list[str], merge_base: str, max_lines: int = 300) -> list[dict]` — one chunk per file; a file whose diff exceeds `max_lines` splits further by contiguous hunk.
- `build_prompt(chunk: dict, graph: dict | None) -> str` — diff text + (Python files only) a short "Callers: `module.func` is called by: a, b, c" block per changed function, from `call_graph.callers_of`. No full source of caller functions is included — names and `file:line` only, keeping to "diff only" plus minimal symbol context.
- `call_llm(prompt: str, base_url, model, api_key) -> dict | None` — stdlib `urllib.request` (OpenAI-compatible `/chat/completions`), `response_format`-style instruction asking for a strict JSON array of findings `{"file", "line", "severity": "low"|"medium"|"high", "category": "security"|"bug"|"style"|"other", "message"}`. Returns `None` on any network error, timeout, or JSON-parse failure — caller treats `None` as "this chunk skipped," never raises.
- `main()`: if `LLM_ALLOW_CLOUD` is false and the endpoint is a cloud one, write `{"skipped": true, "reason": "cloud LLM not allowed (LLM_ALLOW_CLOUD=false)"}` to `findings/ai_review.json` and exit 0 — no network call attempted. Otherwise chunk the diff, call the LLM per chunk, merge all findings, write `findings/ai_review.json` as `{"skipped": false, "findings": [...]}` (or `{"skipped": true, "reason": "<network/parse error>"}` if every chunk failed).

### `scripts/post_comment.py` changes

- `_FINDING_FILES` gains `"ai_review": "ai_review.json"`.
- `determine_status`: block only if any finding has `severity == "high"` and `category == "security"`. A `skipped: true` payload is never blocking.
- New `_ai_review_row` / `_ai_review_details`, same pattern as existing scanners; row shows "skipped" / "N advisory findings" / "N findings (M blocking)".

### Jenkinsfile

- New stage `AI Review`, same `when { expression { env.PR_NUMBER != null } }` guard, placed after `Run Tests` (order among non-blocking-vs-advisory stages doesn't matter functionally — Scan Secrets already gates everything before it). Calls `python3 -m scripts.ai_review`.
- `withCredentials` for `LLM_API_KEY` if/when that's wired to a Jenkins credential, same pattern as `GITHUB_TOKEN`.

## Test plan

User has a real cloud LLM API key available (`LLM_API_KEY`), so this phase is not fully mocked: plan for a live smoke test against the real provider (same pattern as Phase 2's live GitHub PAT verification), alongside mocked `urllib` tests for the skipped/error/malformed-JSON paths (same pattern as `tests/test_github_api.py`). The real key should be stored as a new Jenkins Secret-text credential (e.g. `llm-api-key`) and injected via `withCredentials`, never hardcoded — same handling as `github-token`.

## What's left to the builder (writing-plans / implementation)

- Exact `ast` traversal details for `call_graph.py` (which node types count as "a function," how qualnames are built for nested classes).
- Exact hunk-splitting algorithm for oversized file diffs.
- Exact LLM prompt wording (the JSON schema instruction, system prompt).
- Test fixtures for: a cloud-endpoint-refused case, a malformed-LLM-JSON case, a network-timeout case, a genuine high-severity-security finding blocking the status, a Python file with a real caller relationship resolved correctly by `call_graph.py`.
