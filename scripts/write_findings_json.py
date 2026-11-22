"""CLI helper: write a {passed, tool, output} findings JSON from a captured log file."""
import json
import sys


def build_result(tool: str, passed: bool, output_text: str) -> dict:
    return {"passed": passed, "tool": tool, "output": output_text[:2000]}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 4:
        print(
            "usage: write_findings_json.py <out_path> <tool> <passed:0|1> <output_file>",
            file=sys.stderr,
        )
        return 2
    out_path, tool, passed_flag, output_file = argv
    with open(output_file, "r", errors="replace") as f:
        output_text = f.read()
    with open(out_path, "w") as f:
        json.dump(build_result(tool, passed_flag == "1", output_text), f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
