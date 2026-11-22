"""Regression tests for running scripts the way Jenkins does: `python3 -m scripts.<name>`.

`python3 scripts/post_comment.py` puts scripts/ on sys.path (not the repo root),
so `from scripts.github_api import ...` raises ModuleNotFoundError. Jenkins must
invoke these as modules (`-m scripts.<name>`) from the repo root instead.
"""
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_github_api_module_execution_has_no_import_error():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.github_api"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert "ModuleNotFoundError" not in result.stderr
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_post_comment_module_execution_has_no_import_error():
    env = os.environ.copy()
    env.pop("PR_NUMBER", None)
    result = subprocess.run(
        [sys.executable, "-m", "scripts.post_comment"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    assert "ModuleNotFoundError" not in result.stderr
    assert result.returncode == 0
