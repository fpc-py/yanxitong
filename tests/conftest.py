"""Shared fixtures: deterministic fakes so tests never load BGE-M3 / Neo4j."""

import hashlib

import numpy as np
import pytest


class FakeEncoder:
    """Deterministic 8-dim encoder (hash-seeded) for VectorStore injection."""

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        vectors = []
        for text in texts:
            seed = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
            vec = np.random.default_rng(seed).standard_normal(8).astype(np.float32)
            vectors.append(vec / (np.linalg.norm(vec) + 1e-9))
        return np.array(vectors, dtype=np.float32)


@pytest.fixture()
def fake_encoder_cls():
    return FakeEncoder
