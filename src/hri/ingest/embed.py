"""Local embeddings (BAAI/bge-small-en-v1.5 via fastembed/ONNX). Runs in-region; no API call."""
from __future__ import annotations

from functools import lru_cache

import numpy as np

MODEL = "BAAI/bge-small-en-v1.5"


@lru_cache
def _model():
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL)


def embed_texts(texts: list[str]) -> list[np.ndarray]:
    return list(_model().embed(texts, batch_size=64))


def embed_one(text: str) -> np.ndarray:
    return embed_texts([text])[0]
