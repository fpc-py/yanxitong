"""OpenAlex API client with polite-pool etiquette.

Follows the OpenAlex recommended practice:

* Identify the caller via a ``mailto`` query parameter (polite pool) and an
  optional ``api_key`` for higher daily quotas.
* Light client-side pacing so bursts of agent calls stay under the ~10 rps
  polite-pool ceiling.
* Exponential-backoff retries for transient network/5xx failures; 403/429
  fail fast so a single throttled source degrades instead of stalling the
  whole multi-source search.

Abstracts are stored by OpenAlex as an inverted index (``{word: [positions]}``)
and are reconstructed into plain text here.
"""

import asyncio
import logging
import random
from typing import Optional

import httpx

from src.core.config import get_settings

logger = logging.getLogger(__name__)

OPENALEX_API_BASE = "https://api.openalex.org/works"
USER_AGENT = "yanxitong/2.0 research-literature agent"

# Trim the (large) work payload to the fields the canonical paper schema uses.
_SELECT_FIELDS = ",".join([
    "id", "doi", "title", "display_name", "publication_year", "cited_by_count",
    "type", "authorships", "primary_location", "open_access",
    "abstract_inverted_index", "ids",
])


class OpenAlexClient:
    """Async OpenAlex API client respecting polite-pool etiquette."""

    MAX_RETRIES: int = 3
    """Number of attempts for a single API call before giving up."""

    BACKOFF_BASE: float = 2.0
    """Base backoff in seconds; doubled on each retry, plus jitter."""

    MIN_INTERVAL: float = 0.15
    """Client-side pacing (~6 rps) to stay under the polite-pool 10 rps limit."""

    def __init__(self):
        self.settings = get_settings()
        self._last_request: float = 0.0
        self._lock = asyncio.Lock()

    async def _rate_limit(self):
        """Sleep until at least ``MIN_INTERVAL`` seconds since the last request."""
        async with self._lock:
            now = asyncio.get_running_loop().time()
            elapsed = now - self._last_request
            if elapsed < self.MIN_INTERVAL:
                await asyncio.sleep(self.MIN_INTERVAL - elapsed)
            self._last_request = asyncio.get_running_loop().time()

    def _polite_params(self, params: dict) -> dict:
        """Attach polite-pool identity parameters when configured."""
        retriever = self.settings.retriever
        if retriever.openalex_mailto:
            params["mailto"] = retriever.openalex_mailto
        if retriever.openalex_api_key:
            params["api_key"] = retriever.openalex_api_key
        return params

    async def _get_with_retry(self, params: dict) -> Optional[dict]:
        """GET the works endpoint as JSON with pacing and retries.

        Returns the decoded payload, or ``None`` after all retries are
        exhausted (or on a non-retryable/throttled 4xx error).
        """
        last_error: Optional[BaseException] = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            await self._rate_limit()
            try:
                async with httpx.AsyncClient(
                    timeout=30.0, headers={"User-Agent": USER_AGENT}
                ) as client:
                    response = await client.get(OPENALEX_API_BASE, params=params)
                    if response.status_code in (403, 429):
                        # Quota/permission problems will not fix themselves on
                        # retry; degrade this source instead of stalling.
                        logger.warning(
                            "OpenAlex %d (throttled/forbidden); skipping this source. "
                            "Set an OpenAlex API key for a higher quota.",
                            response.status_code,
                        )
                        return None
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code
                if 400 <= status < 500:
                    logger.error(f"OpenAlex client error {status}, not retrying: {e}")
                    return None
            except (httpx.HTTPError, ValueError) as e:
                last_error = e

            backoff = self.BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            logger.warning(
                "OpenAlex request failed (attempt %d/%d): %s; retrying in %.1fs",
                attempt, self.MAX_RETRIES, last_error, backoff,
            )
            await asyncio.sleep(backoff)

        logger.error(f"OpenAlex API error after {self.MAX_RETRIES} attempts: {last_error}")
        return None

    async def search(self, query: str, max_results: Optional[int] = None) -> list[dict]:
        """Search OpenAlex works matching ``query`` via full-text search."""
        max_results = max_results or self.settings.retriever.openalex_max_results
        params = self._polite_params({
            "search": query,
            "per-page": min(max_results, 200),
            "select": _SELECT_FIELDS,
        })

        data = await self._get_with_retry(params)
        if data is None:
            return []

        return self._parse_works(data.get("results", []))

    def _parse_works(self, raw_works: list[dict]) -> list[dict]:
        """Map raw OpenAlex works onto the canonical paper dict shape."""
        papers = []
        for w in raw_works:
            ids = w.get("ids") or {}
            doi = (w.get("doi") or ids.get("doi") or "").strip()
            openalex_id = (ids.get("openalex") or w.get("id") or "").strip()

            authors = [
                (a.get("author") or {}).get("display_name", "")
                for a in (w.get("authorships") or [])
                if (a.get("author") or {}).get("display_name")
            ]

            abstract = self._reconstruct_abstract(w.get("abstract_inverted_index"))

            primary = w.get("primary_location") or {}
            source_info = primary.get("source") or {}
            venue = source_info.get("display_name", "")
            landing_url = primary.get("landing_page_url") or ""
            open_access = w.get("open_access") or {}
            url = landing_url or open_access.get("oa_url") or doi or openalex_id

            title = w.get("title") or w.get("display_name") or "Untitled"

            papers.append({
                "title": title,
                "authors": authors,
                "year": w.get("publication_year") or 0,
                "abstract": abstract,
                "url": url,
                "source": "openalex",
                "doi": doi,
                "openalex_id": openalex_id,
                "citations_count": w.get("cited_by_count", 0),
                "is_open_access": bool(open_access.get("is_oa", False)),
                "oa_url": open_access.get("oa_url") or "",
                "venue": venue,
                "work_type": w.get("type", ""),
                "key_findings": [],
                "methods": [],
                "datasets": [],
                "metrics": {},
                "text": f"{title}. {abstract}",
            })
        return papers

    async def get_work_by_doi(self, doi: str) -> Optional[dict]:
        """Look up a single work by DOI (bare or URL form)."""
        clean = doi.strip()
        for prefix in ("https://doi.org/", "http://doi.org/"):
            if clean.lower().startswith(prefix):
                clean = clean[len(prefix):]
        params = self._polite_params({"filter": f"doi:{clean}", "per-page": 1})
        data = await self._get_with_retry(params)
        if data is None:
            return None
        results = self._parse_works(data.get("results", []))
        return results[0] if results else None

    @staticmethod
    def _reconstruct_abstract(inverted_index: Optional[dict]) -> str:
        """Rebuild plain text from OpenAlex's ``{word: [positions]}`` format."""
        if not inverted_index or not isinstance(inverted_index, dict):
            return ""
        positioned: list[tuple[int, str]] = []
        for word, positions in inverted_index.items():
            if not isinstance(positions, list):
                continue
            for pos in positions:
                if isinstance(pos, int):
                    positioned.append((pos, word))
        positioned.sort(key=lambda item: item[0])
        return " ".join(word for _, word in positioned)


_openalex_client: Optional[OpenAlexClient] = None

def get_openalex_client() -> OpenAlexClient:
    global _openalex_client
    if _openalex_client is None:
        _openalex_client = OpenAlexClient()
    return _openalex_client
