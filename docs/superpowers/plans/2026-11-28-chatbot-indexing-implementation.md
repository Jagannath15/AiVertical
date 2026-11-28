# Chatbot Indexing Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the indexing half of Phase 4's chatbot — clone each configured repo, chunk its source, scan each chunk for secrets before it ever reaches an embedding, embed locally, upsert into a per-repo Qdrant collection, and build a per-repo CodeGraphContext graph DB — all independently testable via a CLI entrypoint, with no FastAPI/OAuth dependency.

**Architecture:** Five small modules (`chatbot_clone.py`, `chatbot_chunk.py`, `chatbot_embed.py`, `chatbot_qdrant.py`, `chatbot_graph.py`) each own one responsibility; `chatbot_indexer.py` orchestrates them and reuses Phase 3's `scripts.ai_review.chunk_contains_secret` for the pre-embedding secret scan rather than duplicating it. Plan B (chatbot service, OAuth, chat UI) imports `reindex_repo` from this plan's `chatbot_indexer.py` and calls it from an HTTP endpoint — not built here.

**Tech Stack:** Python 3.11, `qdrant-client` (official Qdrant Python client), a local embedding library (`sentence-transformers` or equivalent — verify official package before installing, per CLAUDE.md), CodeGraphContext (already verified in Phase 3: MIT, org-owned, github.com/CodeGraphContext/CodeGraphContext), pytest.

**Spec:** `docs/superpowers/specs/2026-11-28-chatbot-design.md`

## Global Constraints

- The chatbot's clone PAT must never appear in subprocess argv or any log/stdout — pass it via environment or a credential helper, never as a literal command-line argument (CLAUDE.md: never hardcode or commit secrets; this extends to never exposing them in process listings either).
- Qdrant: **separate collection per `repo_id`**, not one shared collection with a filter field (design doc's hardening — a missing filter in a shared collection silently leaks cross-repo, a wrong collection name just errors).
- CodeGraphContext: **separate graph DB instance per `repo_id`** (same reasoning).
- Every chunk is scanned for secrets (reusing `scripts.ai_review.chunk_contains_secret`, fail-closed — if the scan itself can't run, treat as containing a secret) before it is embedded or upserted. A chunk that fails this check is dropped, not redacted-and-kept.
- No third-party package gets installed without first confirming it's the official one (author, repo, recent activity) and showing the install command, per CLAUDE.md.
- Local embedding only — no network call for embeddings, ever (design doc: indexing touches the whole repo, CLAUDE.md's "never send a whole repo externally" applies directly).

## Review Focus

1. **A chunk contains a secret.** It must never reach Qdrant or get embedded — dropped before `embed_texts` is ever called on it.
2. **`repo_id` contains characters invalid in a Qdrant collection name** (e.g. `/` in `org/repo`) — `collection_name` must produce a valid, deterministic, collision-free name.
3. **Re-indexing a repo whose local clone directory already exists but is stale/corrupted** (a previous partial clone, or a directory that exists but isn't a git repo at all) — `clone_or_pull` must recover (re-clone), not silently operate on wrong or broken content.
4. **The clone PAT must never appear in a subprocess's argv list** — testable directly: inspect the actual command list passed to `subprocess.run`.
5. **A file that can't be decoded/parsed during chunking** (binary content, non-UTF8) — `chunk_file` must skip it, not crash the whole indexing run (same pattern already established in `scripts/call_graph.py`).

---

## Task 1: Dependency verification + scaffolding

**Files:**
- Modify: `jenkins/Dockerfile` is NOT touched (chatbot is its own service, not part of the Jenkins image)
- Create: `chatbot/Dockerfile`, `chatbot/requirements.txt`
- Modify: `docker-compose.yml` (uncomment the `qdrant` service stub, add a `chatbot` service build stanza — Plan B fills in its actual command/ports; this task only needs the service and volumes to exist so later tasks have somewhere to run)
- Modify: `.env.example` (add `REPOS=org/repo1,org/repo2`, `CHATBOT_CLONE_TOKEN=`, `QDRANT_HOST=` / `QDRANT_PORT=` already exist from Phase 1 — confirm, don't duplicate)

> TDD exception: Dockerfile/compose/env are configuration, consistent with how Phases 1-2's equivalents were handled — verified by a successful `docker compose build`, not unit tests.

- [ ] **Step 1: Confirm official packages before adding them**

For `qdrant-client` and whichever local embedding library is chosen (e.g. `sentence-transformers`): check PyPI project page (author, recent release activity) and the linked source repo (org-owned or well-established individual maintainer, star count plausible for its age — same verification method used for CodeGraphContext/Graphify in Phase 3). Show the exact `pip install` line to the user before it goes in `requirements.txt`.

- [ ] **Step 2: Write `chatbot/requirements.txt` and `chatbot/Dockerfile`**

`requirements.txt`: the two confirmed packages above, pinned to exact versions (same pinning discipline as `jenkins/Dockerfile`'s Gitleaks/Trivy/Semgrep `ARG` pattern). `Dockerfile`: Python 3.11 base image, `pip install -r requirements.txt`, copies `scripts/` (this service imports from the same `scripts.*` modules the PR pipeline uses, e.g. `scripts.ai_review.chunk_contains_secret`).

- [ ] **Step 3: Uncomment and extend `docker-compose.yml`**

Uncomment the existing `qdrant` service stub. Add a `chatbot` service: `build: { context: ., dockerfile: chatbot/Dockerfile }`, a named volume for cloned repos (`chatbot_repos:`) and one for graph DB files (`chatbot_graphs:`), `depends_on: [qdrant]`. No `command`/`ports` yet — Plan B adds those once the FastAPI app exists.

- [ ] **Step 4: Add new env vars to `.env.example`**

`REPOS=org/repo1,org/repo2` (comma-separated, matches the existing single-`GH_REPO` pattern) and `CHATBOT_CLONE_TOKEN=` (fine-grained PAT, read-only, scoped to exactly the repos in `REPOS` — comment explaining this is separate from `GITHUB_TOKEN`/`github-checkout`, least-privilege, per the design doc's credential-scoping hardening).

- [ ] **Step 5: Verify**

Run: `docker compose build chatbot qdrant`
Expected: both images build successfully.

- [ ] **Step 6: Commit**

```bash
git add chatbot/Dockerfile chatbot/requirements.txt docker-compose.yml .env.example
git commit -m "feat: chatbot service scaffolding - Dockerfile, compose, env vars"
```

---

## Task 2: `scripts/chatbot_clone.py` — secure clone/pull

**Files:**
- Create: `scripts/chatbot_clone.py`
- Create: `tests/test_chatbot_clone.py`

**Interfaces:**
- Produces: `clone_or_pull(repo: str, dest_dir: str, token: str) -> None` — `repo` is `"org/repo"` format.

- [ ] **Step 1: Write the failing tests**

```python
import os
import subprocess
from unittest.mock import patch, MagicMock

from scripts.chatbot_clone import clone_or_pull


def test_clone_or_pull_clones_when_dest_does_not_exist(tmp_path):
    dest = tmp_path / "repo"
    with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        clone_or_pull("org/repo", str(dest), "tok")
    args = mock_run.call_args[0][0]
    assert "clone" in args


def test_clone_or_pull_token_never_in_argv(tmp_path):
    dest = tmp_path / "repo"
    with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        clone_or_pull("org/repo", str(dest), "super-secret-token")
    args = mock_run.call_args[0][0]
    assert "super-secret-token" not in args
    assert all("super-secret-token" not in str(a) for a in args)


def test_clone_or_pull_recovers_from_corrupted_existing_dir(tmp_path):
    dest = tmp_path / "repo"
    dest.mkdir()
    (dest / "not_a_git_repo.txt").write_text("junk")
    with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        clone_or_pull("org/repo", str(dest), "tok")
    # Must re-clone (not attempt `git pull` inside a non-git directory)
    args = mock_run.call_args[0][0]
    assert "clone" in args


def test_clone_or_pull_pulls_when_valid_git_repo_exists(tmp_path):
    dest = tmp_path / "repo"
    (dest / ".git").mkdir(parents=True)
    with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        clone_or_pull("org/repo", str(dest), "tok")
    args = mock_run.call_args[0][0]
    assert "pull" in args
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_chatbot_clone.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.chatbot_clone'`

- [ ] **Step 3: Implement `clone_or_pull` in `scripts/chatbot_clone.py`**

Pass the token via the `GIT_ASKPASS`/environment mechanism (not a literal `https://{token}@...` URL, which would appear in argv and in git's own remote-url config) — e.g. set `GIT_ASKPASS` to a short wrapper and pass the token through `subprocess.run(..., env={**os.environ, "GIT_TOKEN": token, ...})`, or an equivalent approach that keeps the token out of the args list the test inspects. If `dest_dir` doesn't exist or exists but has no `.git` subdirectory, clone (removing any existing non-git directory contents first); otherwise `git pull` inside it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_chatbot_clone.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/chatbot_clone.py tests/test_chatbot_clone.py
git commit -m "feat: chatbot_clone.py - clone/pull with token never in argv"
```

---

## Task 3: `scripts/chatbot_chunk.py` — file chunking

**Files:**
- Create: `scripts/chatbot_chunk.py`
- Create: `tests/test_chatbot_chunk.py`

**Interfaces:**
- Produces: `chunk_file(file_path: str, max_lines: int = 100) -> list[dict]` — each item `{"file_path": str, "chunk_text": str, "chunk_index": int}`

- [ ] **Step 1: Write the failing tests**

```python
import os

from scripts.chatbot_chunk import chunk_file


def test_chunk_file_one_chunk_for_small_file(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("line\n" * 10)
    chunks = chunk_file(str(f), max_lines=100)
    assert len(chunks) == 1
    assert chunks[0]["file_path"] == str(f)
    assert chunks[0]["chunk_index"] == 0


def test_chunk_file_splits_large_file(tmp_path):
    f = tmp_path / "big.py"
    f.write_text("line\n" * 250)
    chunks = chunk_file(str(f), max_lines=100)
    assert len(chunks) == 3


def test_chunk_file_skips_non_utf8_file(tmp_path):
    f = tmp_path / "bad.py"
    f.write_bytes(b"\xff\xfe\x00\x01not valid utf8")
    chunks = chunk_file(str(f), max_lines=100)  # must not raise
    assert chunks == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_chatbot_chunk.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.chatbot_chunk'`

- [ ] **Step 3: Implement `chunk_file` in `scripts/chatbot_chunk.py`**

Read with `encoding="utf-8"`; on `UnicodeDecodeError`, return `[]` (skip the file — same pattern as `scripts/call_graph.py`'s per-file skip). Split lines into consecutive groups of `max_lines`, one chunk per group.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_chatbot_chunk.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/chatbot_chunk.py tests/test_chatbot_chunk.py
git commit -m "feat: chatbot_chunk.py - fixed-size file chunking, skips undecodable files"
```

---

## Task 4: `scripts/chatbot_embed.py` — local embeddings

**Files:**
- Create: `scripts/chatbot_embed.py`
- Create: `tests/test_chatbot_embed.py`

**Interfaces:**
- Produces: `embed_texts(texts: list[str], model=None) -> list[list[float]]` — `model` is injectable for tests (anything with an `.encode(texts) -> list[list[float]]` method); production code lazily loads the real local model when `model` is `None`.

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import MagicMock

from scripts.chatbot_embed import embed_texts


def test_embed_texts_uses_injected_model():
    fake_model = MagicMock()
    fake_model.encode.return_value = [[0.1, 0.2], [0.3, 0.4]]
    result = embed_texts(["hello", "world"], model=fake_model)
    assert result == [[0.1, 0.2], [0.3, 0.4]]
    fake_model.encode.assert_called_once_with(["hello", "world"])


def test_embed_texts_empty_list_returns_empty():
    fake_model = MagicMock()
    result = embed_texts([], model=fake_model)
    assert result == []
    fake_model.encode.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_chatbot_embed.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.chatbot_embed'`

- [ ] **Step 3: Implement `embed_texts` in `scripts/chatbot_embed.py`**

If `texts` is empty, return `[]` without touching `model` (keeps tests and real usage from ever loading the (large, slow-to-load) real model for a no-op call). Otherwise, if `model` is `None`, lazily load the real local embedding model (module-level cache so it loads once per process, not per call) and call `.encode(texts)`; return its result as a plain list of lists.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_chatbot_embed.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/chatbot_embed.py tests/test_chatbot_embed.py
git commit -m "feat: chatbot_embed.py - local embeddings, injectable model for tests"
```

---

## Task 5: `scripts/chatbot_qdrant.py` — per-repo collections

**Files:**
- Create: `scripts/chatbot_qdrant.py`
- Create: `tests/test_chatbot_qdrant.py`

**Interfaces:**
- Produces:
  - `collection_name(repo_id: str) -> str`
  - `ensure_collection(client, repo_id: str, vector_size: int) -> None`
  - `clear_collection(client, repo_id: str) -> None` — deletes all points in that repo's collection
  - `upsert_chunks(client, repo_id: str, chunks: list[dict]) -> None` — each chunk: `{"id": str, "vector": list[float], "file_path": str, "chunk_text": str}`

**Why `clear_collection` exists:** re-indexing runs on every merge. A naive incremental upsert never removes a point for a chunk whose source was deleted or shrunk — that content stays queryable in Qdrant indefinitely, including if a file was deleted specifically *because* it had a problem. `reindex_repo` (Task 7) calls `clear_collection` before re-upserting a repo's fresh chunks, trading a slightly more expensive reindex for no stale/leaked content ever persisting past a merge. Acceptable at 1-5 repos, reindexed once per merge, not continuously.

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import MagicMock

from scripts.chatbot_qdrant import collection_name, ensure_collection, clear_collection, upsert_chunks


def test_collection_name_is_deterministic_and_valid():
    name1 = collection_name("org/repo")
    name2 = collection_name("org/repo")
    assert name1 == name2
    assert "/" not in name1


def test_collection_name_differs_for_different_repos():
    assert collection_name("org/repo1") != collection_name("org/repo2")


def test_ensure_collection_creates_if_missing():
    client = MagicMock()
    client.collection_exists.return_value = False
    ensure_collection(client, "org/repo", vector_size=384)
    assert client.create_collection.called


def test_ensure_collection_skips_if_exists():
    client = MagicMock()
    client.collection_exists.return_value = True
    ensure_collection(client, "org/repo", vector_size=384)
    assert not client.create_collection.called


def test_upsert_chunks_targets_repo_specific_collection():
    client = MagicMock()
    chunks = [{"id": "1", "vector": [0.1], "file_path": "a.py", "chunk_text": "x"}]
    upsert_chunks(client, "org/repo", chunks)
    call_kwargs = client.upsert.call_args[1]
    assert call_kwargs["collection_name"] == collection_name("org/repo")


def test_clear_collection_targets_repo_specific_collection():
    client = MagicMock()
    clear_collection(client, "org/repo")
    assert client.delete.called or client.recreate_collection.called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_chatbot_qdrant.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.chatbot_qdrant'`

- [ ] **Step 3: Implement in `scripts/chatbot_qdrant.py`**

`collection_name`: deterministic sanitization, e.g. replace `/` with `__` and prefix with a fixed namespace like `repo__` (avoids any collision with a future non-repo collection). `ensure_collection`/`upsert_chunks`: thin wrappers over the `qdrant-client` API (`client.collection_exists`, `client.create_collection`, `client.upsert`) — exact `qdrant-client` call shapes (e.g. `PointStruct`, vector params object) come from its current docs at implementation time, not guessed here; the tests above pin the *contract* (which collection name gets used, when creation is skipped) that this implementation must satisfy regardless of the exact client API shape.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_chatbot_qdrant.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/chatbot_qdrant.py tests/test_chatbot_qdrant.py
git commit -m "feat: chatbot_qdrant.py - one collection per repo_id, never a shared filtered one"
```

---

## Task 6: `scripts/chatbot_graph.py` — per-repo CodeGraphContext wrapper

**Files:**
- Create: `scripts/chatbot_graph.py`
- Create: `tests/test_chatbot_graph.py`

**Interfaces:**
- Produces: `build_repo_graph(repo_id: str, repo_path: str, graph_dir: str) -> str` — returns the path to that repo's graph DB file/directory.

> Before writing the non-test implementation: confirm CodeGraphContext's current install command and actual library/CLI call shape against its live docs (github.com/CodeGraphContext/CodeGraphContext) — its exact API was explicitly left unverified in the design doc.

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import patch

from scripts.chatbot_graph import build_repo_graph


def test_build_repo_graph_uses_repo_specific_path(tmp_path):
    graph_dir = str(tmp_path)
    with patch("scripts.chatbot_graph._run_codegraphcontext") as mock_run:
        result = build_repo_graph("org/repo", "/some/clone/path", graph_dir)
    assert "org" in result or "repo" in result
    mock_run.assert_called_once()


def test_build_repo_graph_different_repos_get_different_paths(tmp_path):
    graph_dir = str(tmp_path)
    with patch("scripts.chatbot_graph._run_codegraphcontext"):
        path1 = build_repo_graph("org/repo1", "/clone1", graph_dir)
        path2 = build_repo_graph("org/repo2", "/clone2", graph_dir)
    assert path1 != path2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_chatbot_graph.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.chatbot_graph'`

- [ ] **Step 3: Implement `build_repo_graph` and `_run_codegraphcontext` in `scripts/chatbot_graph.py`**

`build_repo_graph` computes a repo-specific path under `graph_dir` (same sanitization approach as `collection_name` in Task 5, for consistency) and delegates the actual graph-building call to `_run_codegraphcontext(repo_path, graph_path)` — a separate function so tests can mock just the CodeGraphContext call without needing the real dependency installed. Implement `_run_codegraphcontext`'s body against CodeGraphContext's verified current API (per the Step 0 note above).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_chatbot_graph.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/chatbot_graph.py tests/test_chatbot_graph.py
git commit -m "feat: chatbot_graph.py - one CodeGraphContext instance per repo_id"
```

---

## Task 7: `scripts/chatbot_indexer.py` — orchestration + CLI

**Files:**
- Create: `scripts/chatbot_indexer.py`
- Create: `tests/test_chatbot_indexer.py`

**Interfaces:**
- Consumes: `clone_or_pull` (Task 2), `chunk_file` (Task 3), `embed_texts` (Task 4), `ensure_collection`/`clear_collection`/`upsert_chunks` (Task 5), `build_repo_graph` (Task 6), `scripts.ai_review.chunk_contains_secret` (Phase 3)
- Produces: `reindex_repo(repo_id: str, token: str, clone_dir: str, graph_dir: str, qdrant_client, embed_model=None) -> dict` — returns `{"files_indexed": int, "chunks_embedded": int, "chunks_skipped_secret": int}`. CLI: `python3 -m scripts.chatbot_indexer --repo-id <org/repo> --token <tok> --clone-dir <dir> --graph-dir <dir>`

- [ ] **Step 1: Write the failing tests**

```python
import os
from unittest.mock import patch, MagicMock

from scripts.chatbot_indexer import reindex_repo


def test_reindex_repo_skips_chunk_with_secret(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    (clone_dir / "a.py").write_text("line\n" * 5)
    graph_dir = str(tmp_path / "graphs")
    qdrant_client = MagicMock()

    with patch("scripts.chatbot_indexer.clone_or_pull"), \
         patch("scripts.chatbot_indexer.build_repo_graph", return_value="graph/path"), \
         patch("scripts.chatbot_indexer.chunk_contains_secret", return_value=True), \
         patch("scripts.chatbot_indexer.embed_texts") as mock_embed:
        result = reindex_repo("org/repo", "tok", str(clone_dir), graph_dir, qdrant_client)

    assert result["chunks_skipped_secret"] == 1
    assert result["chunks_embedded"] == 0
    mock_embed.assert_not_called()


def test_reindex_repo_embeds_clean_chunks(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    (clone_dir / "a.py").write_text("line\n" * 5)
    graph_dir = str(tmp_path / "graphs")
    qdrant_client = MagicMock()

    with patch("scripts.chatbot_indexer.clone_or_pull"), \
         patch("scripts.chatbot_indexer.build_repo_graph", return_value="graph/path"), \
         patch("scripts.chatbot_indexer.chunk_contains_secret", return_value=False), \
         patch("scripts.chatbot_indexer.embed_texts", return_value=[[0.1, 0.2]]):
        result = reindex_repo("org/repo", "tok", str(clone_dir), graph_dir, qdrant_client)

    assert result["files_indexed"] == 1
    assert result["chunks_embedded"] == 1
    assert result["chunks_skipped_secret"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_chatbot_indexer.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.chatbot_indexer'`

- [ ] **Step 3: Implement `reindex_repo` and the CLI in `scripts/chatbot_indexer.py`**

Import `chunk_contains_secret` from `scripts.ai_review` directly (reuse, not reimplementation). Order: `clone_or_pull` → walk the cloned repo's files (same reviewable-extension list as `scripts/ai_review.py`'s `_REVIEWABLE_PATTERNS`, for consistency) → `chunk_file` each → `chunk_contains_secret` filter (drop matches, increment `chunks_skipped_secret`, never call `embed_texts` on them) → `embed_texts` the survivors, assigning each kept chunk a deterministic `id` (e.g. a hash of `file_path` + `chunk_index`, not a random UUID — irrelevant here since the whole collection is cleared first, but keeps ids stable if that ever changes) → `ensure_collection` + **`clear_collection`** (before upserting — Task 5's staleness fix) + `upsert_chunks` → `build_repo_graph`. Use `argparse` for the CLI, calling `reindex_repo` with a real `qdrant_client` constructed from `QDRANT_HOST`/`QDRANT_PORT` env vars.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: all passing (61 existing from Phases 1-3 + new tests from this plan's Tasks 2-7)

- [ ] **Step 5: Commit**

```bash
git add scripts/chatbot_indexer.py tests/test_chatbot_indexer.py
git commit -m "feat: chatbot_indexer.py - orchestrates clone/chunk/secret-scan/embed/upsert/graph"
```
