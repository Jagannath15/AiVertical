# Architecture Decisions

Running log of choices and trade-offs. Most recent first.

---

## 2026-11-22 — Phase 2 fail-closed policy for scanners

**Decision:** If a blocking scanner (Gitleaks, Semgrep, Trivy) cannot be verified as having actually run — binary missing, crash, bad commit range — the pipeline fails the build rather than reporting "clean". Each script writes an explicit `"error"` field (or `"found": null` for secrets) instead of an empty/clean result, and `post_comment.py`'s `determine_status` treats that as blocking.

**Reason:** An initial implementation had `scan_secrets.sh` treat a missing Gitleaks binary the same as "no secrets found" — fail *open*. Caught in code review before any real PR ran against it. CLAUDE.md's hard rule ("if Gitleaks finds a secret, STOP before any LLM call") only works if "didn't find" and "didn't run" are never confused.

**Trade-off:** A flaky scanner now blocks merges instead of silently passing them. Acceptable for a <10-dev team — a blocked PR gets noticed and re-run; a leaked secret does not.

---

## 2026-11-22 — Semgrep `--config=auto` kept for now, with `--metrics=off`

**Decision:** `scan_code.sh` still uses `semgrep --config=auto`, which fetches rules from semgrep.dev at runtime — in tension with "prefer self-hosted, offline tools." Added `--metrics=off` to stop telemetry, but did not vendor a pinned local ruleset in this phase.

**Reason:** Vendoring Semgrep's registry rulesets (p/ci, p/security-audit, etc.) into the repo/image is real scope — picking which rulesets, pinning versions, keeping them updated — and wasn't in Phase 2's plan. Flagged in code review as Important, not Critical: `scan_code.sh` already fails closed (any Semgrep failure is reported as a blocking error per the fail-closed policy above), so a network outage blocks merges rather than silently skipping the scan.

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
