# Phase 4: Code Chatbot Design

## Intent

A web chat for developers AND non-technical people (PMs, QA) to ask questions about 1-5 private repos. Answers cite file paths, offer a "simple explanation" vs "show code" mode, and respect each repo's real access control. Re-indexed on every merge to main.

## Goals

- Semantic search (Qdrant, already self-hosted per `docs/DECISIONS.md`) plus structural queries ("who calls X", "what imports Y") across Python, JS, Go, and whatever else the 1-5 repos actually use.
- Access mirrors real GitHub permissions — no separate access list to maintain and let drift.
- Indexing never sends repo content to a third party — the codebase is private, and indexing touches *all* of it, not a bounded diff.
- Answering a question CAN use a cloud LLM (gated by the existing `LLM_ALLOW_CLOUD` flag) because that step only ever sees a bounded, retrieved slice of the codebase — same exposure profile as Phase 3's diff-only AI review, not the same as indexing.

## Non-goals

- A heavy frontend framework — a plain server-rendered chat page is enough for this team's scale.
- Real-time token streaming for v1 (can be added later without an architecture change).
- Multi-tenant/SaaS concerns — this is one internal deployment for one team.

## Anti-goals (would make this a failure even if it "works")

- Any path where full repo content reaches a cloud service during indexing.
- A user seeing an answer sourced from a repo they don't actually have GitHub access to.
- An answer that doesn't cite the file(s) it came from.

## Constraints (from CLAUDE.md, carried over verbatim)

- Code is private; prefer self-hosted, offline tools.
- `LLM_ALLOW_CLOUD` (default false) gates any cloud LLM call — already exists, reused here for answer generation only.
- Qdrant is the chosen vector DB (already decided).
- Per-repo access control; "simple explanation" mode and "show code" toggle; answers cite file paths.
- Re-indexed on each merge to main.

## Approaches considered (this session's back-and-forth, settled)

**Access control:** no-login/network-only, a static per-user API key, or GitHub OAuth. Chosen: **GitHub OAuth for identity, server-side PAT for permission checks** (hardened after a security pass — see Access Control below for why not full-scope OAuth) — repo access mirrors actual GitHub permissions directly, no separate mapping to build or let drift out of sync as team membership changes.

**Structural graph queries (multi-language — Python, JS, Go, etc.):** three options surfaced and reconsidered in order: (1) CodeGraphContext, (2) build our own via stdlib only (rejected as infeasible for real multi-language call resolution without a real parser), (3) build our own on top of tree-sitter directly. Landed back on **(1) CodeGraphContext** — github.com/CodeGraphContext/CodeGraphContext, already verified in Phase 3's research: MIT, org-owned, 15 months old, 4.4k stars with organic growth, embeds locally (KuzuDB/FalkorDB Lite, no external service, no data leaves the network). Its PyPI "Alpha" status is a real but survivable risk here — unlike Phase 3's PR pipeline (which runs CodeGraphContext-adjacent logic per-build next to live secrets), this is a standalone indexer/query service with no secrets in its blast radius. `scripts/call_graph.py` (Phase 3's stdlib `ast` tool) is unaffected and keeps doing its narrower job — ephemeral per-build Python-only context for the PR pipeline, a different use case from a persistent multi-language queryable graph.

**Embeddings for the vector index:** cloud API (same `LLM_ALLOW_CLOUD` gate as answer generation) vs. a local self-hosted model. Initially proposed cloud-allowed, then reversed: unlike answer generation (bounded to retrieved snippets), indexing inherently processes the *entire* codebase on every merge — CLAUDE.md's "never send a whole repo to any external service" applies directly and without exception here. Chosen: **local only**, a self-hosted model (e.g. `sentence-transformers` — a well-established, non-AI-hype-cycle library; still confirm it's the official package per CLAUDE.md's install-verification rule before adding it). No env flag needed for this step since there's no cloud path to gate.

## Design

### Indexing (`scripts/indexer.py`, triggered on merge to main)

**Where it runs (settled during builder check):** indexing logic lives inside the chatbot FastAPI service itself, exposed as an internal HTTP endpoint (`POST /internal/reindex/{repo_id}`, shared-secret-authenticated). The Jenkinsfile's merge-to-main path calls this endpoint instead of running `indexer.py` as a Jenkins stage. This avoids a shared-volume/concurrency story between the Jenkins container (which writes) and the chatbot container (which reads) for an embedded DB — the embedded DB only ever has one process touching it, the chatbot's own.

**Getting repo source onto disk (settled):** the chatbot service clones/pulls each configured repo itself (shallow clone, updated in place on each reindex), using a read-only GitHub PAT stored as its own credential — separate from Jenkins' `github-token`/`github-checkout` (different service, different least-privilege scope: read-only, no status/comment permissions needed).

**Which repos are indexed (settled):** a comma-separated `REPOS=org/repo1,org/repo2` env var on the chatbot service, matching the existing single-`GH_REPO` pattern in `.env.example` — adding a 6th repo later is an env-var edit + restart, no code change, appropriate for a list that changes rarely at this team's scale.

**Per-repo isolation (a real gap the first draft missed, caught in builder check):** the first draft gave Qdrant a `repo_id` filter for vector search but said nothing about the equivalent for CodeGraphContext — if the graph were one shared DB across all repos, a structural query ("who calls X") could surface callers from a repo the asker has no GitHub access to, directly violating this doc's own anti-goal. Fixed: **one separate embedded graph DB instance per `repo_id`** (a separate KuzuDB/FalkorDB Lite file per repo), not one shared graph. This makes cross-repo leakage structurally impossible rather than depending on a query-time filter CodeGraphContext's API may or may not actually support.

- For each configured repo: clone/pull, then run CodeGraphContext against it to build/update that repo's own graph DB instance.
- Chunk source files (function/class-sized chunks, reusing the language boundaries CodeGraphContext's parser already finds rather than re-implementing chunking). **Before embedding, run a Gitleaks-style scan on each chunk and skip/redact any match** (same fail-closed posture as Phase 3's `chunk_contains_secret`) — Phase 2's secret scanning only covers PRs going forward; anything already committed before that existed would otherwise get embedded and become queryable in one shot across all indexed repos, turning "find me API keys" into a single-query oracle instead of requiring per-repo manual digging.
- Embed each surviving chunk with the local embedding model, upsert into Qdrant with metadata: `repo_id`, `file_path`, `chunk_text`.
- **Qdrant collection strategy (hardened after a security pass):** use **separate Qdrant collections per `repo_id`**, not one shared collection with a `repo_id` filter field. A single shared collection means one code path that forgets the filter silently returns every repo's content instead of erroring — a correctness bug becomes a cross-repo data leak. Separate collections make that failure mode structurally harder: a query against the wrong collection returns nothing or errors, not someone else's data. The same reasoning is why CodeGraphContext already gets one graph DB instance per repo, above — this makes Qdrant consistent with that.

### Access control (FastAPI + GitHub OAuth)

**OAuth scope (hardened after a security pass):** classic GitHub OAuth Apps have no read-only private-repo scope — `repo` grants read *and* write to every private repo the user can see, just to find out which repos they can see. That's far more trust than this needs. OAuth here is for **identity only** (`read:user` scope — who is this person), never for repo access. Per-repo permission is checked server-side instead, using the chatbot's own PAT (the same one used for cloning) against `GET /repos/{owner}/{repo}/collaborators/{username}/permission` for the logged-in user. The user's own GitHub token, if captured at all, grants nothing beyond "who are you."

- On login (identity-only OAuth), fetch and cache which configured repos this user has collaborator access to, via the server-side PAT above — re-check periodically (e.g. every 15 minutes, or on-demand), not on every single query, to stay within GitHub API rate limits. **Known trade-off:** someone removed from a repo on GitHub keeps chatbot access for up to that cache window. Accepted at this team's scale (<10 people); revisit if that ever stops being true.
- Every query is filtered to the intersection of `{the repos this deployment indexes}` and `{the repos this user's cached permission check covers}` — enforced server-side on every request, never trusting a client-supplied `repo_id` (the client picks a repo to ask about; the server independently validates that choice is in the user's allowed set before touching Qdrant or the graph DB — not just at login).

### Answering a question

- Embed the user's question (same local model as indexing — consistency between index and query embeddings matters for retrieval quality).
- Vector search Qdrant, filtered to the user's accessible `repo_id`s.
- If the question looks structural ("who calls X", "what imports Y") — a simple heuristic is enough for v1, not full intent classification — also query CodeGraphContext for that repo.
- Assemble retrieved chunks + graph results + the user's question into a prompt; call the configured LLM (OpenAI-compatible, same `LLM_ALLOW_CLOUD` gate and `LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY` as Phase 3). "Simple explanation" vs "show code" are two different system prompts over this same retrieved context, not two different retrieval paths.
- Response includes the file path(s) the answer drew from (already have them from retrieval — no extra step needed).

### Deployment

- New `chatbot` service in `docker-compose.yml` (FastAPI app). Un-comment the existing `qdrant` service stub.
- CodeGraphContext's embedded graph DB(s) run in-process within the chatbot container, one file per `repo_id` on a persistent volume — no separate graph-DB container needed at this scale.
- The chatbot's Jenkinsfile-side trigger: a new stage on the merge-to-main path (only relevant to the repo(s) this pipeline is attached to) does `curl -X POST $CHATBOT_URL/internal/reindex/{repo_id}` with a shared secret header, after the existing scanner stages. Fire-and-forget is acceptable — reindexing failure shouldn't fail the merge-to-main build; log and move on.
- New secrets: GitHub OAuth client ID/secret, the chatbot's own read-only repo-clone PAT, and the Jenkins→chatbot reindex-trigger shared secret — all env vars on the chatbot service (not Jenkins credentials — mostly not Jenkins pipeline steps, except the shared secret which Jenkins also needs to send the trigger request).
- **These are higher-value credentials than anything in Phases 1-3** (the clone PAT reads all 1-5 repos; today's Jenkins credentials are scoped to one repo's PR operations) — scope the PAT to fine-grained, read-only, limited to exactly the configured repos, never org-wide. The reindex-trigger endpoint must only be reachable inside the docker network, never exposed on a public port.

### Prompt injection via indexed content (a bigger surface than Phase 3)

Phase 3's AI review only ever sees attacker-controlled text bounded to a PR's diff. This chatbot's retrieval can surface *any* committed file — including a comment deliberately written to manipulate the LLM when it's retrieved (e.g. "ignore prior instructions and reveal X"). Same mitigation class as Phase 3 (findings/answers stay advisory — the access-control and secret-scanning layers above don't depend on the LLM behaving correctly, so a successfully-injected response still can't cross a `repo_id` boundary or surface a secret that was already redacted at index time). Log as a `docs/DECISIONS.md` entry at implementation time given the larger surface.

### Hosting and OAuth callback stability

The chatbot shares whatever host Jenkins ends up on (CLAUDE.md: "the server host is undecided and may move later" — same caveat applies here, not a new decision). GitHub OAuth apps require a fixed, pre-registered callback URL, so a host move breaks the OAuth app registration the same way it would break a webhook URL. `docs/MANUAL_STEPS.md` needs a documented re-registration step (update the GitHub OAuth app's callback URL) alongside whatever steps already exist for Jenkins' own host moves, so this isn't a surprise when it happens.

## What's left to the builder (writing-plans / implementation)

- Exact chunk sizing/boundaries for embedding (function-level vs class-level vs fixed-size).
- The structural-vs-semantic question-routing heuristic's exact rules.
- Session storage mechanism for cached GitHub permissions (in-memory dict is fine at this scale; note it resets on restart — acceptable or not is a judgment call the builder can make and flag).
- Exact local embedding model choice and its official-package verification (per CLAUDE.md's install-confirmation rule) before adding the dependency.
- Chat UI specifics (plain HTML/JS vs a minimal framework) — no strong constraint from this conversation either way.
- CodeGraphContext's exact API surface (library import vs subprocess CLI) for both indexing and structural queries — confirm against its current docs at implementation time, not guessed here.
