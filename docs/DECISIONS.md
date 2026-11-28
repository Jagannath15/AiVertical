# Architecture Decisions

Running log of choices and trade-offs. Most recent first.

---

## 2026-11-28 — Chatbot indexing (Plan A): fixes from a live-probed final review

**Decision:** Several fixes to `scripts/chatbot_*.py` before any real indexing run, found by a reviewer who actually built and probed the image rather than reading the diff alone:

- Qdrant point IDs must be UUIDs (Qdrant rejects raw hex digests) — switched to `uuid.uuid5` over `repo_id:rel_path:chunk_index`.
- The clone PAT must never be a CLI argument (`ps`/`/proc/<pid>/cmdline` visibility) — the CLI now reads `CHATBOT_CLONE_TOKEN` from the environment instead of a `--token` flag.
- A secret split across a fixed-size chunk boundary (e.g. a PEM key) was invisible to a per-chunk-only scan — the scan now runs once over each whole file before chunking; the whole file is dropped if flagged.
- A symlinked file inside a cloned repo, pointing outside that repo's own clone directory, could get indexed into that repo's collection — closes a path that bypassed the per-repo isolation this whole design exists for. `_walk_reviewable_files` now skips any symlink and any path whose real location resolves outside `clone_dir`.
- `clear_collection`/`ensure_collection` now always run, even on a reindex that keeps zero chunks (every file deleted, or every chunk flagged as a secret) — previously the prior index stayed queryable forever in that case, defeating the reason `clear_collection` was added in the first place.
- `chatbot/Dockerfile` was missing `git` and `gitleaks` entirely — the pipeline could not have run at all. Added, pinned to the same Gitleaks version as `jenkins/Dockerfile`.
- Qdrant's host port is now bound to `127.0.0.1` only, not all interfaces — it has no API key configured and holds every indexed repo's code; the chatbot reaches it over the compose network regardless.
- `repo_id` is validated (rejects path traversal like `..`, normalizes case) before it's used to build any filesystem path or collection name.
- A per-repo-id lock serializes concurrent reindexes of the same repo (relevant once Plan B's reindex-trigger endpoint exists).

**Reason:** The reviewer built the actual chatbot image and ran a real `qdrant-client` probe rather than trusting the diff — this is how the ID-format bug, the missing binaries, and the restart-loop-shaped Dockerfile gap were caught; mocked unit tests alone did not exercise any of them.

**Known follow-up, not fixed in this pass:** whether CodeGraphContext's embedded backends actually respect the `HOME` env var override for per-repo storage isolation (`scripts/chatbot_graph.py`) is still unverified against the real `cgc` binary — Task 6's tests mock that call entirely. Verify with two real tiny repos before Plan B's structural queries depend on it.

---

## 2026-11-22 — Call-graph context: build it ourselves, not CodeGraphContext or Graphify

**Decision:** `scripts/call_graph.py` is a ~150-line stdlib `ast` module, Python-only, zero third-party dependencies. Both CodeGraphContext and a tool called "Graphify" were evaluated and rejected.

**Reason:** CodeGraphContext (github.com/CodeGraphContext/CodeGraphContext) checked out cleanly on direct verification — MIT, org-owned, 15 months old, 4.4k stars, organic growth — but is explicitly PyPI "Alpha" status. Graphify (github.com/Graphify-Labs/graphify) also checked out as a real org-owned Apache-2.0 project, not the scam it initially looked like (an earlier "1-day-old release" read was a factual error — the repo is 7.5 months old), but has genuinely anomalous growth (151.7k stars in that time), a double-y PyPI package name its own README has to disclaim (`graphifyy`, warning that other `graphify*` packages are impostors), a hosted Cloud/MCP service, and an installer that writes into AI-assistant config/hooks. For a private-code pipeline, the risk/value trade-off of either third-party tool lost to writing a narrower, fully self-hosted one ourselves.

**Trade-off:** our version only resolves calls by last-component name matching (no type inference), is Python-only, and will over-report callers in ambiguous cases (two functions named `run` both show up). Acceptable — it's advisory LLM-prompt context, not a precise dependency map. Non-Python PRs get no graph context this phase.

---

## 2026-11-22 — AI review: secrets scanned per-chunk before sending, "skipped" means skipped

**Decision:** Two fixes from the final review of Phase 3, before any live run with `LLM_ALLOW_CLOUD=true`:

1. `scripts/ai_review.py` now runs Gitleaks against the *exact* diff payload of each chunk (`chunk_contains_secret`, via a temp file + `gitleaks detect --no-git`) before sending it to the LLM, fails closed (treats "couldn't check" as "contains a secret") if Gitleaks itself is unavailable, and skips that chunk if anything is found.
2. `main()` now distinguishes "reviewed, found nothing" from "never actually reviewed" — `findings/ai_review.json` only reports `skipped: false` when at least one chunk was genuinely reviewed; an LLM outage, bad key, or every chunk containing a possible secret now reports `skipped: true`, never a false "✅ clean".

**Reason:** Phase 2's `Scan Secrets` stage only scans *added* lines in `MERGE_BASE..HEAD` via Gitleaks' git-log mode. `git diff` — what `ai_review.py` actually sends to the LLM — also includes **removed lines** and **3 lines of unchanged context** around every change. A PR that removes a hardcoded secret, or edits a line near an existing secret already on `main`, would pass the Scan Secrets gate while still sending that secret to a cloud LLM in the diff context. This was latent (inert while `LLM_ALLOW_CLOUD=false`) but would have gone live the moment cloud calls were enabled. Caught in the final review before that happened.

**Cost if these had shipped as originally written:** secret exposure to a third-party cloud provider, and a pipeline that silently misreports "AI review: ✅ clean" during any LLM outage — the exact failure mode the hard rule ("comment says AI review was skipped") exists to prevent.

---

## 2026-11-22 — AI review cloud/local detection: literal loopback only, for now

**Decision:** `scripts/ai_review.py`'s `is_cloud_endpoint` treats only `localhost`/`127.0.0.1`/`::1` as local; everything else (including a future `host.docker.internal` or docker-compose service like `ollama`) is cloud and refused unless `LLM_ALLOW_CLOUD=true`.

**Reason:** No local LLM setup exists yet. Guessing the real network topology now (sibling compose service name? Windows-host Ollama reached via `host.docker.internal`?) risks either silently breaking local mode later or — worse — misclassifying a real network-reachable endpoint as "local" and sending diff content out unguarded. Erring toward refusing (not leaking) until the actual topology is known.

**Follow-up:** when a local model is actually set up, add its real hostname/compose-service-name to the local-detection list then — don't guess it now.

---

## 2026-11-22 — Two credential IDs for one PAT: `github-checkout` and `github-token`

**Decision:** Split what was one credential ID (`github-token`) into two: `github-checkout` (Username with password, used by the Git SCM checkout in `jenkins/casc/jenkins.yaml`) and `github-token` (Secret text, used by `withCredentials([string(...)])` in the Jenkinsfile for PR comments and status checks). Both hold the same PAT value.

**Reason:** Hit for real on the first live pipeline run: `github-token` was created as Username-with-password (satisfies the git clone), which then broke `withCredentials([string(credentialsId: 'github-token', ...)])` with `Credentials 'github-token' is of type 'Username with password' where 'StringCredentials' was expected`. Jenkins credential *type* is per-ID, and the two plugins (git-client, credentials-binding) require different types for the same underlying secret — one ID can't satisfy both.

**Also fixed in the same incident:** `branches('**')` combined with `lightweight(true)` on the `cpsScm` definition made Jenkins build a literal git refspec `refs/heads/**` to fetch the Jenkinsfile before deciding whether to build — jgit rejects `**` as an actual refspec (`IllegalArgumentException: Invalid refspec`), even though `**` is valid for Jenkins' own branch-name *matching*. Switched to `lightweight(false)` (full checkout), which doesn't go through that lookup.

## 2026-11-22 — Phase 2 fail-closed policy for scanners

**Decision:** If a blocking scanner (Gitleaks, Semgrep, Trivy) cannot be verified as having actually run — binary missing, crash, bad commit range — the pipeline fails the build rather than reporting "clean". Each script writes an explicit `"error"` field (or `"found": null` for secrets) instead of an empty/clean result, and `post_comment.py`'s `determine_status` treats that as blocking.

**Reason:** An initial implementation had `scan_secrets.sh` treat a missing Gitleaks binary the same as "no secrets found" — fail *open*. Caught in code review before any real PR ran against it. CLAUDE.md's hard rule ("if Gitleaks finds a secret, STOP before any LLM call") only works if "didn't find" and "didn't run" are never confused.

**Trade-off:** A flaky scanner now blocks merges instead of silently passing them. Acceptable for a <10-dev team — a blocked PR gets noticed and re-run; a leaked secret does not.

---

## 2026-11-22 — Semgrep uses `p/ci` registry ruleset with `--metrics=off`, not `--config=auto`

**Decision:** `scan_code.sh` runs `semgrep --config=p/ci --metrics=off`. Still fetches rules from the Semgrep registry at runtime — in tension with "prefer self-hosted, offline tools" — but does not use `--config=auto` and does not send telemetry.

**Reason:** Code review flagged `--config=auto` for sending metrics to semgrep.dev. Tried `--config=auto --metrics=off` first; confirmed by running it against this repo in the real Jenkins container that Semgrep refuses that combination outright ("Cannot create auto config when metrics are off"). `--config=auto` does project profiling that requires metrics; a named registry ruleset like `p/ci` does not. Switched to `p/ci`, confirmed working the same way (real run, real container, 21 rules, 0 findings on a clean file).

**Trade-off:** `p/ci` still needs network access to fetch the ruleset — vendoring it locally into the image is a real scope item (picking rulesets, pinning versions, keeping them updated) deferred past Phase 2. `scan_code.sh` fails closed on any Semgrep failure (see the fail-closed policy above), so a network outage blocks merges rather than silently skipping the scan.

**Follow-up:** Vendor a pinned Semgrep ruleset before this goes fully offline-first. Track as a Phase 2.5/3 item.

## 2026-11-22 — JCasC job-dsl script security disabled

**Decision:** `security.globalJobDslSecurityConfiguration.useScriptSecurity: false` in `jenkins/casc/jenkins.yaml`.

**Reason:** JCasC-defined job-dsl scripts must pass the sandbox approval check before they run. For a fresh Jenkins instance with no pre-approved scripts, this causes the pipeline job to fail on first boot. Disabling sandboxing lets the job-dsl `jobs:` block in the YAML run without an admin clicking "Approve Script".

**Risk:** Any Groovy script in the JCasC `jobs:` section — including future additions — runs unsandboxed as the Jenkins admin process. This is acceptable because: (a) only repo maintainers can edit `jenkins/casc/jenkins.yaml`, and (b) the Jenkins instance is not publicly exposed. If Jenkins ever becomes publicly reachable, re-enable sandboxing and approve the job-dsl scripts individually.

---

## 2026-11-22 — Docs generation dropped

**Decision:** Remove the docs generation feature (originally Phase 4).

**Reason:** User determined the feature is not needed for the current team workflow. All AI output goes to PR comments instead.

**Impact:** `docs_generator.py` is not built. `scripts/` only contains the pipeline scripts and `indexer.py`. The merge-to-main pipeline only triggers `indexer.py` for chatbot re-indexing.

---

## 2026-11-22 — Pipeline trigger: SCM polling

**Decision:** Jenkins uses SCM polling (`H/1 * * * *`) instead of GitHub webhooks.

**Reason:** Jenkins is running locally on a developer machine; no public URL is available to receive GitHub webhook POST requests. Alternatives considered:

| Option | Trade-off |
|--------|-----------|
| SCM polling | No external service. ~1 min delay. Zero privacy risk. **Chosen.** |
| smee.io | Real-time trigger. GitHub events pass through smee.io servers — privacy concern for a private-code team. |
| ngrok | Real-time trigger. Events pass through ngrok. Requires account. Same privacy concern. |
| Public VM | Real-time, no third party. Requires infra decision and cost. Can migrate later with config-only change. |

**Migration path:** To switch to webhooks later, add the Jenkins URL to the GitHub repo webhook settings (Settings → Webhooks → Add webhook, `http://<JENKINS_URL>/github-webhook/`) and change the pipeline job trigger from `pollSCM` to `githubPush()`. No script changes needed.

---

## 2026-11-22 — Jenkins configuration: JCasC

**Decision:** Jenkins configuration is committed as code in `jenkins/casc/jenkins.yaml` using the [Configuration as Code plugin](https://plugins.jenkins.io/configuration-as-code/).

**Reason:** Reproducible setup — a fresh `docker compose up` produces an identical Jenkins instance. No manual UI clicks required after the credential step (see `MANUAL_STEPS.md`).

**Trade-off:** JCasC YAML syntax is less discoverable than the UI. Refer to the [JCasC documentation](https://github.com/jenkinsci/configuration-as-code-plugin) when adding new configuration.

---

## 2026-11-22 — LLM backend: cloud now, local later

**Decision:** Start with a cloud OpenAI-compatible API (`LLM_ALLOW_CLOUD=true`). Migrate to Ollama/vLLM later via config-only change.

**Migration path:**
1. Install Ollama locally or on a server: `ollama pull <model>`.
2. Update `.env`: `LLM_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=<model>`, `LLM_API_KEY=ollama`, `LLM_ALLOW_CLOUD=false`.
3. Restart any services that read the `.env`. No code changes.

---

## 2026-11-22 — Implementation language: Python

**Decision:** Python 3.11 for all glue scripts, the AI review script, the indexer, and the chatbot backend.

**Reason:** Python has first-class support for every component: `openai` SDK for the LLM client (OpenAI-compatible), `qdrant-client` for the vector DB, `httpx` for the GitHub API, and FastAPI for the chatbot web layer. Semgrep and Gitleaks both emit JSON that Python reads trivially. One language means one Docker base image, shared utilities, and no context-switching between parts of the system.

**Alternatives considered:**
- **Node.js:** Strong for HTTP/webhooks, weaker LLM/ML ecosystem. Would require a second runtime for indexing.
- **Go:** Fast and statically typed, but sparse LLM/vector DB library support. Would add complexity.

---

## 2026-11-22 — Vector DB: Qdrant

**Decision:** Qdrant for the chatbot's semantic search layer.

**Reason:** Self-hosted via Docker (no external service), production-grade with a well-maintained Python client (`qdrant-client`), supports hybrid search (dense + sparse vectors), and scales well for the target scale (<10 devs, 1–5 repos). Alternatives:

| Option | Reason not chosen |
|--------|------------------|
| Chroma | Simpler API but less mature at scale; persistence story less robust |
| pgvector | Good if already running Postgres; adds a DB dependency for a team that may not have one |
| Qdrant | Best balance of simplicity, self-hosting, and Python support. **Chosen.** |
