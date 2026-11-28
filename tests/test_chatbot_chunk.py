import os

from scripts.chatbot_chunk import chunk_file


def test_chunk_file_one_chunk_for_small_file(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("line\n" * 10)
    chunks = chunk_file(str(f), max_lines=100)
    assert len(chunks) == 1
    assert chunks[0]["file_path"] == str(f)
    assert chunks[0]["chunk_index"] == 0


def test_chunk_file_splits_large_file(tmp_path):
    f = tmp_path / "big.py"
    f.write_text("line\n" * 250)
    chunks = chunk_file(str(f), max_lines=100)
    assert len(chunks) == 3


def test_chunk_file_skips_non_utf8_file(tmp_path):
    f = tmp_path / "bad.py"
    f.write_bytes(b"\xff\xfe\x00\x01not valid utf8")
    chunks = chunk_file(str(f), max_lines=100)  # must not raise
    assert chunks == []


def test_chunk_file_skips_broken_symlink(tmp_path):
    target = tmp_path / "missing_target.py"
    link = tmp_path / "link.py"
    try:
        os.symlink(str(target), str(link))
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks not supported on this platform")
    chunks = chunk_file(str(link), max_lines=100)  # must not raise
    assert chunks == []
