FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Gitleaks — same pin as jenkins/Dockerfile (ARG GITLEAKS_VERSION there);
# chatbot_indexer.py reuses scripts.ai_review.chunk_contains_secret, which
# shells out to the `gitleaks` binary, so it must exist in this image too.
ARG GITLEAKS_VERSION=8.18.4
RUN set -eux; \
    curl -sSfL \
      "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
      -o /tmp/gitleaks.tar.gz; \
    tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks; \
    chmod +x /usr/local/bin/gitleaks; \
    rm /tmp/gitleaks.tar.gz

COPY chatbot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# This service imports from the same scripts.* modules the PR pipeline uses
# (e.g. scripts.ai_review.chunk_contains_secret, scripts.chatbot_indexer) -
# keep both trees in sync.
COPY scripts/ scripts/
