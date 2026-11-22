import json
import os
import tempfile

from scripts.post_comment import load_findings, determine_status, build_comment


def _write(d, name, data):
    with open(os.path.join(d, name), "w") as f:
        json.dump(data, f)


def test_determine_status_fails_on_secret():
    findings = {
        "secrets": {"found": True, "count": 1, "rules": []},
        "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": []},
        "linters": {"passed": True, "tool": "none", "output": ""},
    }
    state, desc = determine_status(findings)
    assert state == "failure"
    assert "secret" in desc.lower()


def test_determine_status_success_when_clean():
    findings = {
        "secrets": {"found": False},
        "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": []},
        "linters": {"passed": True, "tool": "none", "output": ""},
    }
    state, _ = determine_status(findings)
    assert state == "success"


def test_determine_status_fails_on_semgrep_error():
    findings = {
        "secrets": {"found": False},
        "semgrep": {
            "results": [
                {
                    "extra": {"severity": "ERROR"},
                    "check_id": "x",
                    "path": "f.py",
                    "start": {"line": 1},
                }
            ],
            "errors": [],
        },
        "trivy": {"Results": []},
        "linters": {"passed": True, "tool": "none", "output": ""},
    }
    state, _ = determine_status(findings)
    assert state == "failure"


def test_determine_status_fails_on_trivy_critical():
    vuln = {
        "Severity": "CRITICAL",
        "PkgName": "pkg",
        "InstalledVersion": "1.0",
        "VulnerabilityID": "CVE-X",
    }
    findings = {
        "secrets": {"found": False},
        "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": [{"Vulnerabilities": [vuln]}]},
        "linters": {"passed": True, "tool": "none", "output": ""},
    }
    state, _ = determine_status(findings)
    assert state == "failure"


def test_load_findings_returns_none_for_missing_file():
    with tempfile.TemporaryDirectory() as d:
        result = load_findings(d)
    assert result["semgrep"] is None


def test_build_comment_contains_all_sections():
    findings = {
        "secrets": {"found": False},
        "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": []},
        "linters": {"passed": True, "tool": "none", "output": ""},
    }
    comment = build_comment(findings)
    assert "AI Review" in comment
    assert "Phase 3" in comment
    assert "jenkins/pr-check" in comment or "AI Vertical" in comment
