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
