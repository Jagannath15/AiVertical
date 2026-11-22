# Manual Steps

Steps you must perform by hand (GitHub account access, secrets, branch protection).
These cannot be scripted because they require human authorization.

---

## Table of Contents

0. [Prerequisites](#0-prerequisites)
1. [Create a GitHub Personal Access Token](#1-create-a-github-personal-access-token)
2. [Store the Token in Jenkins](#2-store-the-token-in-jenkins)
3. [Point Jenkins at Your Repository and Start It](#3-point-jenkins-at-your-repository-and-start-it)
4. [Enable Branch Protection on main](#4-enable-branch-protection-on-main) *(Phase 2 step)*

---

## 0. Prerequisites

Install these before running any `docker compose` command.

### Docker Desktop (Windows, WSL2 backend)

1. Download Docker Desktop for Windows from **https://www.docker.com/products/docker-desktop/**.
2. Run the installer. When prompted, select **"Use WSL 2 instead of Hyper-V"** (recommended).
3. After install, start Docker Desktop from the Start menu. Wait for the whale icon in the system tray to show **"Docker Desktop is running"**.
4. Open a PowerShell or terminal window and verify:
   ```
   docker --version
   docker compose version
   ```
   Both should print version numbers without errors.

### Git

Confirm git is installed: `git --version`. If not, download from **https://git-scm.com/download/win**.

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

## 3. Point Jenkins at Your Repository and Start It

### 3a. Set your repo name

1. Open `jenkins/casc/jenkins.yaml` in your editor.
2. Find every occurrence of `YOUR_ORG/YOUR_REPO` and replace with your actual `org/repo`, e.g. `acme/backend`.
3. Save the file.

### 3b. Create a `.env` file (optional but recommended)

Copy `.env.example` to `.env` and fill in your values:

```
JENKINS_ADMIN_PASSWORD=choose_a_strong_password
GITHUB_TOKEN=ghp_xxxx          # your GitHub PAT from Step 1
GH_REPO=acme/backend
JENKINS_URL=http://localhost:8080
```

> `.env` is gitignored — never commit it.

### 3c. Build and start Jenkins

From the project root (the folder containing `docker-compose.yml`):

```bash
# First run: build the image (5–10 min; downloads Jenkins LTS + tools)
docker compose up -d --build

# Check startup logs (wait until you see "Jenkins is fully up and running")
docker compose logs -f jenkins
```

Press **Ctrl-C** to stop following logs. Jenkins keeps running in the background.

### 3d. Verify Jenkins is running

1. Open **http://localhost:8080** in your browser.
2. Log in with username `admin` and the password you set in `JENKINS_ADMIN_PASSWORD`.
3. You should see the **Jenkins dashboard** — no setup wizard.
4. Under **Dashboard**, the job `ai-vertical-pr-review` should be listed.
5. Click the job → **Configure** → verify the repository URL shows your actual `org/repo`.

### 3e. Apply JCasC changes

If you later edit `jenkins/casc/jenkins.yaml`, apply changes with:

```bash
docker compose restart jenkins
```

---

## 4. Enable Branch Protection on main

> **Phase 2 step.** The status check `jenkins/pr-check` is posted by `post_comment.py` (Phase 2). The Phase 1 stub Jenkinsfile does NOT post a status check, so GitHub will never show it as an option until Phase 2 is deployed.
>
> **Bootstrap order:** Complete this section AFTER Phase 2 is live and at least one pipeline run has posted a `jenkins/pr-check` status.

### Steps

1. Deploy Phase 2 and push a test PR. Wait for the pipeline to run and post the `jenkins/pr-check` status to GitHub.
2. Go to **GitHub → Your Repo → Settings → Branches → Add branch protection rule**.
3. Branch name pattern: `main`.
4. Check **Require a pull request before merging**.
5. Check **Require status checks to pass before merging**.
6. In the search box, type `jenkins` — select **`jenkins/pr-check`** (it appears after at least one Phase 2 pipeline run).
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
