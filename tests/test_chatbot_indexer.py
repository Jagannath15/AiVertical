import os
from unittest.mock import patch, MagicMock

from scripts.chatbot_indexer import reindex_repo


def test_reindex_repo_skips_chunk_with_secret(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    (clone_dir / "a.py").write_text("line\n" * 5)
    graph_dir = str(tmp_path / "graphs")
    qdrant_client = MagicMock()

    with patch("scripts.chatbot_indexer.clone_or_pull"), \
         patch("scripts.chatbot_indexer.build_repo_graph", return_value="graph/path"), \
         patch("scripts.chatbot_indexer.chunk_contains_secret", return_value=True), \
         patch("scripts.chatbot_indexer.embed_texts") as mock_embed:
        result = reindex_repo("org/repo", "tok", str(clone_dir), graph_dir, qdrant_client)

    assert result["chunks_skipped_secret"] == 1
    assert result["chunks_embedded"] == 0
    mock_embed.assert_not_called()


def test_reindex_repo_embeds_clean_chunks(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    (clone_dir / "a.py").write_text("line\n" * 5)
    graph_dir = str(tmp_path / "graphs")
    qdrant_client = MagicMock()

    with patch("scripts.chatbot_indexer.clone_or_pull"), \
         patch("scripts.chatbot_indexer.build_repo_graph", return_value="graph/path"), \
         patch("scripts.chatbot_indexer.chunk_contains_secret", return_value=False), \
         patch("scripts.chatbot_indexer.embed_texts", return_value=[[0.1, 0.2]]):
        result = reindex_repo("org/repo", "tok", str(clone_dir), graph_dir, qdrant_client)

    assert result["files_indexed"] == 1
    assert result["chunks_embedded"] == 1
    assert result["chunks_skipped_secret"] == 0
