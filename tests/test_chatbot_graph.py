from unittest.mock import patch

from scripts.chatbot_graph import build_repo_graph


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
