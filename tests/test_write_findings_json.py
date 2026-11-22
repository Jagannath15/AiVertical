import json
import os

from scripts.write_findings_json import build_result, main


def test_build_result_truncates_output():
    result = build_result("pytest", True, "x" * 3000)
    assert result["passed"] is True
    assert result["tool"] == "pytest"
    assert len(result["output"]) == 2000


def test_main_writes_json_file(tmp_path):
    log_file = tmp_path / "log.txt"
    log_file.write_text("1 failed, 2 passed")
    out_file = tmp_path / "tests.json"
    rc = main([str(out_file), "pytest", "0", str(log_file)])
    assert rc == 0
    data = json.loads(out_file.read_text())
    assert data == {"passed": False, "tool": "pytest", "output": "1 failed, 2 passed"}
