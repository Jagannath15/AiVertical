"""Orchestrates clone -> chunk -> secret-scan -> embed -> upsert -> graph.

Secret-scan runs on the WHOLE FILE text before it is ever chunked, embedded,
or upserted - a per-chunk-only scan would miss a multiline secret (e.g. a PEM
key) that straddles a chunk boundary. A file that fails the scan is dropped
entirely, not redacted-and-kept (see docs/DECISIONS.md and
docs/superpowers/specs/2026-11-28-chatbot-design.md).
"""
import argparse
import os
import re
import threading
import uuid

from scripts.ai_review import _REVIEWABLE_PATTERNS, chunk_contains_secret
from scripts.chatbot_clone import clone_or_pull
from scripts.chatbot_chunk import chunk_file
from scripts.chatbot_embed import embed_texts
from scripts.chatbot_qdrant import ensure_collection, clear_collection, upsert_chunks
from scripts.chatbot_graph import build_repo_graph

_EXTENSIONS = tuple(p[1:] for p in _REVIEWABLE_PATTERNS if p.startswith("*."))
_BARE_NAMES = tuple(p for p in _REVIEWABLE_PATTERNS if not p.startswith("*."))

# all-MiniLM-L6-v2's output dimensionality (scripts/chatbot_embed.py) - needed
# to create/clear a repo's collection even on a run that keeps zero chunks.
EMBEDDING_DIM = 384

_ID_NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")

_REPO_ID_PART_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_repo_locks = {}
_repo_locks_guard = threading.Lock()


def _is_reviewable(filename: str) -> bool:
    return filename.endswith(_EXTENSIONS) or filename in _BARE_NAMES


def _walk_reviewable_files(clone_dir: str) -> list:
    """Never follows a symlink, and never returns a path whose real location
    is outside clone_dir - without this, a committer could symlink a file
    from a sibling repo (or /proc/self/environ) into their own repo and have
    it indexed into a collection other people can query (see the design
    doc's per-repo isolation goal - this closes the one path that bypassed
    it)."""
    clone_real = os.path.realpath(clone_dir)
    found = []
    for dirpath, dirnames, filenames in os.walk(clone_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.startswith(".")]
        for name in filenames:
            if not _is_reviewable(name):
                continue
            full_path = os.path.join(dirpath, name)
            if os.path.islink(full_path):
                continue
            if not os.path.realpath(full_path).startswith(clone_real + os.sep):
                continue
            found.append(full_path)
    return found


def _chunk_id(repo_id: str, rel_path: str, chunk_index: int) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, f"{repo_id}:{rel_path}:{chunk_index}"))


def _validate_repo_id(repo_id: str) -> str:
    """Rejects path traversal (`..`, empty segments) and normalizes case -
    GitHub repo slugs are case-insensitive, so Org/Repo and org/repo must
    map to the same collection/graph, not two stale, diverging ones."""
    parts = repo_id.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"invalid repo_id: {repo_id!r}")
    for part in parts:
        if part in (".", "..") or ".." in part or not _REPO_ID_PART_RE.match(part):
            raise ValueError(f"invalid repo_id: {repo_id!r}")
    return repo_id.lower()


def _get_repo_lock(repo_id: str) -> threading.Lock:
    with _repo_locks_guard:
        if repo_id not in _repo_locks:
            _repo_locks[repo_id] = threading.Lock()
        return _repo_locks[repo_id]


def reindex_repo(repo_id: str, token: str, clone_dir: str, graph_dir: str, qdrant_client, embed_model=None) -> dict:
    repo_id = _validate_repo_id(repo_id)

    with _get_repo_lock(repo_id):
        clone_or_pull(repo_id, clone_dir, token)

        files = _walk_reviewable_files(clone_dir)
        chunks_skipped_secret = 0
        kept_chunks = []

        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    whole_text = f.read()
            except (UnicodeDecodeError, OSError):
                continue

            file_chunks = chunk_file(file_path)
            if chunk_contains_secret(whole_text):
                chunks_skipped_secret += len(file_chunks)
                continue

            rel_path = os.path.relpath(file_path, clone_dir).replace(os.sep, "/")
            for chunk in file_chunks:
                chunk["rel_path"] = rel_path
            kept_chunks.extend(file_chunks)

        chunks_embedded = 0

        # Always create-if-missing and clear, whether or not anything was
        # kept this run - otherwise a merge that deletes every file (or one
        # where every chunk gets flagged as a secret) leaves the previous
        # index queryable forever, which is exactly what clear_collection
        # exists to prevent.
        ensure_collection(qdrant_client, repo_id, vector_size=EMBEDDING_DIM)
        clear_collection(qdrant_client, repo_id)

        if kept_chunks:
            texts = [c["chunk_text"] for c in kept_chunks]
            vectors = embed_texts(texts, model=embed_model)
            chunks_embedded = len(vectors)

            upsert_chunks(qdrant_client, repo_id, [
                {
                    "id": _chunk_id(repo_id, c["rel_path"], c["chunk_index"]),
                    "vector": v,
                    "file_path": c["rel_path"],
                    "chunk_text": c["chunk_text"],
                    "repo_id": repo_id,
                }
                for c, v in zip(kept_chunks, vectors)
            ])

        build_repo_graph(repo_id, clone_dir, graph_dir)

        return {
            "files_indexed": len(files),
            "chunks_embedded": chunks_embedded,
            "chunks_skipped_secret": chunks_skipped_secret,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--clone-dir", required=True)
    parser.add_argument("--graph-dir", required=True)
    args = parser.parse_args()

    # Token comes from the environment, never argv - argv is visible in `ps`
    # and /proc/<pid>/cmdline to any process on the host.
    token = os.environ.get("CHATBOT_CLONE_TOKEN", "")
    if not token:
        raise SystemExit("CHATBOT_CLONE_TOKEN must be set in the environment")

    from qdrant_client import QdrantClient

    client = QdrantClient(
        host=os.environ.get("QDRANT_HOST", "qdrant"),
        port=int(os.environ.get("QDRANT_PORT", "6333")),
    )
    result = reindex_repo(args.repo_id, token, args.clone_dir, args.graph_dir, client)
    print(result)


if __name__ == "__main__":
    main()
