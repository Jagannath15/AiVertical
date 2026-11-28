import os
import threading
import uuid
from unittest.mock import patch, MagicMock

import pytest

from scripts.chatbot_indexer import reindex_repo, _chunk_id, _validate_repo_id, _get_repo_lock


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


# --- C1: point IDs must be valid UUIDs ---


def test_chunk_id_is_valid_uuid_string():
    cid = _chunk_id("org/repo", "a.py", 0)
    uuid.UUID(cid)  # must not raise


def test_chunk_id_is_deterministic():
    assert _chunk_id("org/repo", "a.py", 0) == _chunk_id("org/repo", "a.py", 0)


def test_chunk_id_differs_by_path():
    assert _chunk_id("org/repo", "a.py", 0) != _chunk_id("org/repo", "b.py", 0)


# --- C2: the CLI must not accept the token as an argv argument ---


def test_main_has_no_token_cli_argument():
    import scripts.chatbot_indexer as mod
    parser_source = mod.__file__
    with open(parser_source) as f:
        source = f.read()
    assert '"--token"' not in source


# --- I1: stale content must not survive a run that keeps nothing ---


def test_reindex_repo_clears_collection_even_when_nothing_kept(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    (clone_dir / "a.py").write_text("SECRET\n" * 5)
    graph_dir = str(tmp_path / "graphs")
    qdrant_client = MagicMock()

    with patch("scripts.chatbot_indexer.clone_or_pull"), \
         patch("scripts.chatbot_indexer.build_repo_graph", return_value="graph/path"), \
         patch("scripts.chatbot_indexer.chunk_contains_secret", return_value=True), \
         patch("scripts.chatbot_indexer.embed_texts") as mock_embed:
        reindex_repo("org/repo", "tok", str(clone_dir), graph_dir, qdrant_client)

    assert qdrant_client.delete.called
    mock_embed.assert_not_called()


# --- I2: a secret split across a chunk boundary must still be caught ---


def test_reindex_repo_scans_whole_file_not_just_each_chunk(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    (clone_dir / "a.py").write_text("line\n" * 5)
    graph_dir = str(tmp_path / "graphs")
    qdrant_client = MagicMock()

    captured_texts = []

    def fake_contains_secret(text):
        captured_texts.append(text)
        return False

    with patch("scripts.chatbot_indexer.clone_or_pull"), \
         patch("scripts.chatbot_indexer.build_repo_graph", return_value="graph/path"), \
         patch("scripts.chatbot_indexer.chunk_contains_secret", side_effect=fake_contains_secret), \
         patch("scripts.chatbot_indexer.embed_texts", return_value=[[0.1, 0.2]]):
        reindex_repo("org/repo", "tok", str(clone_dir), graph_dir, qdrant_client)

    # The whole file's text must have been checked, not just chunk-sized slices.
    assert any(t.count("line") == 5 for t in captured_texts)


# --- I3: a symlink pointing outside the clone dir must never be indexed ---


def test_reindex_repo_skips_symlink_outside_clone_dir(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("OUTSIDE_SECRET_CONTENT\n" * 5)
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    try:
        os.symlink(str(outside / "secret.py"), str(clone_dir / "link.py"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    (clone_dir / "a.py").write_text("line\n" * 5)
    graph_dir = str(tmp_path / "graphs")
    qdrant_client = MagicMock()

    with patch("scripts.chatbot_indexer.clone_or_pull"), \
         patch("scripts.chatbot_indexer.build_repo_graph", return_value="graph/path"), \
         patch("scripts.chatbot_indexer.chunk_contains_secret", return_value=False), \
         patch("scripts.chatbot_indexer.embed_texts", return_value=[[0.1, 0.2]]):
        result = reindex_repo("org/repo", "tok", str(clone_dir), graph_dir, qdrant_client)

    assert result["files_indexed"] == 1  # only a.py; link.py excluded


# --- I10: concurrent reindexes of the same repo must be serialized ---


def test_get_repo_lock_returns_same_lock_for_same_repo_id():
    lock1 = _get_repo_lock("org/repo")
    lock2 = _get_repo_lock("org/repo")
    assert lock1 is lock2
    assert isinstance(lock1, type(threading.Lock()))


def test_get_repo_lock_returns_different_locks_for_different_repos():
    assert _get_repo_lock("org/repo1") is not _get_repo_lock("org/repo2")


# --- I11: repo_id must be validated - no path traversal, case-normalized ---


def test_validate_repo_id_rejects_path_traversal():
    with pytest.raises(ValueError):
        _validate_repo_id("..")


def test_validate_repo_id_rejects_dotdot_segment():
    with pytest.raises(ValueError):
        _validate_repo_id("org/..")


def test_validate_repo_id_normalizes_case():
    assert _validate_repo_id("Org/Repo") == "org/repo"


def test_validate_repo_id_accepts_normal_repo():
    assert _validate_repo_id("org/repo") == "org/repo"


def test_reindex_repo_rejects_invalid_repo_id(tmp_path):
    with pytest.raises(ValueError):
        reindex_repo("..", "tok", str(tmp_path / "c"), str(tmp_path / "g"), MagicMock())
