"""Document similarity via scikit-learn TF-IDF.

Trade-off vs Claude/Voyage/sentence-transformer embeddings: TF-IDF captures
vocabulary overlap, not semantic paraphrase. For same-topic bill clustering
(Signal A) and model-bill matching (Signal D3), vocabulary overlap is usually
the dominant similarity signal — coordinated multistate bills are typically
near-verbatim copies. DECISION_MEMO documents the swap path to dense embeddings.

No fixture mode needed — vectors are computed locally and deterministically.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)


@dataclass
class Cluster:
    indices: list[int]
    cohesion: float  # mean pairwise similarity inside the cluster


class TfidfCorpus:
    """Fit a TF-IDF model over a corpus, expose similarity + clustering."""

    def __init__(self, documents: list[str], *, ngram_range: tuple[int, int] = (1, 2),
                 max_features: int = 10000):
        if not documents:
            raise ValueError("TfidfCorpus requires at least one document")
        self.documents = documents
        self.vectorizer = TfidfVectorizer(
            ngram_range=ngram_range,
            stop_words="english",
            max_features=max_features,
            lowercase=True,
        )
        self.matrix = self.vectorizer.fit_transform(documents)
        self._sim: np.ndarray | None = None

    def similarity_matrix(self) -> np.ndarray:
        if self._sim is None:
            self._sim = cosine_similarity(self.matrix)
        return self._sim

    def cluster(self, threshold: float) -> list[Cluster]:
        """Single-link clustering: documents with similarity >= threshold group together."""
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
            cohesion = _mean_pairwise_similarity(sim, members)
            clusters.append(Cluster(indices=sorted(members), cohesion=cohesion))
        return clusters


def _mean_pairwise_similarity(sim: np.ndarray, indices: list[int]) -> float:
    if len(indices) < 2:
        return 1.0
    pairs = [sim[a, b] for i, a in enumerate(indices) for b in indices[i + 1:]]
    return float(np.mean(pairs))


def pairwise_similarity(text_a: str, text_b: str) -> float:
    """One-off cosine similarity between two texts."""
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", lowercase=True)
    m = vec.fit_transform([text_a, text_b])
    return float(cosine_similarity(m[0:1], m[1:2])[0, 0])


def similarity_to_corpus(query: str, corpus: list[str]) -> list[float]:
    """Cosine of `query` against each doc in `corpus`. Useful for matching one bill against the model corpus."""
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", lowercase=True)
    all_docs = [query] + corpus
    m = vec.fit_transform(all_docs)
    sims = cosine_similarity(m[0:1], m[1:])[0]
    return [float(s) for s in sims]
