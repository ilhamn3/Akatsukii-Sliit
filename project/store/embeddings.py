"""Local semantic embeddings, downloaded only on explicit first use."""

import os
from pathlib import Path
from typing import Protocol
import numpy as np


class Embedder(Protocol):
    model_id: str
    dimension: int

    def encode(self, texts: list[str]) -> np.ndarray: ...


def storage_root() -> Path:
    configured = os.getenv("FACTORY_MEMORY_STORAGE")
    if configured:
        return Path(configured)
    root = Path(__file__).resolve().parents[2] / ".factory_memory"
    root.mkdir(parents=True, exist_ok=True)
    return root


class MiniLMEmbedder:
    # Pin the model revision in persisted model identity to prevent mixing spaces.
    model_id = "sentence-transformers/all-MiniLM-L6-v2@c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
    dimension = 384

    def __init__(self, *, local_files_only: bool = False):
        self.local_files_only = local_files_only
        self._model = None

    def encode(self, texts: list[str]) -> np.ndarray:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            name, revision = self.model_id.split("@")
            self._model = SentenceTransformer(
                name, revision=revision, device="cpu", trust_remote_code=False,
                cache_folder=str(storage_root() / "cache" / "sentence-transformers"),
                local_files_only=self.local_files_only,
            )
        return self._model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
