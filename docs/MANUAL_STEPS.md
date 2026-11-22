# Manual Steps

Steps you must perform by hand (GitHub account access, secrets, branch protection).
These cannot be scripted because they require human authorization.

---

## Table of Contents

1. [Create a GitHub Personal Access Token](#1-create-a-github-personal-access-token)
2. [Store the Token in Jenkins](#2-store-the-token-in-jenkins)
3. [Point Jenkins at Your Repository](#3-point-jenkins-at-your-repository)
4. [Enable Branch Protection on main](#4-enable-branch-protection-on-main)

---

## 1. Create a GitHub Personal Access Token

> Required scope: **`repo`** (full repo access — needed for status checks, PR comments, and reading code for SCM polling).

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)**.
2. Click **Generate new token (classic)**.
3. Set a note, e.g. `jenkins-ai-vertical`.
4. Set expiration to your preference (90 days or no expiry for a long-lived CI token).
5. Check the **`repo`** scope (top-level checkbox — this includes all sub-scopes).
6. Click **Generate token**.
7. **Copy the token now** — GitHub will not show it again.

---

## 2. Store the Token in Jenkins

> Jenkins must hold the token as a credential so scripts never hardcode it.

**Option A — via Jenkins UI (quickest for first run):**

1. Open Jenkins at `http://localhost:8080`.
2. Go to **Manage Jenkins → Credentials → System → Global credentials → Add Credentials**.
3. Kind: **Secret text**.
4. Secret: paste your GitHub PAT.
5. ID: `github-token` ← **this exact ID is used in the Jenkinsfile**.
6. Description: `GitHub PAT for PR comments and status checks`.
7. Click **Create**.

**Option B — via JCasC (declarative, reproducible):**

Add the following block to `jenkins/casc/jenkins.yaml` (replace `<YOUR_TOKEN>`):

```yaml
credentials:
  system:
    domainCredentials:
      - credentials:
          - string:
              scope: GLOBAL
              id: "github-token"
              description: "GitHub PAT for PR comments and status checks"
              secret: "${GITHUB_TOKEN}"   # reads from env var at Jenkins startup
```

Then set the `GITHUB_TOKEN` environment variable before running `docker compose up`:

```bash
export GITHUB_TOKEN=ghp_xxxx
docker compose up -d
```

Option B is preferred for reproducibility. Never paste the raw token into `jenkins.yaml` — use the env-var reference.

---

## 3. Point Jenkins at Your Repository

1. Open `jenkins/casc/jenkins.yaml` in your editor.
2. Find every occurrence of `YOUR_ORG/YOUR_REPO` and replace with your actual `org/repo`, e.g. `acme/backend`.
3. Restart Jenkins to apply: `docker compose restart jenkins`.
4. In the Jenkins UI, verify the pipeline job appears and shows the correct repository URL under **Configure**.

---

## 4. Enable Branch Protection on main

> **Bootstrap order warning:** Set branch protection AFTER the first successful pipeline run. If you enable it before Jenkins has ever posted a status check, the required status check (`jenkins/pr-check`) will never appear as an option.

### Steps

1. Push at least one commit to `main` and let the Jenkins pipeline run successfully (even the stub Jenkinsfile from Phase 1 is enough).
2. Go to **GitHub → Your Repo → Settings → Branches → Add branch protection rule**.
3. Branch name pattern: `main`.
4. Check **Require a pull request before merging**.
5. Check **Require status checks to pass before merging**.
6. In the search box, type `jenkins` — select **`jenkins/pr-check`** (it appears after at least one pipeline run).
7. Check **Require branches to be up to date before merging**.
8. Check **Do not allow bypassing the above settings** (optional but recommended).
9. Click **Create**.

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---------|-------------|-----|
| Jenkins UI blank at `:8080` | Container not running | `docker compose ps`, then `docker compose logs jenkins` |
| "Invalid credentials" in Jenkins | PAT wrong or missing scope | Regenerate token with `repo` scope |
| Status check not appearing | Pipeline never ran | Trigger a manual build first |
| `\r` errors in shell scripts | CRLF line endings | Check `.gitattributes` is committed; re-clone or run `git checkout -- scripts/` |
