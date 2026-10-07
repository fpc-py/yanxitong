"""FAISS vector store with BGE-M3 embeddings for paper retrieval."""

import json
import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

from src.core.config import get_settings


class VectorStore:
    """FAISS-backed vector store using BGE-M3 embeddings."""

    def __init__(self, dim: Optional[int] = None, index_path: Optional[str] = None, encoder=None):
        settings = get_settings()
        self.dim = dim or settings.retriever.vector_dim
        self.model = encoder if encoder is not None else SentenceTransformer(settings.retriever.embedding_model)
        self._lock = threading.Lock()
        self._documents: dict[int, dict] = {}  # faiss_id -> doc
        self._next_id = 0

        if index_path and os.path.exists(index_path):
            self.load(index_path)
        else:
            self.index = faiss.IndexFlatIP(self.dim)  # inner product for cosine on normalized vectors

    def _embed(self, texts: list[str]) -> np.ndarray:
        """Convert texts to normalized embedding vectors."""
        embeddings = self.model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False
        )
        return np.array(embeddings).astype(np.float32)

    def warmup(self) -> None:
        """Run one throwaway embedding to pay lazy-load/compile costs up front.

        Called from application startup on a worker thread so the first user
        question does not absorb BGE-M3's ~35s cold start.
        """
        self._embed(["warmup"])

    def add_documents(self, docs: list[dict]) -> list[int]:
        """Add documents to the index. Each doc needs 'text' field for embedding.
        Returns list of internal IDs."""
        if not docs:
            return []
        texts = [doc.get("text", doc.get("abstract", doc.get("title", ""))) for doc in docs]
        embeddings = self._embed(texts)
        ids = []
        with self._lock:
            start_id = self._next_id
            self.index.add(embeddings)
            for i, doc in enumerate(docs):
                doc_id = start_id + i
                self._documents[doc_id] = {**doc, "_faiss_id": doc_id}
                ids.append(doc_id)
            self._next_id = start_id + len(docs)
        return ids

    def search(
        self,
        query: str,
        top_k: int = 10,
        scope: Optional[str] = None,
        scopes: Optional[list[str]] = None,
    ) -> list[dict]:
        """Search for documents similar to query. Returns docs with scores.

        When ``scope`` (a session id) or ``scopes`` (e.g. the KB zones
        ``["kb:team", "kb:user:3"]``) are given, only documents indexed under
        those scopes are returned — this is what keeps one research question's
        corpus from leaking into another's RAG context. FAISS cannot filter
        natively, so a wider candidate window is fetched and post-filtered.
        """
        if self.index.ntotal == 0:
            return []
        wanted = set(scopes) if scopes else ({scope} if scope else None)
        query_embedding = self._embed([query])
        if wanted:
            k = min(self.index.ntotal, max(top_k * 8, 64))
        else:
            k = min(top_k, self.index.ntotal)
        with self._lock:
            scores, indices = self.index.search(query_embedding, k)
        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx not in self._documents:
                continue
            doc = self._documents[idx]
            if wanted and doc.get("scope") not in wanted:
                continue
            doc = dict(doc)
            doc["similarity"] = float(score)
            results.append(doc)
            if len(results) >= top_k:
                break
        return results

    def save(self, path: str):
        """Persist index and documents to disk."""
        Path(path).mkdir(parents=True, exist_ok=True)
        with self._lock:
            faiss.write_index(self.index, os.path.join(path, "index.faiss"))
            with open(os.path.join(path, "docs.json"), "w", encoding="utf-8") as f:
                json.dump({"_next_id": self._next_id, "documents": self._documents}, f, ensure_ascii=False, default=str)

    def load(self, path: str):
        """Load index and documents from disk."""
        with self._lock:
            self.index = faiss.read_index(os.path.join(path, "index.faiss"))
            with open(os.path.join(path, "docs.json"), "r", encoding="utf-8") as f:
                data = json.load(f)
                self._next_id = data["_next_id"]
                self._documents = {int(k): v for k, v in data["documents"].items()}

    def __len__(self) -> int:
        return self.index.ntotal

    def clear(self):
        """Reset the index."""
        with self._lock:
            self.index = faiss.IndexFlatIP(self.dim)
            self._documents.clear()
            self._next_id = 0


# Global singleton
_vector_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        settings = get_settings()
        _vector_store = VectorStore(index_path=settings.retriever.index_path)
    return _vector_store
