"""AI review of the PR diff - OpenAI-compatible endpoint, gated by LLM_ALLOW_CLOUD.

Never fails the build on its own: a down/misconfigured/refused LLM writes
findings/ai_review.json with skipped=true instead of raising.
"""
import urllib.parse

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


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
