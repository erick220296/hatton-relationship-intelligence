FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE_PATH=/opt/models/fastembed

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl && rm -rf /var/lib/apt/lists/*

# Dependencies + models first (cached unless pyproject.toml changes): a stub package lets the
# editable install resolve without the real source, so code edits don't trigger a full reinstall.
COPY pyproject.toml ./
RUN mkdir -p src/hri && touch src/hri/__init__.py \
 && pip install -e ".[dev]" \
 && python -m spacy download en_core_web_sm \
 && python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

# Source last: a code change rebuilds only this layer (seconds).
COPY . .
CMD ["uvicorn", "hri.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
