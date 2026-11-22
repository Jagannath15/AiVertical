import json
from unittest.mock import patch, MagicMock

from scripts.ai_review import is_cloud_endpoint, build_chunks, call_llm, main


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


def _mock_llm_response(content_str):
    body = json.dumps({"choices": [{"message": {"content": content_str}}]}).encode()
    m = MagicMock()
    m.read.return_value = body
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


def test_call_llm_parses_valid_json_findings():
    valid = json.dumps([{"file": "a.py", "line": 1, "severity": "low", "category": "style", "message": "nit"}])
    with patch("urllib.request.urlopen", return_value=_mock_llm_response(valid)):
        findings = call_llm("prompt", "https://api.openai.com/v1", "gpt-4o", "key")
    assert findings == [{"file": "a.py", "line": 1, "severity": "low", "category": "style", "message": "nit"}]


def test_call_llm_returns_none_on_malformed_json():
    with patch("urllib.request.urlopen", return_value=_mock_llm_response("not json at all")):
        findings = call_llm("prompt", "https://api.openai.com/v1", "gpt-4o", "key")
    assert findings is None


def test_call_llm_returns_none_on_network_error():
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        findings = call_llm("prompt", "https://api.openai.com/v1", "gpt-4o", "key")
    assert findings is None


def test_main_skips_when_cloud_not_allowed(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_ALLOW_CLOUD", "false")
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.chdir(tmp_path)
    main()
    data = json.loads((tmp_path / "findings" / "ai_review.json").read_text())
    assert data["skipped"] is True
    assert "LLM_ALLOW_CLOUD" in data["reason"]


def test_main_writes_findings_when_cloud_allowed(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_ALLOW_CLOUD", "true")
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setenv("MERGE_BASE", "HEAD")
    monkeypatch.chdir(tmp_path)
    with patch("scripts.ai_review.get_changed_files", return_value=[]):
        main()
    data = json.loads((tmp_path / "findings" / "ai_review.json").read_text())
    assert data["skipped"] is False
    assert data["findings"] == []
