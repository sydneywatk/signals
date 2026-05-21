"""Document similarity via sentence-transformers (`all-MiniLM-L6-v2`).

Build context: Voyage AI was the first choice, but the free tier caps at 3
RPM / 10K TPM, which our ~150-doc cold runs blow through every time even
with aggressive throttling. To preserve the "fixtures-first, zero keys"
contract, we fall back to `all-MiniLM-L6-v2` — small (80 MB), local, fast on
CPU, deterministic, no API costs. Embeddings are 384-dim dense vectors.

A `Corpus` wraps a batch of documents into a normalized matrix and exposes
cosine similarity + single-link clustering. Embeddings are cached on disk
under `data/embeddings_cache.json` (keyed by SHA-256 of model+text). The
cache is purely a runtime optimization since compute is local — fixtures
work without it.

If we later upgrade to a paid Voyage account, swap by editing this module
only; the detector API is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from signals.settings import DATA_DIR

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
_CACHE_PATH: Path = DATA_DIR / "embeddings_cache.json"


@dataclass
class Cluster:
    indices: list[int]
    cohesion: float


class Embedder:
    """Singleton wrapper around the SentenceTransformer model + disk cache."""

    _instance: "Embedder | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "Embedder":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._cache: dict[str, list[float]] = {}
        self._model = None
        if _CACHE_PATH.exists():
            try:
                self._cache = json.loads(_CACHE_PATH.read_text())
                logger.info("Loaded embedding cache: %d entries", len(self._cache))
            except json.JSONDecodeError:
                logger.warning("Embedding cache corrupted; starting empty")
        self._dirty = False

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(f"{EMBEDDING_MODEL}|{text}".encode("utf-8")).hexdigest()[:24]

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading SentenceTransformer model %s ...", EMBEDDING_MODEL)
            self._model = SentenceTransformer(EMBEDDING_MODEL)
        return self._model

    def embed(self, texts: list[str], *, input_type: str = "document") -> np.ndarray:
        """Return an (N, D) numpy array of L2-normalized embeddings.

        `input_type` is accepted for API parity with Voyage-style embedders but
        sentence-transformers doesn't differentiate query/document.
        """
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)

        missing_idx: list[int] = []
        missing_texts: list[str] = []
        for i, t in enumerate(texts):
            if self._key(t) not in self._cache:
                missing_idx.append(i)
                missing_texts.append(t)

        if missing_texts:
            model = self._ensure_model()
            logger.info("Embedding %d new texts (cache hit on %d)",
                        len(missing_texts), len(texts) - len(missing_texts))
            vectors = model.encode(missing_texts, normalize_embeddings=True,
                                    show_progress_bar=False)
            for t, vec in zip(missing_texts, vectors):
                self._cache[self._key(t)] = vec.tolist()
            self._dirty = True
            self._persist()

        return np.asarray([self._cache[self._key(t)] for t in texts], dtype=np.float32)

    def _persist(self) -> None:
        if not self._dirty:
            return
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(self._cache))
        self._dirty = False


class Corpus:
    def __init__(self, documents: list[str]):
        if not documents:
            raise ValueError("Corpus requires at least one document")
        self.documents = documents
        self.matrix = Embedder().embed(documents)
        self._sim: np.ndarray | None = None

    def similarity_matrix(self) -> np.ndarray:
        if self._sim is None:
            self._sim = cosine_similarity(self.matrix)
        return self._sim

    def cluster(self, threshold: float) -> list[Cluster]:
        n = self.matrix.shape[0]
        sim = self.similarity_matrix()
        visited = [False] * n
        clusters: list[Cluster] = []
        for i in range(n):
            if visited[i]:
                continue
            members = [i]
            visited[i] = True
            queue = [i]
            while queue:
                cur = queue.pop()
                for j in range(n):
                    if not visited[j] and sim[cur, j] >= threshold:
                        members.append(j)
                        visited[j] = True
                        queue.append(j)
            clusters.append(Cluster(indices=sorted(members),
                                     cohesion=_mean_pairwise(sim, members)))
        return clusters


def _mean_pairwise(sim: np.ndarray, indices: list[int]) -> float:
    if len(indices) < 2:
        return 1.0
    pairs = [sim[a, b] for i, a in enumerate(indices) for b in indices[i + 1:]]
    return float(np.mean(pairs))


def pairwise_similarity(text_a: str, text_b: str) -> float:
    matrix = Embedder().embed([text_a, text_b])
    return float(cosine_similarity(matrix[0:1], matrix[1:2])[0, 0])


def similarity_to_corpus(query: str, corpus: list[str]) -> list[float]:
    if not corpus:
        return []
    embedder = Embedder()
    q = embedder.embed([query])
    c = embedder.embed(corpus)
    sims = cosine_similarity(q, c)[0]
    return [float(s) for s in sims]


# Backward-compat alias
TfidfCorpus = Corpus
