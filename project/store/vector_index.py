"""Exact cosine search over a consistent SQLite snapshot using FAISS."""

import faiss
import numpy as np


def normalized(vectors, dimension: int) -> np.ndarray:
    matrix = np.array(vectors, dtype=np.float32, copy=True, order="C")
    if matrix.ndim != 2 or matrix.shape[1] != dimension:
        raise ValueError("Embedding dimension/shape does not match store configuration")
    norms = np.linalg.norm(matrix, axis=1)
    if not np.isfinite(matrix).all() or not np.isfinite(norms).all() or (norms == 0).any():
        raise ValueError("Embeddings must be finite and nonzero")
    faiss.normalize_L2(matrix)
    return matrix


class VectorIndex:
    def __init__(self, vectors: dict[str, list[float]], dimension: int):
        self.ids = sorted(vectors)
        self.dimension = dimension
        self.index = faiss.IndexFlatIP(dimension)
        if self.ids:
            self.index.add(normalized([vectors[key] for key in self.ids], dimension))

    def search(self, vector, *, k: int) -> list[tuple[str, float]]:
        if not self.ids:
            return []
        scores, positions = self.index.search(normalized([vector], self.dimension), len(self.ids))
        matches = [(self.ids[int(pos)], max(-1.0, min(1.0, float(score))))
                   for score, pos in zip(scores[0], positions[0]) if pos >= 0]
        # Stable tie-breaking for reproducible small-dataset development.
        return sorted(matches, key=lambda item: (-item[1], item[0]))[:k]
