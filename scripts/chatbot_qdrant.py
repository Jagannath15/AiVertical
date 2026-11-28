"""Per-repo Qdrant collections for the chatbot - never one shared collection
with a repo_id filter (a missing filter there would silently leak cross-repo;
a wrong collection name here just errors)."""

_DEFAULT_BATCH_SIZE = 256


def collection_name(repo_id: str) -> str:
    return "repo__" + repo_id.replace("/", "__")


def ensure_collection(client, repo_id: str, vector_size: int) -> None:
    from qdrant_client.models import Distance, VectorParams

    name = collection_name(repo_id)
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )


def clear_collection(client, repo_id: str) -> None:
    from qdrant_client.models import FilterSelector

    client.delete(collection_name=collection_name(repo_id), points_selector=FilterSelector(filter={}))


def upsert_chunks(client, repo_id: str, chunks: list, batch_size: int = _DEFAULT_BATCH_SIZE) -> None:
    from qdrant_client.models import PointStruct

    name = collection_name(repo_id)
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        points = [
            PointStruct(
                id=chunk["id"],
                vector=chunk["vector"],
                payload={
                    "file_path": chunk["file_path"],
                    "chunk_text": chunk["chunk_text"],
                    "repo_id": chunk.get("repo_id", repo_id),
                },
            )
            for chunk in batch
        ]
        client.upsert(collection_name=name, points=points)
