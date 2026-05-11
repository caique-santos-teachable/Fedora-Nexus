"""Embedding index backed by a numpy .npz file — co-located with the Kuzu DB."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

# Text to embed for each symbol: "{name} {content[:500]}"
def _symbol_text(name: str, content: str) -> str:
    return f"{name} {content[:500]}".strip()


def _index_path(db_path: str, root_path: str) -> Path:
    """Return the .npz path for this (db, repo) pair."""
    repo_hash = hashlib.sha256(root_path.encode()).hexdigest()[:16]
    return Path(db_path).parent / "embeddings" / f"{repo_hash}.npz"


def build_index(db_path: str, root_path: str, symbols: list[dict]) -> bool:
    """Embed symbols and save to .npz. Returns True if successful, False if symbols list is empty."""
    if not symbols:
        return False

    texts = [_symbol_text(s.get("name", ""), s.get("content", "")) for s in symbols]
    ids = [s["id"] for s in symbols]

    logger.info("[EMBED] Embedding %d symbols for %s ...", len(symbols), root_path)
    model = _get_model()
    vectors = np.array(list(model.embed(texts)), dtype=np.float32)

    out_path = _index_path(db_path, root_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(str(out_path), ids=np.array(ids), vectors=vectors)
    logger.info("[EMBED] Index saved → %s (%d vectors, %d dims)", out_path, len(ids), vectors.shape[1])
    return True


def load_index(db_path: str, root_path: str) -> tuple[list[str], np.ndarray] | None:
    """Load the embedding index. Returns (ids, vectors) or None if not found."""
    path = _index_path(db_path, root_path)
    if not path.exists():
        return None
    data = np.load(str(path), allow_pickle=False)
    ids = data["ids"].tolist()
    vectors = data["vectors"]
    return ids, vectors


def delete_index(db_path: str, root_path: str) -> None:
    """Remove the embedding index for a repo."""
    path = _index_path(db_path, root_path)
    if path.exists():
        path.unlink()
        logger.debug("[EMBED] Deleted index: %s", path)


def semantic_search(
    ids: list[str],
    vectors: np.ndarray,
    query: str,
    k: int,
) -> list[tuple[str, float]]:
    """Cosine similarity search. Returns [(symbol_id, score)] sorted descending."""
    model = _get_model()
    query_vec = np.array(list(model.embed([query])), dtype=np.float32)[0]
    # Cosine similarity: (vectors @ query_vec) / (||vectors|| * ||query_vec||)
    norms = np.linalg.norm(vectors, axis=1)
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return []
    scores = (vectors @ query_vec) / (norms * query_norm + 1e-10)
    top_k_idx = np.argpartition(scores, -min(k, len(scores)))[-min(k, len(scores)):]
    top_k_idx = top_k_idx[np.argsort(scores[top_k_idx])[::-1]]
    return [(ids[i], float(scores[i])) for i in top_k_idx]


def rrf_fuse(
    bm25_results: list[dict],
    semantic_results: list[tuple[str, float]],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion. Returns [(symbol_id, rrf_score)] sorted descending."""
    scores: dict[str, float] = {}
    for rank, item in enumerate(bm25_results, 1):
        sid = item.get("id", "")
        scores[sid] = scores.get(sid, 0.0) + 1.0 / (k + rank)
    for rank, (sid, _) in enumerate(semantic_results, 1):
        scores[sid] = scores.get(sid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


_MODEL_INSTANCE = None


def _get_model():
    global _MODEL_INSTANCE
    if _MODEL_INSTANCE is None:
        import os
        cache_dir = os.environ.get("FASTEMBED_CACHE_PATH") or None
        logger.info("[EMBED] Loading embedding model ...")
        _MODEL_INSTANCE = TextEmbedding(
            model_name="BAAI/bge-small-en-v1.5",
            cache_dir=cache_dir,
            # Limit ONNX Runtime intra-op threads so background embedding doesn't
            # saturate the CPU when a concurrent index request is being parsed.
            threads=2,
        )
        logger.info("[EMBED] Model ready.")
    return _MODEL_INSTANCE
