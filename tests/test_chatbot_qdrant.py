from unittest.mock import MagicMock

from scripts.chatbot_qdrant import collection_name, ensure_collection, clear_collection, upsert_chunks


def test_collection_name_is_deterministic_and_valid():
    name1 = collection_name("org/repo")
    name2 = collection_name("org/repo")
    assert name1 == name2
    assert "/" not in name1


def test_collection_name_differs_for_different_repos():
    assert collection_name("org/repo1") != collection_name("org/repo2")


def test_ensure_collection_creates_if_missing():
    client = MagicMock()
    client.collection_exists.return_value = False
    ensure_collection(client, "org/repo", vector_size=384)
    assert client.create_collection.called


def test_ensure_collection_skips_if_exists():
    client = MagicMock()
    client.collection_exists.return_value = True
    ensure_collection(client, "org/repo", vector_size=384)
    assert not client.create_collection.called


def test_upsert_chunks_targets_repo_specific_collection():
    client = MagicMock()
    chunks = [{"id": "1", "vector": [0.1], "file_path": "a.py", "chunk_text": "x"}]
    upsert_chunks(client, "org/repo", chunks)
    call_kwargs = client.upsert.call_args[1]
    assert call_kwargs["collection_name"] == collection_name("org/repo")


def test_clear_collection_targets_repo_specific_collection():
    client = MagicMock()
    clear_collection(client, "org/repo")
    assert client.delete.called or client.recreate_collection.called


def test_upsert_chunks_batches_large_lists():
    client = MagicMock()
    chunks = [{"id": str(i), "vector": [0.1], "file_path": "a.py", "chunk_text": "x"} for i in range(600)]
    upsert_chunks(client, "org/repo", chunks, batch_size=256)
    assert client.upsert.call_count == 3


def test_upsert_chunks_includes_repo_id_in_payload():
    client = MagicMock()
    chunks = [{"id": "1", "vector": [0.1], "file_path": "a.py", "chunk_text": "x", "repo_id": "org/repo"}]
    upsert_chunks(client, "org/repo", chunks)
    points = client.upsert.call_args[1]["points"]
    assert points[0].payload["repo_id"] == "org/repo"
