"""
Embedding generator — three providers in priority order:

1. local       → sentence-transformers (best quality, 384-dim, requires HF download)
2. gemini      → Google text-embedding-004 (768-dim, free API tier)
3. hash        → sklearn HashingVectorizer (offline fallback, 384-dim, zero download)

Set EMBEDDING_PROVIDER in .env. Default: "local".
The hash provider is used automatically if local download fails.
"""
from __future__ import annotations
import struct
from loguru import logger
from career_agent.config import settings


# ── Providers ─────────────────────────────────────────────────────

class LocalEmbedder:
    """sentence-transformers, fully offline after first download, 384-dim."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading local model: {model_name}")
        self._model = SentenceTransformer(model_name, trust_remote_code=True)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(
            texts, convert_to_numpy=True, normalize_embeddings=True
        )
        return vecs.tolist()

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class GeminiEmbedder:
    """Google text-embedding-004, 768-dim, free tier (1500 req/day)."""

    def __init__(self):
        import google.generativeai as genai
        genai.configure(api_key=settings.gemini_api_key)
        self._genai = genai
        logger.info("Gemini embedding provider ready")

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = self._genai.embed_content(
            model="models/gemini-embedding-001",
            content=texts,
            task_type="retrieval_document",
        )
        return [e for e in result["embedding"]]

    def embed_one(self, text: str) -> list[float]:
        result = self._genai.embed_content(
            model="models/gemini-embedding-001",
            content=text,
            task_type="retrieval_query",
        )
        return result["embedding"]


class HashEmbedder:
    """
    Offline fallback — sklearn HashingVectorizer → 384-dim L2-normalised vectors.
    Zero model download. Works in air-gapped environments.
    Quality: good for keyword overlap; not as semantic as transformers.
    Use this in sandboxes or as a bootstrap before the real model loads.
    """

    def __init__(self, dim: int = 384):
        from sklearn.feature_extraction.text import HashingVectorizer
        import numpy as np
        self._np = np
        self._vec = HashingVectorizer(
            n_features=dim,
            norm="l2",
            alternate_sign=False,
            analyzer="word",
            ngram_range=(1, 2),
            lowercase=True,
        )
        logger.info(f"HashEmbedder ready (dim={dim}) — offline fallback mode")

    def embed(self, texts: list[str]) -> list[list[float]]:
        mat = self._vec.transform(texts).toarray()
        return mat.tolist()

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


# ── Factory ───────────────────────────────────────────────────────

def get_embedder():
    provider = settings.embedding_provider

    if provider == "gemini":
        return GeminiEmbedder()

    if provider == "hash":
        return HashEmbedder(dim=settings.embedding_dim)

    # Default: try local (sentence-transformers); fall back to hash
    try:
        return LocalEmbedder(model_name=settings.embedding_model)
    except Exception as e:
        logger.warning(
            f"sentence-transformers unavailable ({e}). "
            "Falling back to HashEmbedder. "
            "Set EMBEDDING_PROVIDER=hash to silence this warning."
        )
        return HashEmbedder(dim=settings.embedding_dim)


# ── Byte packing for sqlite-vec storage ──────────────────────────

def vec_to_bytes(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)

def bytes_to_vec(b: bytes) -> list[float]:
    n = len(b) // 4
    return list(struct.unpack(f"{n}f", b))


# ── Singleton ─────────────────────────────────────────────────────

_embedder = None

def embedder():
    global _embedder
    if _embedder is None:
        _embedder = get_embedder()
    return _embedder
