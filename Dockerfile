FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# Install system dependencies for tree-sitter native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency files first for layer caching
COPY pyproject.toml .
COPY src/ src/

# Install the package and all dependencies
RUN pip install --no-cache-dir -e .

ENV FASTEMBED_CACHE_PATH=/app/.cache/fastembed
RUN python -c "\
from fastembed import TextEmbedding; \
import os; \
list(TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir=os.environ['FASTEMBED_CACHE_PATH']).embed(['warmup']))" \
&& echo "Embedding model pre-downloaded."

# /repos is the mount point for the host code directory
VOLUME ["/repos"]

# /data is the mount point for the embedded graph DB
VOLUME ["/data"]

# Expose HTTP/SSE port
EXPOSE 7832

# The MCP server speaks stdio — no port exposed
CMD ["python", "-u", "-m", "fedora-nexus-core.mcp.server"]
