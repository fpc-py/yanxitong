"""Cross-encoder reranker for paper search.

Dense retrieval (BGE-M3) is fast but shallow — it scores query and document
independently. A cross-encoder feeds (query, passage) as a pair and re-scores
each candidate with full attention between the two, which lifts precision at
top-k. We only rerank the shortlist the dense retriever already found, so the
cost is bounded by ``reranker_candidates``.

Degrades to a no-op (returns candidates unchanged) when:
- no reranker_model path is configured, or
- the model fails to load / score (logged, never raised — retrieval must not
  break because the reranker hiccupped).
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from src.core.config import get_settings

logger = logging.getLogger(__name__)

_model = None
_lock = threading.Lock()
_loaded_path: Optional[str] = None


def _get_model():
    """Lazily load the cross-encoder. Returns None when disabled or broken."""
    global _model, _loaded_path
    cfg = get_settings().retriever
    path = (cfg.reranker_model or "").strip()
    if not path:
        return None
    with _lock:
        if _model is not None and _loaded_path == path:
            return _model
        try:
            from sentence_transformers import CrossEncoder
            _model = CrossEncoder(path, max_length=512)
            _loaded_path = path
            logger.info("Reranker loaded: %s", path)
            return _model
        except Exception as e:
            logger.warning("Reranker load failed (%s); reranking disabled.", e)
            _model = None
            _loaded_path = None
            return None


def rerank(question: str, candidates: list[dict], top_k: int) -> list[dict]:
    """Re-score candidates with a cross-encoder. Returns up to ``top_k`` docs.

    Each candidate needs ``title`` / ``abstract`` (or ``text``) fields; the
    passage fed to the model is title + abstract snippet. On any failure the
    original ordering is preserved (graceful degradation).
    """
    model = _get_model()
    if model is None or not candidates:
        return candidates[:top_k]
    try:
        pairs = []
        for doc in candidates:
            passage = (doc.get("title", "") + ". " + (doc.get("abstract") or doc.get("text") or ""))[:512]
            pairs.append([question, passage])
        scores = model.predict(pairs)
        scored = list(zip(candidates, scores))
        scored.sort(key=lambda x: -float(x[1]))
        out = []
        for doc, s in scored[:top_k]:
            d = dict(doc)
            d["rerank_score"] = float(s)
            # keep dense similarity as tie-break, expose rerank as primary signal
            out.append(d)
        return out
    except Exception as e:
        logger.warning("Reranking degraded to dense order: %s", e)
        return candidates[:top_k]
