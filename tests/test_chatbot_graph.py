from unittest.mock import patch, MagicMock

from scripts.chatbot_graph import build_repo_graph, _run_codegraphcontext


def test_build_repo_graph_uses_repo_specific_path(tmp_path):
    graph_dir = str(tmp_path)
    with patch("scripts.chatbot_graph._run_codegraphcontext") as mock_run:
        result = build_repo_graph("org/repo", "/some/clone/path", graph_dir)
    assert "org" in result or "repo" in result
    mock_run.assert_called_once()


def test_build_repo_graph_different_repos_get_different_paths(tmp_path):
    graph_dir = str(tmp_path)
    with patch("scripts.chatbot_graph._run_codegraphcontext"):
        path1 = build_repo_graph("org/repo1", "/clone1", graph_dir)
        path2 = build_repo_graph("org/repo2", "/clone2", graph_dir)
    assert path1 != path2


def test_run_codegraphcontext_env_does_not_leak_full_os_environ(tmp_path, monkeypatch):
    monkeypatch.setenv("CHATBOT_CLONE_TOKEN", "super-secret")
    graph_path = str(tmp_path / "g")
    with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        _run_codegraphcontext("/repo", graph_path)
    env_arg = mock_run.call_args[1]["env"]
    assert "CHATBOT_CLONE_TOKEN" not in env_arg
    assert env_arg["HOME"] == graph_path
