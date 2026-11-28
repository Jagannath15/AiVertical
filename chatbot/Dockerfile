FROM python:3.11-slim

WORKDIR /app

COPY chatbot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# This service imports from the same scripts.* modules the PR pipeline uses
# (e.g. scripts.ai_review.chunk_contains_secret, scripts.chatbot_indexer) -
# keep both trees in sync.
COPY scripts/ scripts/
