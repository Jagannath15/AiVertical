# Phase 3: AI Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an LLM-based AI review of the PR diff (with limited Python caller/dependency context) as a new advisory scanner, integrated into the existing Phase 2 `findings/*.json` + `post_comment.py` + Jenkinsfile pattern.

**Architecture:** `scripts/call_graph.py` builds a best-effort Python caller graph with stdlib `ast` (no third-party dependency — this replaces the originally-considered CodeGraphContext/Graphify). `scripts/ai_review.py` chunks the diff per file (splitting oversized files by hunk), builds a prompt per chunk (diff + caller names for Python functions), calls the configured OpenAI-compatible endpoint, and writes `findings/ai_review.json`. `scripts/post_comment.py` gains a fifth scanner row. The Jenkinsfile gains an `AI Review` stage after `Run Tests`, gated by a new `llm-api-key` Jenkins credential, with a Groovy-level fallback so a missing/misconfigured credential degrades to "skipped" rather than failing the build.

**Tech Stack:** Python 3 (stdlib only — `ast`, `urllib.request`, `subprocess`), pytest, Groovy (Jenkinsfile), OpenAI-compatible HTTP API.

**Spec:** `docs/superpowers/specs/2026-11-22-ai-review-design.md`

## Global Constraints

- `LLM_ALLOW_CLOUD` default false; any non-loopback `LLM_BASE_URL` is treated as cloud and refused unless `LLM_ALLOW_CLOUD=true`. No local LLM exists yet — only `localhost`/`127.0.0.1`/`::1` count as local for now.
- Secret found → this stage never runs (already guaranteed: `error()` in the Scan Secrets stage aborts the pipeline before AI Review).
- LLM down/timeout/malformed response → never fails the build; `findings/ai_review.json` records `skipped` and the comment says so.
- Per-call timeout: 30s. Total AI-review wall-clock budget: 10 minutes; remaining chunks are marked skipped once the budget is spent.
- Blocking only on `severity == "high"` AND `category == "security"`; everything else is advisory.
- No third-party Python dependency (no CodeGraphContext, no Graphify) — `call_graph.py` is stdlib `ast` only, Python files only.
- `LLM_API_KEY` is a secret: new Jenkins credential `llm-api-key` (Secret text), injected via `withCredentials`, same pattern as `github-token`. Never hardcoded.
- A missing/misconfigured `llm-api-key` credential must degrade the `AI Review` stage to "skipped", never fail the pipeline (Jenkins throws on an undefined `credentialsId` before any Python code runs, so this needs a Groovy-level `try/catch`, not a Python-level fix).

## Review Focus

1. **`llm-api-key` credential not yet configured** (first real run, before the user creates it) — the `AI Review` stage must not fail the pipeline; it must record "skipped" and continue to `Post Results`.
2. **`LLM_ALLOW_CLOUD=false` with a cloud `LLM_BASE_URL`** (today's actual configuration — no local LLM exists) — `ai_review.py` must refuse to make any network call and write `{"skipped": true, ...}` immediately.
3. **LLM returns non-JSON or malformed JSON** (a bad response, or a diff whose content coincidentally confuses the model into non-JSON output) — must not crash; that chunk's findings are dropped, not fabricated.
4. **A single diff hunk larger than the chunk size cap, with no further internal boundary to split on** — `build_chunks` must not infinite-loop or crash; it truncates as a last resort and still produces a sendable chunk.
5. **A changed `.py` file that fails to parse** (syntax error, or a generated/non-UTF8 file with a `.py` extension) — `build_graph` must skip that file and continue, not crash the whole call-graph build.

---

## Task 1: `call_graph.py` — stdlib `ast` Python caller graph

**Files:**
- Create: `scripts/call_graph.py`
- Create: `tests/test_call_graph.py`

**Interfaces:**
- Produces:
  - `build_graph(repo_root: str) -> dict` — returns `{"callers": {qualname: [caller_qualname, ...]}, "locations": {qualname: "relpath:lineno"}}`
  - `callers_of(graph: dict, qualname: str) -> list[str]` — sorted list, `[]` if unknown

**Qualname scheme:** dotted path from `repo_root` with `/` → `.`, `.py` stripped, then `.` + nested scope names, e.g. `scripts/post_comment.py`'s `determine_status` function → `scripts.post_comment.determine_status`; a method `run` on class `Foo` in that file → `scripts.post_comment.Foo.run`. `locations` values use forward-slash relative paths, e.g. `"scripts/post_comment.py:19"` (the `def`'s line number).

**Resolution heuristic (a spec decision, not left to the implementer):** for a `Call` node whose `func` is an `ast.Name` or `ast.Attribute`, resolve by **last-component name matching** — add a caller edge from the enclosing function's qualname to *every* known qualname whose final dotted component equals the called name. This is deliberately over-inclusive (two unrelated functions both named `run` both get listed as "called") rather than under-inclusive; it's advisory LLM-prompt context, not a precise analyzer, and the design doc already accepts this trade-off.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_call_graph.py
import os
import tempfile

from scripts.call_graph import build_graph, callers_of


def _write(d, relpath, content):
    path = os.path.join(d, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def test_callers_of_resolves_simple_cross_file_call():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.py", "def helper():\n    return 1\n")
        _write(d, "b.py", "from a import helper\n\ndef main():\n    return helper()\n")
        graph = build_graph(d)
    assert callers_of(graph, "a.helper") == ["b.main"]


def test_callers_of_returns_empty_for_unknown_qualname():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.py", "def f():\n    pass\n")
        graph = build_graph(d)
    assert callers_of(graph, "nonexistent.func") == []


def test_build_graph_records_locations():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.py", "def f():\n    pass\n")
        graph = build_graph(d)
    assert graph["locations"]["a.f"] == "a.py:1"


def test_build_graph_resolves_method_call_by_last_component():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "a.py", "class Foo:\n    def run(self):\n        pass\n")
        _write(d, "b.py", "def main(foo):\n    foo.run()\n")
        graph = build_graph(d)
    assert "b.main" in callers_of(graph, "a.Foo.run")


def test_build_graph_skips_file_with_syntax_error():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "broken.py", "def f(:\n    pass\n")
        _write(d, "a.py", "def f():\n    pass\n")
        graph = build_graph(d)  # must not raise
    assert "a.f" in graph["locations"]
    assert "broken.f" not in graph["locations"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_call_graph.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.call_graph'`

- [ ] **Step 3: Implement `scripts/call_graph.py`**

Walk `repo_root` with `os.walk`, skipping any directory component starting with `.` (e.g. `.git`) or named `__pycache__`. For each `*.py` file, `ast.parse` its contents inside a `try/except SyntaxError` (and `UnicodeDecodeError`, for non-UTF8 files) — on failure, skip that file and continue (Review Focus #5). Track an enclosing-scope stack (module → class → function) while walking the AST to build each `FunctionDef`/`AsyncFunctionDef`'s qualname and record it in `locations`. Walk each function's body for `Call` nodes; resolve the called name via the last-component heuristic above against all qualnames already collected across the whole repo (two-pass: collect all qualnames first, then resolve calls, since a callee defined later in another file must still resolve).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_call_graph.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/call_graph.py tests/test_call_graph.py
git commit -m "feat: call_graph.py - stdlib ast caller graph for Python (no 3rd-party dep)"
```

---

## Task 2: `ai_review.py` — diff chunking (no network)

**Files:**
- Create: `scripts/ai_review.py`
- Create: `tests/test_ai_review.py`

**Interfaces:**
- Consumes: `scripts.call_graph.callers_of` (Task 1), for Task 3's `build_prompt`
- Produces:
  - `is_cloud_endpoint(base_url: str) -> bool`
  - `get_file_diff(file_path: str, merge_base: str) -> str` — `subprocess.run(["git", "diff", f"{merge_base}..HEAD", "--", file_path], ...)`, returns stdout
  - `build_chunks(file_diffs: dict[str, str], max_lines: int = 300) -> list[dict]` — each chunk: `{"file": str, "diff": str, "chunk_index": int, "chunk_count": int}`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ai_review.py
from scripts.ai_review import is_cloud_endpoint, build_chunks


def test_is_cloud_endpoint_true_for_openai():
    assert is_cloud_endpoint("https://api.openai.com/v1") is True


def test_is_cloud_endpoint_false_for_localhost():
    assert is_cloud_endpoint("http://localhost:11434/v1") is False
    assert is_cloud_endpoint("http://127.0.0.1:11434/v1") is False
    assert is_cloud_endpoint("http://[::1]:11434/v1") is False


def test_build_chunks_one_chunk_for_small_file():
    chunks = build_chunks({"a.py": "line\n" * 10}, max_lines=300)
    assert len(chunks) == 1
    assert chunks[0]["file"] == "a.py"
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["chunk_count"] == 1


def test_build_chunks_splits_oversized_file_by_hunk():
    hunk_a = "@@ -1,5 +1,5 @@\n" + "x\n" * 200
    hunk_b = "@@ -50,5 +50,5 @@\n" + "y\n" * 200
    chunks = build_chunks({"big.py": hunk_a + hunk_b}, max_lines=250)
    assert len(chunks) == 2
    assert all(c["file"] == "big.py" for c in chunks)
    assert chunks[0]["chunk_count"] == 2


def test_build_chunks_truncates_single_hunk_larger_than_cap():
    one_giant_hunk = "@@ -1,500 +1,500 @@\n" + "x\n" * 500
    chunks = build_chunks({"huge.py": one_giant_hunk}, max_lines=100)
    assert len(chunks) == 1
    assert len(chunks[0]["diff"].splitlines()) <= 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_ai_review.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.ai_review'`

- [ ] **Step 3: Implement `is_cloud_endpoint` and `build_chunks` in `scripts/ai_review.py`**

`is_cloud_endpoint`: parse with `urllib.parse.urlparse`, compare `.hostname` against `{"localhost", "127.0.0.1", "::1"}`. `build_chunks`: for each file, split its diff text on lines starting with `@@` (hunk headers) to get hunks; if the whole file's diff is within `max_lines`, one chunk; otherwise, greedily pack consecutive hunks into chunks up to `max_lines`, and if a single hunk alone exceeds `max_lines`, truncate it to the first `max_lines` lines rather than looping (Review Focus #4).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_ai_review.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/ai_review.py tests/test_ai_review.py
git commit -m "feat: ai_review.py diff chunking (no network yet)"
```

---

## Task 3: `ai_review.py` — LLM call, prompt, and `main()`

**Files:**
- Modify: `scripts/ai_review.py`
- Modify: `tests/test_ai_review.py`

**Interfaces:**
- Consumes: `scripts.call_graph.build_graph`/`callers_of` (Task 1); `is_cloud_endpoint`/`build_chunks`/`get_file_diff` (Task 2)
- Produces:
  - `build_prompt(chunk: dict, graph: dict | None) -> str`
  - `call_llm(prompt: str, base_url: str, model: str, api_key: str, timeout: int = 30) -> list[dict] | None` — returns parsed findings or `None` on any network/timeout/parse failure
  - `main() -> None` — reads env (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_ALLOW_CLOUD`, `MERGE_BASE`), writes `findings/ai_review.json`

**Finding schema** (what `call_llm` must parse out of the LLM's JSON response, and what `main()` writes under `"findings"`): `{"file": str, "line": int, "severity": "low"|"medium"|"high", "category": "security"|"bug"|"style"|"other", "message": str}`.

**`findings/ai_review.json` schema:** `{"skipped": false, "findings": [...]}` or `{"skipped": true, "reason": str}`.

- [ ] **Step 1: Write the failing tests**

```python
import json
from unittest.mock import patch, MagicMock

from scripts.ai_review import call_llm, main


def _mock_llm_response(content_str):
    body = json.dumps({"choices": [{"message": {"content": content_str}}]}).encode()
    m = MagicMock()
    m.read.return_value = body
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def test_call_llm_parses_valid_json_findings():
    valid = json.dumps([{"file": "a.py", "line": 1, "severity": "low", "category": "style", "message": "nit"}])
    with patch("urllib.request.urlopen", return_value=_mock_llm_response(valid)):
        findings = call_llm("prompt", "https://api.openai.com/v1", "gpt-4o", "key")
    assert findings == [{"file": "a.py", "line": 1, "severity": "low", "category": "style", "message": "nit"}]


def test_call_llm_returns_none_on_malformed_json():
    with patch("urllib.request.urlopen", return_value=_mock_llm_response("not json at all")):
        findings = call_llm("prompt", "https://api.openai.com/v1", "gpt-4o", "key")
    assert findings is None


def test_call_llm_returns_none_on_network_error():
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        findings = call_llm("prompt", "https://api.openai.com/v1", "gpt-4o", "key")
    assert findings is None


def test_main_skips_when_cloud_not_allowed(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_ALLOW_CLOUD", "false")
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.chdir(tmp_path)
    main()
    data = json.loads((tmp_path / "findings" / "ai_review.json").read_text())
    assert data["skipped"] is True
    assert "LLM_ALLOW_CLOUD" in data["reason"]


def test_main_writes_findings_when_cloud_allowed(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_ALLOW_CLOUD", "true")
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setenv("MERGE_BASE", "HEAD")
    monkeypatch.chdir(tmp_path)
    with patch("scripts.ai_review.get_changed_files", return_value=[]):
        main()
    data = json.loads((tmp_path / "findings" / "ai_review.json").read_text())
    assert data["skipped"] is False
    assert data["findings"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_ai_review.py -v`
Expected: `AttributeError`/`ImportError` on `call_llm`/`main` (not yet defined)

- [ ] **Step 3: Implement `build_prompt`, `call_llm`, `get_changed_files`, and `main` in `scripts/ai_review.py`**

`get_changed_files(merge_base: str) -> list[str]`: `git diff --name-only --diff-filter=d {merge_base}..HEAD -- '*.py'` via `subprocess.run`, mirroring `scripts/scan_code.sh`'s own file-list logic (needed so `main` has something to call; Task 2's `test_main_writes_findings_when_cloud_allowed` mocks it directly). `build_prompt`: diff text plus, for Python files, a `"Callers: <qualname> is called by: a, b, c"` line per changed function found via `call_graph.callers_of` — names and `file:line` only, never full caller source (per the design doc's "diff only" constraint). Instruct the LLM to respond with *only* a JSON array matching the finding schema above. `call_llm`: POST to `{base_url}/chat/completions` (OpenAI-compatible) via `urllib.request`, `timeout=timeout`; wrap the whole call in `try/except Exception` (covers `URLError`, `TimeoutError`, `HTTPError`, and `json.JSONDecodeError` parsing the response body) and return `None` on any of them. `main`: if `is_cloud_endpoint(LLM_BASE_URL)` and `LLM_ALLOW_CLOUD` is not `"true"`, write `{"skipped": true, "reason": "cloud LLM not allowed (LLM_ALLOW_CLOUD=false)"}` and return immediately — no network call. Otherwise build chunks for every changed `.py` file, call `call_llm` per chunk (30s each, stop once a 10-minute running total is exceeded — remaining chunks contribute no findings but don't mark the whole run skipped), merge all non-`None` results, and write `{"skipped": false, "findings": [...]}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_ai_review.py -v`
Expected: 10 passed (5 from Task 2 + 5 new — run the whole file)

- [ ] **Step 5: Commit**

```bash
git add scripts/ai_review.py tests/test_ai_review.py
git commit -m "feat: ai_review.py LLM call + main() - cloud gating, timeout budget, never fails build"
```

---

## Task 4: `post_comment.py` — AI review row and blocking rule

**Files:**
- Modify: `scripts/post_comment.py`
- Modify: `tests/test_post_comment.py`

**Interfaces:**
- Consumes: `findings/ai_review.json` schema from Task 3 (`{"skipped": bool, "reason"?: str, "findings"?: [...]}`)

- [ ] **Step 1: Write the failing tests**

```python
def test_determine_status_blocks_on_high_security_ai_finding():
    findings = {
        "secrets": {"found": False}, "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": []}, "linters": {"passed": True, "tool": "none", "output": ""},
        "ai_review": {"skipped": False, "findings": [
            {"file": "a.py", "line": 5, "severity": "high", "category": "security", "message": "SQL injection"}
        ]},
    }
    state, desc = determine_status(findings)
    assert state == "failure"
    assert "a.py" in desc


def test_determine_status_advisory_ai_finding_does_not_block():
    findings = {
        "secrets": {"found": False}, "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": []}, "linters": {"passed": True, "tool": "none", "output": ""},
        "ai_review": {"skipped": False, "findings": [
            {"file": "a.py", "line": 5, "severity": "low", "category": "style", "message": "naming nit"}
        ]},
    }
    state, _ = determine_status(findings)
    assert state == "success"


def test_determine_status_ignores_missing_ai_review_key():
    findings = {
        "secrets": {"found": False}, "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": []}, "linters": {"passed": True, "tool": "none", "output": ""},
        "ai_review": None,
    }
    state, _ = determine_status(findings)
    assert state == "success"


def test_build_comment_shows_skipped_ai_review_reason():
    findings = {
        "secrets": {"found": False}, "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": []}, "linters": {"passed": True, "tool": "none", "output": ""},
        "ai_review": {"skipped": True, "reason": "cloud LLM not allowed (LLM_ALLOW_CLOUD=false)"},
    }
    comment = build_comment(findings)
    assert "LLM_ALLOW_CLOUD" in comment


def test_build_comment_shows_ai_review_finding_details():
    findings = {
        "secrets": {"found": False}, "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": []}, "linters": {"passed": True, "tool": "none", "output": ""},
        "ai_review": {"skipped": False, "findings": [
            {"file": "a.py", "line": 5, "severity": "high", "category": "security", "message": "SQL injection risk"}
        ]},
    }
    comment = build_comment(findings)
    assert "SQL injection risk" in comment
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_post_comment.py -v`
Expected: FAILED — `determine_status`/`build_comment` don't yet know about `ai_review` (old hardcoded "Phase 3 (not yet enabled)" row stays; new assertions fail)

- [ ] **Step 3: Implement in `scripts/post_comment.py`**

Add `"ai_review": "ai_review.json"` to `_FINDING_FILES`. In `determine_status`, after the existing `tests` check, add: iterate `(findings.get("ai_review") or {}).get("findings", [])`, return `"failure", f"AI review: high-confidence security finding in {f['file']}:{f['line']}"` for the first finding with `severity == "high"` and `category == "security"`. Add `_ai_review_row(a)` and `_ai_review_details(a)` following the exact pattern of `_tests_row`/`_tests_details` (lines 116-128 of the current file) — `skipped` shows the reason, no findings shows "✅ clean", findings-without-blocking shows "⚠️ N advisory", a blocking finding shows "🔴 N blocking, M advisory". Replace the hardcoded `"| AI Review | ⚠️ Phase 3 (not yet enabled) | advisory |"` line in `build_comment` with `_ai_review_row(findings.get("ai_review"))`, and add `_ai_review_details(findings.get("ai_review"))` to the `details` concatenation.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: all passing (28 existing + 5 new call_graph + 10 new ai_review + 5 new post_comment = 48)

- [ ] **Step 5: Commit**

```bash
git add scripts/post_comment.py tests/test_post_comment.py
git commit -m "feat: post_comment.py - AI review row, blocks only on high-confidence security findings"
```

---

## Task 5: Jenkinsfile — `AI Review` stage, with graceful credential fallback

**Files:**
- Modify: `Jenkinsfile`
- Modify: `docker-compose.yml`
- Modify: `docs/MANUAL_STEPS.md`
- Modify: `docs/DECISIONS.md`

**Interfaces:**
- Consumes: `scripts.ai_review.main` (Task 3) via `python3 -m scripts.ai_review`; `MERGE_BASE` env (already set by the `Extract Merge Base` stage)

> TDD exception: Jenkinsfile/docker-compose are configuration, consistent with how Phase 2's Jenkinsfile stages were verified (live pipeline run, not unit tests).

- [ ] **Step 1: Add non-secret LLM config to `docker-compose.yml`**

In the `jenkins` service's `environment:` block, add (same pattern as the existing `JENKINS_URL` line):
```yaml
- LLM_BASE_URL=${LLM_BASE_URL:-https://api.openai.com/v1}
- LLM_MODEL=${LLM_MODEL:-gpt-4o}
- LLM_ALLOW_CLOUD=${LLM_ALLOW_CLOUD:-false}
```
Do **not** add `LLM_API_KEY` here — same reasoning as the `GITHUB_TOKEN` omission already documented in the file: a secret in the container environment would be inherited by every `sh` step, including PR code under test.

- [ ] **Step 2: Add the `AI Review` stage to `Jenkinsfile`**

Insert after `Run Tests`, before `Post Results`:
```groovy
stage('AI Review') {
    when { expression { env.PR_NUMBER != null } }
    steps {
        script {
            try {
                withCredentials([string(credentialsId: 'llm-api-key', variable: 'LLM_API_KEY')]) {
                    sh 'python3 -m scripts.ai_review'
                }
            } catch (Exception e) {
                echo "AI review skipped: credential 'llm-api-key' not available (${e.getMessage()})"
                sh '''python3 -c "import json; json.dump({'skipped': True, 'reason': 'llm-api-key credential not configured'}, open('findings/ai_review.json', 'w'))"'''
            }
        }
    }
}
```
The `try/catch` is required (Global Constraints) — `withCredentials` with an undefined `credentialsId` throws before any Python code runs, which would otherwise fail the whole build the first time this runs, before the user has created the credential.

- [ ] **Step 3: Validate the Jenkinsfile against the running Jenkins instance**

Run (same approach as Phase 2's validation): `curl -s -u "admin:$PASS" -X POST -F "jenkinsfile=<Jenkinsfile" http://localhost:8080/pipeline-model-converter/validate`
Expected: `Jenkinsfile successfully validated.`

- [ ] **Step 4: Document the `llm-api-key` credential in `docs/MANUAL_STEPS.md`**

Add a new numbered section (after the existing credential section, before branch protection) following the exact format of the existing `github-token`/`github-checkout` steps: Kind **Secret text**, Secret = the real `LLM_API_KEY` value, ID `llm-api-key` exactly, description `LLM API key for AI code review`. Note explicitly that until this credential is created, the `AI Review` stage will show "skipped" in the PR comment rather than failing the build (per the `try/catch` above) — this credential's absence is safe, not a blocker to merge.

- [ ] **Step 5: Log the Phase 3 decisions in `docs/DECISIONS.md`**

Add an entry (same format as existing entries) covering: building `call_graph.py` ourselves instead of CodeGraphContext/Graphify (with the verification findings from the design doc — CodeGraphContext checked out clean but Alpha status; Graphify checked out as a real org-owned project but with anomalously fast growth and assistant-hook-writing behavior we chose to avoid entirely), and the cloud-only `is_cloud_endpoint` scope (no local LLM exists yet; revisit the hostname check when one does).

- [ ] **Step 6: Commit**

```bash
git add Jenkinsfile docker-compose.yml docs/MANUAL_STEPS.md docs/DECISIONS.md
git commit -m "feat: Jenkinsfile AI Review stage - graceful skip if llm-api-key not configured"
```

- [ ] **Step 7: Live verification (manual, after the user creates the `llm-api-key` credential and pushes)**

Push a test PR; confirm the pipeline's `AI Review` stage runs, and the PR comment shows either real findings or a clean "AI Review ✅ clean" row (not "Phase 3 not yet enabled" — that hardcoded placeholder is gone after Task 4).
