"""Local embeddings for the chatbot's indexer - no network call, ever."""

_MODEL_NAME = "all-MiniLM-L6-v2"
_cached_model = None


def _get_model():
    global _cached_model
    if _cached_model is None:
        from sentence_transformers import SentenceTransformer
        _cached_model = SentenceTransformer(_MODEL_NAME)
    return _cached_model


def embed_texts(texts: list, model=None) -> list:
    if not texts:
        return []
    if model is None:
        model = _get_model()
    return list(model.encode(texts))
