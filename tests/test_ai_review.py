from scripts.ai_review import is_cloud_endpoint, build_chunks


def test_is_cloud_endpoint_true_for_openai():
    assert is_cloud_endpoint("https://api.openai.com/v1") is True


def test_is_cloud_endpoint_false_for_localhost():
    assert is_cloud_endpoint("http://localhost:11434/v1") is False
    assert is_cloud_endpoint("http://127.0.0.1:11434/v1") is False
    assert is_cloud_endpoint("http://[::1]:11434/v1") is False


def test_build_chunks_one_chunk_for_small_file():
    chunks = build_chunks({"a.py": "line\n" * 10}, max_lines=300)
    assert len(chunks) == 1
    assert chunks[0]["file"] == "a.py"
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["chunk_count"] == 1


def test_build_chunks_splits_oversized_file_by_hunk():
    hunk_a = "@@ -1,5 +1,5 @@\n" + "x\n" * 200
    hunk_b = "@@ -50,5 +50,5 @@\n" + "y\n" * 200
    chunks = build_chunks({"big.py": hunk_a + hunk_b}, max_lines=250)
    assert len(chunks) == 2
    assert all(c["file"] == "big.py" for c in chunks)
    assert chunks[0]["chunk_count"] == 2


def test_build_chunks_truncates_single_hunk_larger_than_cap():
    one_giant_hunk = "@@ -1,500 +1,500 @@\n" + "x\n" * 500
    chunks = build_chunks({"huge.py": one_giant_hunk}, max_lines=100)
    assert len(chunks) == 1
    assert len(chunks[0]["diff"].splitlines()) <= 100
