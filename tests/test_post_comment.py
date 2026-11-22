import json
import os
import tempfile
from unittest.mock import patch

from scripts.post_comment import load_findings, determine_status, build_comment, main


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
    assert "jenkins/pr-check" in comment


def test_determine_status_fails_when_secret_scan_unverified():
    findings = {
        "secrets": {"found": None, "error": "gitleaks scan could not be verified (exit 127)"},
        "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": []},
        "linters": {"passed": True, "tool": "none", "output": ""},
    }
    state, desc = determine_status(findings)
    assert state == "failure"
    assert "could not be verified" in desc


def test_determine_status_fails_when_trivy_errored():
    findings = {
        "secrets": {"found": False},
        "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": [], "error": "trivy scan failed (exit 127)"},
        "linters": {"passed": True, "tool": "none", "output": ""},
    }
    state, desc = determine_status(findings)
    assert state == "failure"
    assert "trivy" in desc.lower() or "dependency" in desc.lower()


def test_determine_status_fails_when_semgrep_scan_failed():
    findings = {
        "secrets": {"found": False},
        "semgrep": {"results": [], "errors": [{"type": "ScanFailed", "message": "semgrep did not produce output"}]},
        "trivy": {"Results": []},
        "linters": {"passed": True, "tool": "none", "output": ""},
    }
    state, desc = determine_status(findings)
    assert state == "failure"


def test_determine_status_fails_when_tests_failed():
    findings = {
        "secrets": {"found": False},
        "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": []},
        "linters": {"passed": True, "tool": "none", "output": ""},
        "tests": {"passed": False, "tool": "pytest", "output": "1 failed"},
    }
    state, desc = determine_status(findings)
    assert state == "failure"
    assert "test" in desc.lower()


def test_determine_status_ignores_missing_tests_key():
    findings = {
        "secrets": {"found": False},
        "semgrep": {"results": [], "errors": []},
        "trivy": {"Results": []},
        "linters": {"passed": True, "tool": "none", "output": ""},
        "tests": None,
    }
    state, _ = determine_status(findings)
    assert state == "success"


def test_build_comment_with_populated_findings_shows_specifics():
    findings = {
        "secrets": {"found": False},
        "semgrep": {
            "results": [
                {"extra": {"severity": "ERROR"}, "check_id": "python.lang.security.audit.foo", "path": "app.py", "start": {"line": 10}}
            ],
            "errors": [],
        },
        "trivy": {
            "Results": [
                {"Vulnerabilities": [{"Severity": "CRITICAL", "VulnerabilityID": "CVE-2024-0001", "PkgName": "libfoo", "InstalledVersion": "1.0"}]}
            ]
        },
        "linters": {"passed": False, "tool": "flake8", "output": "app.py:1: E501 line too long"},
        "tests": {"passed": False, "tool": "pytest", "output": "1 failed, 2 passed"},
    }
    comment = build_comment(findings)
    assert "python.lang.security.audit.foo" in comment
    assert "CVE-2024-0001" in comment
    assert "E501" in comment
    assert "pytest" in comment


def test_main_exits_nonzero_when_commit_sha_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GH_REPO", "org/repo")
    monkeypatch.delenv("GIT_COMMIT", raising=False)
    monkeypatch.setenv("PR_NUMBER", "5")
    monkeypatch.chdir(tmp_path)
    with patch("scripts.post_comment.post_pr_comment") as mock_post:
        try:
            main()
            assert False, "expected SystemExit"
        except SystemExit as e:
            assert e.code != 0
        assert mock_post.called


def test_main_posts_comment_and_sets_status(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GH_REPO", "org/repo")
    monkeypatch.setenv("GIT_COMMIT", "abc123")
    monkeypatch.setenv("PR_NUMBER", "5")
    monkeypatch.chdir(tmp_path)
    with patch("scripts.post_comment.post_pr_comment") as mock_post, \
         patch("scripts.post_comment.set_commit_status") as mock_status:
        main()
    assert mock_post.called
    assert mock_status.called
    assert mock_status.call_args[0][2] == "success"
