"""Embedding model and cross-encoder reranker, loaded once per process.

RAG_EMBEDDER=bge-m3 (default) uses BAAI/bge-m3 (1024-dim dense vectors). RAG_EMBEDDER=hash is a
deterministic bag-of-words embedder for unit tests that must not download a 2 GB model.
"""

import hashlib
import threading

import numpy as np

from shared.settings import env
from shared.text import terms

DIM = 1024
# One lock for loading AND inference: loading a float16 model temporarily changes torch's
# process-wide default dtype, so nothing may run on the models while another one loads.
_lock = threading.RLock()
_instances: dict[str, object] = {}


def _device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


class HashEmbedder:
    name = "hash"

    def encode(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        vectors = np.zeros((len(texts), DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            for term in terms(text):
                digest = hashlib.md5(term.encode()).digest()
                vectors[row, int.from_bytes(digest[:4], "little") % DIM] += 1.0
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1, norms)


class BgeM3Embedder:
    name = "BAAI/bge-m3"

    def __init__(self):
        from sentence_transformers import SentenceTransformer
        device = _device()
        self.model = SentenceTransformer(self.name, device=device,
                                         model_kwargs={"torch_dtype": "float16"} if device == "cuda" else {})
        self.model.max_seq_length = 1024

    def encode(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        with _lock:
            return self.model.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                                     convert_to_numpy=True, show_progress_bar=False).astype(np.float32)


class Reranker:
    name = "BAAI/bge-reranker-v2-m3"

    def __init__(self):
        from sentence_transformers import CrossEncoder
        device = _device()
        self.model = CrossEncoder(self.name, device=device, max_length=1024,
                                  model_kwargs={"torch_dtype": "float16"} if device == "cuda" else {})

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        # CrossEncoder applies a sigmoid to the single logit, so scores are 0..1 relevance values
        # that can be compared with the threshold τ.
        with _lock:
            scores = self.model.predict([(query, passage) for passage in passages], batch_size=16,
                                        show_progress_bar=False)
        return [float(value) for value in np.asarray(scores, dtype=np.float64)]


def _shared(name: str, factory):
    if name not in _instances:
        with _lock:
            if name not in _instances:
                _instances[name] = factory()
    return _instances[name]


def embedder():
    return _shared("embedder", lambda: HashEmbedder() if env("RAG_EMBEDDER", "bge-m3") == "hash"
                   else BgeM3Embedder())


def reranker() -> Reranker:
    return _shared("reranker", Reranker)
