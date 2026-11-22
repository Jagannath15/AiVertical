"""AI review of the PR diff - OpenAI-compatible endpoint, gated by LLM_ALLOW_CLOUD.

Never fails the build on its own: a down/misconfigured/refused LLM writes
findings/ai_review.json with skipped=true instead of raising.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from scripts.call_graph import build_graph, callers_of

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TIMEOUT_SECONDS = 30
TOTAL_BUDGET_SECONDS = 600

_FINDING_SYSTEM_PROMPT = (
    "You are a code reviewer. Review the diff below. Respond with ONLY a JSON "
    'array of findings, no other text. Each finding: {"file": str, "line": int, '
    '"severity": "low"|"medium"|"high", "category": "security"|"bug"|"style"|"other", '
    '"message": str}. An empty array [] means no findings.'
)


def is_cloud_endpoint(base_url: str) -> bool:
    hostname = urllib.parse.urlparse(base_url).hostname
    return hostname not in _LOCAL_HOSTS


def _split_hunks(diff_text: str) -> list:
    lines = diff_text.splitlines(keepends=True)
    hunks = []
    current = []
    for line in lines:
        if line.startswith("@@") and current:
            hunks.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        hunks.append("".join(current))
    return hunks


def build_chunks(file_diffs: dict, max_lines: int = 300) -> list:
    chunks = []
    for file_path, diff_text in file_diffs.items():
        total_lines = len(diff_text.splitlines())
        if total_lines <= max_lines:
            chunks.append({"file": file_path, "diff": diff_text, "chunk_index": 0, "chunk_count": 1})
            continue

        hunks = _split_hunks(diff_text)
        packed = []
        current_lines = []
        for hunk in hunks:
            hunk_line_count = len(hunk.splitlines())
            if hunk_line_count > max_lines:
                # Single hunk alone exceeds the cap - truncate as a last resort.
                if current_lines:
                    packed.append("".join(current_lines))
                    current_lines = []
                truncated = "\n".join(hunk.splitlines()[:max_lines])
                packed.append(truncated)
                continue
            candidate = current_lines + hunk.splitlines(keepends=True)
            if len(candidate) > max_lines and current_lines:
                packed.append("".join(current_lines))
                current_lines = list(hunk.splitlines(keepends=True))
            else:
                current_lines = candidate
        if current_lines:
            packed.append("".join(current_lines))

        file_chunks = [
            {"file": file_path, "diff": d, "chunk_index": i, "chunk_count": len(packed)}
            for i, d in enumerate(packed)
        ]
        chunks.extend(file_chunks)
    return chunks


def get_changed_files(merge_base: str) -> list:
    """Changed .py files in merge_base..HEAD, deleted files excluded (mirrors scan_code.sh)."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", f"{merge_base}..HEAD", "--", "*.py"],
        capture_output=True, text=True,
    )
    return [f for f in result.stdout.splitlines() if f]


def get_file_diff(file_path: str, merge_base: str) -> str:
    result = subprocess.run(
        ["git", "diff", f"{merge_base}..HEAD", "--", file_path],
        capture_output=True, text=True,
    )
    return result.stdout


def _changed_function_qualnames(chunk: dict, graph: dict) -> list:
    """Qualnames defined in chunk['file'] whose location appears in graph."""
    if not chunk["file"].endswith(".py"):
        return []
    prefix = chunk["file"] + ":"
    return [q for q, loc in graph.get("locations", {}).items() if loc.startswith(prefix)]


def build_prompt(chunk: dict, graph: dict = None) -> str:
    parts = [_FINDING_SYSTEM_PROMPT, f"\nFile: {chunk['file']}", "```diff", chunk["diff"], "```"]
    if graph is not None:
        for qualname in _changed_function_qualnames(chunk, graph):
            callers = callers_of(graph, qualname)
            if callers:
                parts.append(f"Callers: {qualname} is called by: {', '.join(callers)}")
    return "\n".join(parts)


def call_llm(prompt: str, base_url: str, model: str, api_key: str, timeout: int = TIMEOUT_SECONDS):
    """Returns parsed findings list, or None on any network/timeout/parse failure."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        content = body["choices"][0]["message"]["content"]
        findings = json.loads(content)
        if not isinstance(findings, list):
            return None
        return findings
    except Exception:
        return None


def main():
    base_url = os.environ.get("LLM_BASE_URL", "")
    model = os.environ.get("LLM_MODEL", "")
    api_key = os.environ.get("LLM_API_KEY", "")
    allow_cloud = os.environ.get("LLM_ALLOW_CLOUD", "false").lower() == "true"
    merge_base = os.environ.get("MERGE_BASE", "HEAD^")

    os.makedirs("findings", exist_ok=True)

    if is_cloud_endpoint(base_url) and not allow_cloud:
        with open("findings/ai_review.json", "w") as f:
            json.dump({"skipped": True, "reason": "cloud LLM not allowed (LLM_ALLOW_CLOUD=false)"}, f)
        return

    changed_files = get_changed_files(merge_base)
    file_diffs = {f: get_file_diff(f, merge_base) for f in changed_files}
    chunks = build_chunks(file_diffs)

    graph = None
    if changed_files:
        try:
            graph = build_graph(".")
        except Exception:
            graph = None

    all_findings = []
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    for chunk in chunks:
        if time.monotonic() >= deadline:
            break
        prompt = build_prompt(chunk, graph)
        findings = call_llm(prompt, base_url, model, api_key)
        if findings:
            all_findings.extend(findings)

    with open("findings/ai_review.json", "w") as f:
        json.dump({"skipped": False, "findings": all_findings}, f)


if __name__ == "__main__":
    main()
