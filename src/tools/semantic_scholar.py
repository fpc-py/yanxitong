"""Semantic Scholar API client.

Implements the free-tier etiquette of the Semantic Scholar Graph API:

* Sliding-window rate limiting (100 requests per 5 minutes) so bursts of
  agent calls queue instead of getting throttled server-side.
* Optional ``x-api-key`` support via the ``SEMANTIC_SCHOLAR_API_KEY``
  environment variable (raising the quota to the paid tier).
* Exponential-backoff retries, honouring the ``Retry-After`` header on 429.
"""

import asyncio
import logging
import os
from typing import Optional

import httpx

from src.core.config import get_settings

logger = logging.getLogger(__name__)

SS_API_BASE = "https://api.semanticscholar.org/graph/v1"
SS_API_KEY_ENV = "SEMANTIC_SCHOLAR_API_KEY"


class SemanticScholarClient:
    """Async Semantic Scholar API client."""

    FIELDS = "title,authors,year,abstract,url,externalIds,citationCount,publicationTypes,journal,fieldsOfStudy"

    RATE_LIMIT_REQUESTS: int = 100
    """Free-tier quota: 100 requests per window."""

    RATE_WINDOW_SECONDS: float = 300.0
    """Quota window length in seconds (5 minutes)."""

    MAX_RETRIES: int = 3
    """Number of attempts per API call before giving up."""

    BACKOFF_BASE: float = 2.0
    """Base backoff in seconds; doubled per retry, plus jitter."""

    def __init__(self):
        self.settings = get_settings()
        self._api_key: Optional[str] = os.getenv(SS_API_KEY_ENV) or None
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Rate limiting / transport
    # ------------------------------------------------------------------ #

    async def _rate_limit(self):
        """Block until a slot is free in the rolling 100-per-5-min window."""
        async with self._lock:
            while True:
                now = asyncio.get_running_loop().time()
                self._timestamps = [
                    t for t in self._timestamps if now - t < self.RATE_WINDOW_SECONDS
                ]
                if len(self._timestamps) < self.RATE_LIMIT_REQUESTS:
                    self._timestamps.append(now)
                    return
                sleep_for = self.RATE_WINDOW_SECONDS - (now - self._timestamps[0])
                logger.info(
                    "Semantic Scholar quota reached; sleeping %.1fs", max(sleep_for, 0.1)
                )
                await asyncio.sleep(max(sleep_for, 0.1))

    def _headers(self) -> dict:
        headers = {"User-Agent": "yanxitong/2.0 research-literature agent"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    async def _get_with_retry(self, path: str, params: dict) -> Optional[dict]:
        """GET ``SS_API_BASE + path`` as JSON with rate limiting and retries.

        Returns the decoded payload, or ``None`` after all attempts fail.
        """
        last_error: Optional[BaseException] = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            await self._rate_limit()
            try:
                async with httpx.AsyncClient(timeout=30.0, headers=self._headers()) as client:
                    response = await client.get(f"{SS_API_BASE}{path}", params=params)
                    if response.status_code == 429:
                        # Free-tier throttling is persistent without an API key,
                        # and the server's Retry-After (often 60s) would stall the
                        # whole agent request. Fail fast so the workflow degrades
                        # gracefully instead of blocking the UI.
                        logger.warning(
                            "Semantic Scholar 429 (rate limited); skipping this source. "
                            "Set %s for a higher quota.",
                            SS_API_KEY_ENV,
                        )
                        return None
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code
                # 404/400/403 will not fix themselves on retry.
                if 400 <= status < 500 and status != 429:
                    logger.error(f"Semantic Scholar client error {status}, not retrying: {e}")
                    return None
            except (httpx.HTTPError, ValueError) as e:
                last_error = e

            backoff = self.BACKOFF_BASE * (2 ** (attempt - 1))
            logger.warning(
                "Semantic Scholar request failed (attempt %d/%d): %s; retrying in %.1fs",
                attempt, self.MAX_RETRIES, last_error, backoff,
            )
            await asyncio.sleep(backoff)

        logger.error(f"Semantic Scholar API error after {self.MAX_RETRIES} attempts: {last_error}")
        return None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def search(self, query: str, max_results: Optional[int] = None) -> list[dict]:
        """Search Semantic Scholar for papers."""
        max_results = max_results or self.settings.retriever.semantic_scholar_max_results
        params = {
            "query": query,
            "limit": min(max_results, 100),
            "fields": self.FIELDS,
        }

        data = await self._get_with_retry("/paper/search", params)
        if data is None:
            return []

        return self._parse_results(data.get("data", []))

    def _parse_results(self, raw_papers: list[dict]) -> list[dict]:
        papers = []
        for p in raw_papers:
            paper_id = p.get("paperId", "")
            external_ids = p.get("externalIds", {}) or {}
            tldr = (p.get("tldr") or {}).get("text", "")
            abstract = p.get("abstract") or tldr or ""

            papers.append({
                "title": p.get("title", "Untitled"),
                "authors": [a.get("name", "") for a in (p.get("authors") or [])],
                "year": p.get("year", 0),
                "abstract": abstract,
                "url": p.get("url", f"https://www.semanticscholar.org/paper/{paper_id}"),
                "source": "semantic_scholar",
                "doi": external_ids.get("DOI", ""),
                "paper_id": paper_id,
                "corpus_id": external_ids.get("CorpusId", ""),
                "citations_count": p.get("citationCount", 0),
                "fields_of_study": p.get("fieldsOfStudy") or [],
                "publication_types": p.get("publicationTypes") or [],
                "journal": (p.get("journal") or {}).get("name", ""),
                "tldr": tldr,
                "key_findings": [],
                "methods": [],
                "datasets": [],
                "metrics": {},
                "text": f"{p.get('title', '')}. {abstract}",
            })
        return papers

    async def get_paper(self, paper_id: str) -> Optional[dict]:
        """Get detailed info for a specific paper (incl. citations/references).

        Args:
            paper_id: A Semantic Scholar ``paperId``, DOI, ARXiv id, Mag id,
                ACL id, or ``CorpusId:<n>`` string.
        """
        params = {"fields": f"{self.FIELDS},references,citations,tldr"}
        data = await self._get_with_retry(f"/paper/{paper_id}", params)
        if not data:
            return None
        parsed = self._parse_results([data])
        if not parsed:
            return None
        paper = parsed[0]
        paper["references"] = data.get("references") or []
        paper["citations"] = data.get("citations") or []
        return paper


_ss_client: Optional[SemanticScholarClient] = None

def get_ss_client() -> SemanticScholarClient:
    global _ss_client
    if _ss_client is None:
        _ss_client = SemanticScholarClient()
    return _ss_client
