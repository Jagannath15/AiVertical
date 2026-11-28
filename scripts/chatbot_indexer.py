"""Orchestrates clone -> chunk -> secret-scan -> embed -> upsert -> graph.

Secret-scan runs on every chunk before it is ever embedded or upserted -
a chunk that fails is dropped, not redacted-and-kept (see docs/DECISIONS.md
and docs/superpowers/specs/2026-11-28-chatbot-design.md).
"""
import argparse
import hashlib
import os

from scripts.ai_review import _REVIEWABLE_PATTERNS, chunk_contains_secret
from scripts.chatbot_clone import clone_or_pull
from scripts.chatbot_chunk import chunk_file
from scripts.chatbot_embed import embed_texts
from scripts.chatbot_qdrant import ensure_collection, clear_collection, upsert_chunks
from scripts.chatbot_graph import build_repo_graph

_EXTENSIONS = tuple(p[1:] for p in _REVIEWABLE_PATTERNS if p.startswith("*."))
_BARE_NAMES = tuple(p for p in _REVIEWABLE_PATTERNS if not p.startswith("*."))


def _is_reviewable(filename: str) -> bool:
    return filename.endswith(_EXTENSIONS) or filename in _BARE_NAMES


def _walk_reviewable_files(clone_dir: str) -> list:
    found = []
    for dirpath, dirnames, filenames in os.walk(clone_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__" and not d.startswith(".")]
        for name in filenames:
            if _is_reviewable(name):
                found.append(os.path.join(dirpath, name))
    return found


def _chunk_id(file_path: str, chunk_index: int) -> str:
    return hashlib.sha256(f"{file_path}:{chunk_index}".encode()).hexdigest()


def reindex_repo(repo_id: str, token: str, clone_dir: str, graph_dir: str, qdrant_client, embed_model=None) -> dict:
    clone_or_pull(repo_id, clone_dir, token)

    files = _walk_reviewable_files(clone_dir)
    chunks_embedded = 0
    chunks_skipped_secret = 0
    kept_chunks = []

    for file_path in files:
        for chunk in chunk_file(file_path):
            if chunk_contains_secret(chunk["chunk_text"]):
                chunks_skipped_secret += 1
                continue
            kept_chunks.append(chunk)

    if kept_chunks:
        texts = [c["chunk_text"] for c in kept_chunks]
        vectors = embed_texts(texts, model=embed_model)
        chunks_embedded = len(vectors)

        ensure_collection(qdrant_client, repo_id, vector_size=len(vectors[0]))
        clear_collection(qdrant_client, repo_id)
        upsert_chunks(qdrant_client, repo_id, [
            {
                "id": _chunk_id(c["file_path"], c["chunk_index"]),
                "vector": v,
                "file_path": c["file_path"],
                "chunk_text": c["chunk_text"],
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
    parser.add_argument("--token", required=True)
    parser.add_argument("--clone-dir", required=True)
    parser.add_argument("--graph-dir", required=True)
    args = parser.parse_args()

    from qdrant_client import QdrantClient

    client = QdrantClient(
        host=os.environ.get("QDRANT_HOST", "qdrant"),
        port=int(os.environ.get("QDRANT_PORT", "6333")),
    )
    result = reindex_repo(args.repo_id, args.token, args.clone_dir, args.graph_dir, client)
    print(result)


if __name__ == "__main__":
    main()
