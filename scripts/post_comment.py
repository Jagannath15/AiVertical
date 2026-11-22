"""Aggregate scanner findings, post one PR comment, set jenkins/pr-check status."""
import json
import os
import sys

from scripts.github_api import post_pr_comment, set_commit_status

STATUS_CONTEXT = "jenkins/pr-check"

_FINDING_FILES = {
    "secrets": "secrets.json",
    "semgrep": "semgrep.json",
    "trivy": "trivy.json",
    "linters": "linters.json",
    "tests": "tests.json",
}


def load_findings(findings_dir: str) -> dict:
    result = {}
    for key, fname in _FINDING_FILES.items():
        path = os.path.join(findings_dir, fname)
        if os.path.exists(path):
            with open(path) as f:
                result[key] = json.load(f)
        else:
            result[key] = None
    return result


def determine_status(findings: dict):
    secrets = findings.get("secrets") or {}
    if secrets.get("error"):
        return "failure", f"Secret scan could not be verified: {secrets['error']}"
    if secrets.get("found"):
        count = secrets.get("count", 1)
        return "failure", f"Secret(s) detected ({count} found) — merge blocked"

    semgrep = findings.get("semgrep") or {}
    for err in semgrep.get("errors", []):
        if err.get("type") == "ScanFailed":
            return "failure", f"Code scan error: {err.get('message', 'semgrep failed')}"
    for r in semgrep.get("results", []):
        if (r.get("extra") or {}).get("severity") == "ERROR":
            return "failure", f"Semgrep ERROR finding in {r.get('path','?')}:{r.get('start',{}).get('line','?')}"

    trivy = findings.get("trivy") or {}
    if trivy.get("error"):
        return "failure", f"Dependency scan error: {trivy['error']}"
    for res in trivy.get("Results", []):
        for v in res.get("Vulnerabilities") or []:
            if v.get("Severity") == "CRITICAL":
                return "failure", f"Trivy CRITICAL: {v.get('VulnerabilityID','?')} in {v.get('PkgName','?')}"

    linters = findings.get("linters") or {}
    if linters and not linters.get("passed", True):
        return "failure", f"Linter/tests failed ({linters.get('tool','?')})"

    tests = findings.get("tests") or {}
    if tests and not tests.get("passed", True):
        return "failure", f"Tests failed ({tests.get('tool','?')})"

    return "success", "All checks passed"


def _emoji(passed):
    return "✅" if passed else "🔴"


def _secrets_row(s):
    if s is None:
        return "| Secrets (Gitleaks) | ⚠️ skipped | — |"
    if s.get("found"):
        rules = ", ".join(s.get("rules", []))
        return f"| Secrets (Gitleaks) | 🔴 **{s['count']} found** | {rules} |"
    return "| Secrets (Gitleaks) | ✅ clean | — |"


def _semgrep_row(s):
    if s is None:
        return "| Code (Semgrep) | ⚠️ skipped | — |"
    errors = [r for r in s.get("results", []) if (r.get("extra") or {}).get("severity") == "ERROR"]
    warnings = [r for r in s.get("results", []) if (r.get("extra") or {}).get("severity") in ("WARNING", "INFO")]
    if errors:
        return f"| Code (Semgrep) | 🔴 {len(errors)} ERROR, {len(warnings)} advisory | blocking |"
    if warnings:
        return f"| Code (Semgrep) | ⚠️ {len(warnings)} advisory | non-blocking |"
    return "| Code (Semgrep) | ✅ clean | — |"


def _trivy_row(t):
    if t is None:
        return "| Dependencies (Trivy) | ⚠️ skipped | — |"
    crits, highs = 0, 0
    for res in t.get("Results", []):
        for v in res.get("Vulnerabilities") or []:
            if v.get("Severity") == "CRITICAL":
                crits += 1
            elif v.get("Severity") == "HIGH":
                highs += 1
    if crits:
        return f"| Dependencies (Trivy) | 🔴 {crits} CRITICAL, {highs} HIGH | blocking |"
    if highs:
        return f"| Dependencies (Trivy) | ⚠️ {highs} HIGH | advisory |"
    return "| Dependencies (Trivy) | ✅ clean | — |"


def _linters_row(l):
    if l is None:
        return "| Linters / Tests | ⚠️ skipped | — |"
    icon = "✅" if l.get("passed") else "🔴"
    status = "passed" if l.get("passed") else "**failed**"
    return f"| Linters / Tests | {icon} {status} ({l.get('tool','?')}) | {'—' if l.get('passed') else 'blocking'} |"


def _tests_row(t):
    if t is None:
        return "| Tests | ⚠️ skipped | — |"
    icon = "✅" if t.get("passed") else "🔴"
    status = "passed" if t.get("passed") else "**failed**"
    return f"| Tests | {icon} {status} ({t.get('tool','?')}) | {'—' if t.get('passed') else 'blocking'} |"


def _tests_details(t):
    if not t or t.get("passed") or not t.get("output"):
        return ""
    out = t["output"][:2000]
    return f"\n<details><summary>Test output</summary>\n\n```\n{out}\n```\n\n</details>\n"


def _semgrep_details(s):
    if not s or not s.get("results"):
        return ""
    lines = [f"- `{r.get('path','?')}:{r.get('start',{}).get('line','?')}` [{r.get('check_id','?')}] {(r.get('extra') or {}).get('severity','?')}" for r in s["results"][:20]]
    body = "\n".join(lines)
    return f"\n<details><summary>Semgrep findings ({len(s['results'])})</summary>\n\n{body}\n\n</details>\n"


def _trivy_details(t):
    if not t:
        return ""
    vulns = []
    for res in t.get("Results", []):
        for v in res.get("Vulnerabilities") or []:
            if v.get("Severity") in ("CRITICAL", "HIGH"):
                vulns.append(f"- **{v.get('Severity')}** {v.get('VulnerabilityID','?')} in `{v.get('PkgName','?')}` {v.get('InstalledVersion','?')}")
    if not vulns:
        return ""
    body = "\n".join(vulns[:20])
    return f"\n<details><summary>Trivy HIGH/CRITICAL vulnerabilities ({len(vulns)})</summary>\n\n{body}\n\n</details>\n"


def _linters_details(l):
    if not l or l.get("passed") or not l.get("output"):
        return ""
    out = l["output"][:2000]
    return f"\n<details><summary>Linter output</summary>\n\n```\n{out}\n```\n\n</details>\n"


def build_comment(findings: dict) -> str:
    state, desc = determine_status(findings)
    icon = "✅" if state == "success" else "🔴"

    table = "\n".join([
        "| Check | Result | Notes |",
        "|-------|--------|-------|",
        _secrets_row(findings.get("secrets")),
        _semgrep_row(findings.get("semgrep")),
        _trivy_row(findings.get("trivy")),
        _linters_row(findings.get("linters")),
        _tests_row(findings.get("tests")),
        "| AI Review | ⚠️ Phase 3 (not yet enabled) | advisory |",
    ])

    details = (
        _semgrep_details(findings.get("semgrep"))
        + _trivy_details(findings.get("trivy"))
        + _linters_details(findings.get("linters"))
        + _tests_details(findings.get("tests"))
    )

    return (
        f"## {icon} AI Vertical PR Check — `jenkins/pr-check`\n\n"
        f"**{desc}**\n\n"
        f"{table}\n"
        f"{details}"
        f"\n*Status check: `jenkins/pr-check`*"
    )


def main():
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GH_REPO", "")
    sha = os.environ.get("GIT_COMMIT", "")
    pr_number_str = os.environ.get("PR_NUMBER", "")

    if not pr_number_str:
        print("PR_NUMBER not set, skipping comment/status")
        return

    findings = load_findings("findings")
    state, desc = determine_status(findings)
    comment = build_comment(findings)

    try:
        post_pr_comment(repo, int(pr_number_str), comment, token)
    except Exception as e:
        print(f"WARNING: could not post PR comment: {e}", file=sys.stderr)

    if not sha:
        print("ERROR: GIT_COMMIT not set, cannot set status check", file=sys.stderr)
        sys.exit(1)

    try:
        set_commit_status(repo, sha, state, desc[:140], STATUS_CONTEXT, token)
    except Exception as e:
        print(f"WARNING: could not set commit status: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
