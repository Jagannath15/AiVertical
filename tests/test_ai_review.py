import json
from unittest.mock import patch, MagicMock

from scripts.ai_review import (
    is_cloud_endpoint,
    build_chunks,
    build_prompt,
    call_llm,
    chunk_contains_secret,
    get_changed_files,
    main,
)


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


# --- C1: LLM down/timeout for every chunk must report skipped, not clean ---


def test_main_reports_skipped_when_every_chunk_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_ALLOW_CLOUD", "true")
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setenv("MERGE_BASE", "HEAD")
    monkeypatch.chdir(tmp_path)
    with patch("scripts.ai_review.get_changed_files", return_value=["a.py"]), \
         patch("scripts.ai_review.get_file_diff", return_value="@@ -1,1 +1,1 @@\n+x\n"), \
         patch("scripts.ai_review.chunk_contains_secret", return_value=False), \
         patch("scripts.ai_review.call_llm", return_value=None):
        main()
    data = json.loads((tmp_path / "findings" / "ai_review.json").read_text())
    assert data["skipped"] is True
    assert "1" in data["reason"]


def test_main_marks_partial_when_some_chunks_fail(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_ALLOW_CLOUD", "true")
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setenv("MERGE_BASE", "HEAD")
    monkeypatch.chdir(tmp_path)
    responses = [None, [{"file": "a.py", "line": 1, "severity": "low", "category": "style", "message": "ok"}]]
    with patch("scripts.ai_review.get_changed_files", return_value=["a.py", "b.py"]), \
         patch("scripts.ai_review.get_file_diff", return_value="@@ -1,1 +1,1 @@\n+x\n"), \
         patch("scripts.ai_review.chunk_contains_secret", return_value=False), \
         patch("scripts.ai_review.call_llm", side_effect=responses):
        main()
    data = json.loads((tmp_path / "findings" / "ai_review.json").read_text())
    assert data["skipped"] is False
    assert data["partial"] is True
    assert data["chunks_reviewed"] == 1
    assert data["chunks_total"] == 2


# --- C2: secrets in diff context/removed lines must never reach the LLM ---


def test_chunk_contains_secret_true_on_leak():
    result = MagicMock(returncode=1)
    with patch("subprocess.run", return_value=result):
        assert chunk_contains_secret("some diff text") is True


def test_chunk_contains_secret_false_when_clean():
    result = MagicMock(returncode=0)
    with patch("subprocess.run", return_value=result):
        assert chunk_contains_secret("some diff text") is False


def test_chunk_contains_secret_fails_closed_on_subprocess_error():
    with patch("subprocess.run", side_effect=FileNotFoundError("gitleaks not found")):
        assert chunk_contains_secret("some diff text") is True


def test_main_skips_chunk_containing_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_ALLOW_CLOUD", "true")
    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setenv("MERGE_BASE", "HEAD")
    monkeypatch.chdir(tmp_path)
    with patch("scripts.ai_review.get_changed_files", return_value=["a.py"]), \
         patch("scripts.ai_review.get_file_diff", return_value="@@ -1,1 +1,1 @@\n+secret\n"), \
         patch("scripts.ai_review.chunk_contains_secret", return_value=True) as mock_secret, \
         patch("scripts.ai_review.call_llm") as mock_llm:
        main()
    assert mock_llm.called is False
    data = json.loads((tmp_path / "findings" / "ai_review.json").read_text())
    assert data["skipped"] is True
    assert "secret" in data["reason"].lower()


# --- I1: a wrong-shaped (but valid-JSON) LLM response must not crash ---


def test_call_llm_drops_invalid_items_but_keeps_valid():
    raw = json.dumps(["oops", {"file": "a.py", "line": 1, "severity": "LOW", "category": "Style", "message": "nit"}])
    with patch("urllib.request.urlopen", return_value=_mock_llm_response(raw)):
        findings = call_llm("prompt", "https://api.openai.com/v1", "gpt-4o", "key")
    assert findings == [{"file": "a.py", "line": 1, "severity": "low", "category": "style", "message": "nit"}]


def test_call_llm_returns_empty_list_when_all_items_invalid():
    raw = json.dumps(["oops", 123, {"no_file_key": True}])
    with patch("urllib.request.urlopen", return_value=_mock_llm_response(raw)):
        findings = call_llm("prompt", "https://api.openai.com/v1", "gpt-4o", "key")
    assert findings == []


# --- I6: strip markdown code fences before parsing ---


def test_call_llm_strips_markdown_code_fences():
    fenced = "```json\n" + json.dumps([{"file": "a.py", "line": 1, "severity": "low", "category": "style", "message": "nit"}]) + "\n```"
    with patch("urllib.request.urlopen", return_value=_mock_llm_response(fenced)):
        findings = call_llm("prompt", "https://api.openai.com/v1", "gpt-4o", "key")
    assert findings == [{"file": "a.py", "line": 1, "severity": "low", "category": "style", "message": "nit"}]


# --- I2: review all configured file types, not just .py ---


def test_get_changed_files_pathspec_includes_non_python_patterns():
    with patch("subprocess.run", return_value=MagicMock(stdout="")) as mock_run:
        get_changed_files("HEAD^")
    args = mock_run.call_args[0][0]
    assert "*.js" in args
    assert "*.go" in args
    assert "Dockerfile" in args


# --- I3: caller context limited to functions the diff actually touches ---


def test_build_prompt_excludes_caller_context_outside_hunk_range():
    graph = {
        "callers": {"a.touched": ["b.main"], "a.untouched": ["b.other"]},
        "locations": {"a.touched": "a.py:5", "a.untouched": "a.py:50"},
    }
    chunk = {"file": "a.py", "diff": "@@ -1,10 +1,10 @@\n+x\n", "chunk_index": 0, "chunk_count": 1}
    prompt = build_prompt(chunk, graph)
    assert "a.touched" in prompt
    assert "a.untouched" not in prompt


def test_build_prompt_caps_caller_list():
    many_callers = [f"b.caller{i}" for i in range(15)]
    graph = {
        "callers": {"a.f": many_callers},
        "locations": {"a.f": "a.py:5"},
    }
    chunk = {"file": "a.py", "diff": "@@ -1,10 +1,10 @@\n+x\n", "chunk_index": 0, "chunk_count": 1}
    prompt = build_prompt(chunk, graph)
    assert "more" in prompt
