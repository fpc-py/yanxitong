"""Canonical paper schema and cross-source merge/deduplication.

Every data source (arXiv, Semantic Scholar, OpenAlex) maps its payload onto
the same field names via :func:`normalize_paper`, then :func:`merge_dedup`
collapses duplicates using three keys, in priority order:

1. normalized DOI (exact)
2. arXiv ID (version suffix stripped)
3. normalized title (lowercased, punctuation removed, whitespace collapsed)

Merged records keep the first (richest) occurrence and absorb missing fields,
longer abstracts, citation counts, and union of per-source lists.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Canonical keys every paper dict carries after normalization. Values mirror
# the historical arXiv/S2 shapes so downstream consumers (vector store,
# citations, KG builder) need no changes.
PAPER_DEFAULTS: dict = {
    "title": "Untitled",
    "authors": [],
    "year": 0,
    "abstract": "",
    "url": "",
    "source": "",
    "doi": "",
    "arxiv_id": "",
    "citations_count": 0,
    "key_findings": [],
    "methods": [],
    "datasets": [],
    "metrics": {},
    "text": "",
}

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:")
_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v(\d+))?")
_ARXIV_OLD_ID_RE = re.compile(r"([a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v(\d+))?")


def normalize_doi(doi: str) -> str:
    """Return the bare lowercase DOI (``10.xxxx/yyy``), or ``""``."""
    if not doi:
        return ""
    d = doi.strip().lower()
    for prefix in _DOI_PREFIXES:
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.strip()


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace (CJK preserved)."""
    if not title:
        return ""
    t = title.lower()
    t = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def extract_arxiv_id(paper: dict) -> str:
    """Best-effort arXiv ID for a paper: explicit field, then URLs/ids."""
    explicit = (paper.get("arxiv_id") or "").strip()
    if explicit:
        return explicit
    haystacks = [
        paper.get("url", "") or "",
        (paper.get("external_ids") or {}).get("ArXiv", "") if isinstance(paper.get("external_ids"), dict) else "",
        paper.get("paper_id", "") or "",
        paper.get("corpus_id", "") or "",
    ]
    for text in haystacks:
        m = _ARXIV_ID_RE.search(str(text)) or _ARXIV_OLD_ID_RE.search(str(text))
        if m:
            return m.group(1)
    return ""


def arxiv_key(arxiv_id: str) -> str:
    """Dedup key for an arXiv ID: base id without version suffix."""
    if not arxiv_id:
        return ""
    m = _ARXIV_ID_RE.search(arxiv_id) or _ARXIV_OLD_ID_RE.search(arxiv_id)
    return m.group(1).lower() if m else arxiv_id.strip().lower()


def normalize_paper(paper: dict) -> dict:
    """Fill canonical fields with defaults, preserving source-specific extras."""
    p = dict(paper)
    for key, default in PAPER_DEFAULTS.items():
        value = p.get(key)
        if value is None:
            p[key] = default() if callable(default) else default
        elif key == "title" and not str(value).strip():
            p[key] = "Untitled"
        elif key == "authors" and not isinstance(value, list):
            p[key] = []
    if not p.get("text"):
        p["text"] = f"{p.get('title', '')}. {p.get('abstract', '')}".strip()
    p["doi"] = normalize_doi(p.get("doi", ""))
    if not p.get("arxiv_id"):
        p["arxiv_id"] = extract_arxiv_id(p)
    return p


def _merge_two(primary: dict, other: dict) -> dict:
    """Fold ``other`` into ``primary``, keeping the richest values."""
    merged = dict(primary)

    for key, default in PAPER_DEFAULTS.items():
        if merged.get(key) in (None, "", [], {}, 0) and other.get(key) not in (None, "", [], {}, 0):
            merged[key] = other.get(key)

    # Prefer the longer abstract (S2 sometimes truncates, OpenAlex rebuilds).
    if len(other.get("abstract") or "") > len(merged.get("abstract") or ""):
        merged["abstract"] = other["abstract"]
    if (other.get("citations_count") or 0) > (merged.get("citations_count") or 0):
        merged["citations_count"] = other["citations_count"]

    sources = merged.get("sources") or ([merged["source"]] if merged.get("source") else [])
    if other.get("source") and other["source"] not in sources:
        sources.append(other["source"])
    merged["sources"] = sources

    if not merged.get("text"):
        merged["text"] = f"{merged.get('title', '')}. {merged.get('abstract', '')}".strip()
    return merged


def merge_dedup(papers: list[dict]) -> list[dict]:
    """Deduplicate and merge papers from multiple sources into canonical dicts."""
    merged: list[dict] = []
    by_doi: dict[str, int] = {}
    by_arxiv: dict[str, int] = {}
    by_title: dict[str, int] = {}

    for raw in papers:
        p = normalize_paper(raw)
        doi_key = p.get("doi", "")
        arxiv_k = arxiv_key(p.get("arxiv_id", ""))
        title_key = normalize_title(p.get("title", ""))

        idx: Optional[int] = None
        if doi_key and doi_key in by_doi:
            idx = by_doi[doi_key]
        elif arxiv_k and arxiv_k in by_arxiv:
            idx = by_arxiv[arxiv_k]
        elif title_key and title_key in by_title:
            idx = by_title[title_key]

        if idx is None:
            merged.append(p)
            idx = len(merged) - 1
        else:
            merged[idx] = _merge_two(merged[idx], p)

        if doi_key:
            by_doi.setdefault(doi_key, idx)
        if arxiv_k:
            by_arxiv.setdefault(arxiv_k, idx)
        if title_key:
            by_title.setdefault(title_key, idx)

    if len(merged) != len(papers):
        logger.debug("merge_dedup: %d papers -> %d after cross-source merge", len(papers), len(merged))
    return merged
