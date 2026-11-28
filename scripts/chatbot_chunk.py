"""Fixed-size file chunking for the chatbot's indexer."""


def chunk_file(file_path: str, max_lines: int = 100) -> list:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        return []

    chunks = []
    for i in range(0, len(lines), max_lines):
        chunks.append({
            "file_path": file_path,
            "chunk_text": "".join(lines[i:i + max_lines]),
            "chunk_index": len(chunks),
        })
    return chunks
