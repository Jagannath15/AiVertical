"""AI review of the PR diff - OpenAI-compatible endpoint, gated by LLM_ALLOW_CLOUD.

Never fails the build on its own: a down/misconfigured/refused LLM writes
findings/ai_review.json with skipped=true instead of raising.
"""
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from scripts.call_graph import build_graph, callers_of

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TIMEOUT_SECONDS = 30
TOTAL_BUDGET_SECONDS = 600
MAX_CALLERS_SHOWN = 10

_REVIEWABLE_PATTERNS = [
    "*.py", "*.js", "*.ts", "*.go", "*.java", "*.rb", "*.c", "*.cpp", "*.cs",
    "*.php", "*.sh", "Dockerfile", "Jenkinsfile",
]

_VALID_SEVERITIES = {"low", "medium", "high"}
_VALID_CATEGORIES = {"security", "bug", "style", "other"}

_FINDING_SYSTEM_PROMPT = (
    "You are a code reviewer. Review the diff below. Respond with ONLY a JSON "
    'array of findings, no other text, no markdown code fences. Each finding: '
    '{"file": str, "line": int, "severity": "low"|"medium"|"high", '
    '"category": "security"|"bug"|"style"|"other", "message": str}. '
    "An empty array [] means no findings."
)

_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


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
    """Changed reviewable files in merge_base..HEAD, deleted files excluded (mirrors scan_code.sh)."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", f"{merge_base}..HEAD", "--"] + _REVIEWABLE_PATTERNS,
        capture_output=True, text=True,
    )
    return [f for f in result.stdout.splitlines() if f]


def get_file_diff(file_path: str, merge_base: str) -> str:
    result = subprocess.run(
        ["git", "diff", f"{merge_base}..HEAD", "--", file_path],
        capture_output=True, text=True,
    )
    return result.stdout


def _hunk_new_line_ranges(diff_text: str) -> list:
    """[(start, end_exclusive), ...] for each hunk's new-side line range."""
    ranges = []
    for line in diff_text.splitlines():
        m = _HUNK_HEADER_RE.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) else 1
            ranges.append((start, start + count))
    return ranges


def _changed_function_qualnames(chunk: dict, graph: dict) -> list:
    """Qualnames defined in chunk['file'] whose def line falls inside this
    chunk's hunk ranges - not every function in the file (that would blow
    up the prompt and send far more repo-wide symbol names than intended)."""
    if not chunk["file"].endswith(".py"):
        return []
    prefix = chunk["file"] + ":"
    ranges = _hunk_new_line_ranges(chunk["diff"])
    result = []
    for qualname, loc in graph.get("locations", {}).items():
        if not loc.startswith(prefix):
            continue
        lineno = int(loc.rsplit(":", 1)[1])
        if not ranges or any(start <= lineno < end for start, end in ranges):
            result.append(qualname)
    return result


def build_prompt(chunk: dict, graph: dict = None) -> str:
    parts = [_FINDING_SYSTEM_PROMPT, f"\nFile: {chunk['file']}", "```diff", chunk["diff"], "```"]
    if graph is not None:
        for qualname in _changed_function_qualnames(chunk, graph):
            callers = callers_of(graph, qualname)
            if callers:
                shown = callers[:MAX_CALLERS_SHOWN]
                suffix = f", ... +{len(callers) - MAX_CALLERS_SHOWN} more" if len(callers) > MAX_CALLERS_SHOWN else ""
                parts.append(f"Callers: {qualname} is called by: {', '.join(shown)}{suffix}")
    return "\n".join(parts)


def chunk_contains_secret(diff_text: str) -> bool:
    """Scans the exact payload we're about to send to the LLM - not just
    what Gitleaks' git-log-based Scan Secrets stage already checked, which
    only covers added lines in merge_base..HEAD, not removed lines or the
    surrounding diff context lines `git diff` also includes. Fails CLOSED:
    if the check itself can't run (gitleaks missing/erroring), treat as
    containing a secret rather than assume clean."""
    tmp_path = None
    report_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tf:
            tf.write(diff_text)
            tmp_path = tf.name
        report_path = tmp_path + ".report.json"
        result = subprocess.run(
            [
                "gitleaks", "detect", "--no-git", "--source", tmp_path,
                "--report-format", "json", "--report-path", report_path,
                "--redact", "--no-banner",
            ],
            capture_output=True, text=True,
        )
        return result.returncode != 0
    except Exception:
        return True
    finally:
        for p in (tmp_path, report_path):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass


def _extract_json_array(text: str) -> str:
    """Strip markdown code fences and surrounding prose real providers often
    add even when told not to - without this, every fenced response gets
    dropped as a parse failure and misreported as 'nothing found'."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:] if lines else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return text


def _normalize_finding(item):
    """Keep only well-shaped dicts; drop anything else rather than crash
    post_comment.py downstream (a hallucinated or injected non-dict entry
    must not take down the whole build)."""
    if not isinstance(item, dict):
        return None
    file_ = item.get("file")
    if not isinstance(file_, str) or not file_:
        return None
    try:
        line = int(item.get("line", 0))
    except (TypeError, ValueError):
        line = 0
    severity = str(item.get("severity", "low")).lower()
    if severity not in _VALID_SEVERITIES:
        severity = "low"
    category = str(item.get("category", "other")).lower()
    if category not in _VALID_CATEGORIES:
        category = "other"
    message = str(item.get("message", ""))[:500]
    return {"file": file_, "line": line, "severity": severity, "category": category, "message": message}


def call_llm(prompt: str, base_url: str, model: str, api_key: str, timeout: int = TIMEOUT_SECONDS):
    """Returns a normalized findings list (possibly empty), or None on any
    network/timeout/parse failure. None and [] are deliberately distinct:
    None means 'this chunk was never reviewed', [] means 'reviewed, nothing
    found' - callers must not conflate the two (see main())."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
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
        raw = json.loads(_extract_json_array(content))
        if not isinstance(raw, list):
            return None
        return [n for n in (_normalize_finding(item) for item in raw) if n is not None]
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
    chunks_reviewed = 0
    secret_skipped = 0
    deadline = time.monotonic() + TOTAL_BUDGET_SECONDS
    for chunk in chunks:
        if time.monotonic() >= deadline:
            break
        if chunk_contains_secret(chunk["diff"]):
            secret_skipped += 1
            continue
        prompt = build_prompt(chunk, graph)
        findings = call_llm(prompt, base_url, model, api_key)
        if findings is not None:
            chunks_reviewed += 1
            all_findings.extend(findings)

    chunks_total = len(chunks)
    result = {"skipped": False, "findings": all_findings}

    if chunks_total > 0 and chunks_reviewed == 0:
        # Nothing was actually reviewed - this must read "skipped", never
        # "clean" (CLAUDE.md: LLM down/timeout -> comment says AI review
        # was skipped, not that it found nothing).
        if secret_skipped == chunks_total:
            reason = f"all {chunks_total} chunk(s) contained possible secrets - not sent to LLM"
        else:
            reason = f"LLM unavailable or returned invalid response for all {chunks_total} chunk(s)"
        result = {"skipped": True, "reason": reason}
    elif chunks_reviewed < chunks_total:
        result["partial"] = True
        result["chunks_reviewed"] = chunks_reviewed
        result["chunks_total"] = chunks_total

    with open("findings/ai_review.json", "w") as f:
        json.dump(result, f)


if __name__ == "__main__":
    main()
