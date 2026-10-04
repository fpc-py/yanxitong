"""Arxiv API client with rate limiting.

Follows the arXiv Open API etiquette:

* At most one request every 3 seconds (enforced with an ``asyncio.Lock`` so
  concurrent callers are serialized).
* A descriptive ``User-Agent`` header on every request.
* Exponential-backoff retries for transient network/5xx failures.
"""

import asyncio
import logging
import random
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import httpx

from src.core.config import get_settings

logger = logging.getLogger(__name__)

ARXIV_API_BASE = "https://export.arxiv.org/api/query"
ARXIV_PDF_BASE = "https://arxiv.org/pdf/"
USER_AGENT = "yanxitong/2.0 research-literature agent"

# Recognised arXiv search-field prefixes. When a query already carries one of
# these (e.g. ``ti:transformers``) it is passed through unchanged.
_FIELD_PREFIXES = ("ti:", "au:", "abs:", "cat:", "all:", "co:", "jr:", "rn:", "id:")


class ArxivClient:
    """Async Arxiv API client respecting rate limits."""

    MAX_RETRIES: int = 3
    """Number of attempts for a single API call before giving up."""

    BACKOFF_BASE: float = 2.0
    """Base backoff in seconds; doubled on each retry, plus jitter."""

    def __init__(self):
        self.settings = get_settings()
        self._last_request: float = 0.0
        self._min_interval: float = 3.0  # Required by arxiv API policy
        self._lock = asyncio.Lock()

    async def _rate_limit(self):
        """Sleep until at least ``_min_interval`` seconds since the last request.

        Guarded by an ``asyncio.Lock`` so concurrent coroutines cannot both
        pass the check and issue back-to-back requests.
        """
        async with self._lock:
            now = asyncio.get_running_loop().time()
            elapsed = now - self._last_request
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request = asyncio.get_running_loop().time()

    async def _get_with_retry(self, url: str) -> Optional[str]:
        """GET a URL with rate limiting and exponential-backoff retry.

        Returns the response body, or ``None`` after all retries are
        exhausted (or on a non-retryable 4xx error).
        """
        last_error: Optional[BaseException] = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            await self._rate_limit()
            try:
                async with httpx.AsyncClient(
                    timeout=30.0, headers={"User-Agent": USER_AGENT}
                ) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.text
            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code
                # Client errors (except 429 Too Many Requests) will not fix
                # themselves; fail fast instead of hammering the API.
                if 400 <= status < 500 and status != 429:
                    logger.error(f"Arxiv API client error {status}, not retrying: {e}")
                    return None
            except (httpx.HTTPError, httpx.RequestError) as e:
                last_error = e

            backoff = self.BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            logger.warning(
                "Arxiv request failed (attempt %d/%d): %s; retrying in %.1fs",
                attempt, self.MAX_RETRIES, last_error, backoff,
            )
            await asyncio.sleep(backoff)

        logger.error(f"Arxiv API error after {self.MAX_RETRIES} attempts: {last_error}")
        return None

    async def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        start: int = 0,
    ) -> list[dict]:
        """Search arxiv for papers matching query. Returns list of PaperSummary-like dicts."""
        max_results = max_results or self.settings.retriever.arxiv_max_results
        search_query = query if query.startswith(_FIELD_PREFIXES) else f"all:{query}"
        params = {
            "search_query": search_query,
            "start": start,
            "max_results": max_results,
            "sortBy": "relevance",
        }
        url = f"{ARXIV_API_BASE}?{urlencode(params)}"

        xml_text = await self._get_with_retry(url)
        if xml_text is None:
            return []

        return self._parse_response(xml_text)

    def _parse_response(self, xml_text: str) -> list[dict]:
        """Parse arxiv Atom XML response into paper dicts."""
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error(f"Arxiv returned unparsable XML: {e}")
            return []

        papers = []

        for entry in root.findall("atom:entry", ns):
            id_el = entry.find("atom:id", ns)
            id_url = (id_el.text or "").strip() if id_el is not None else ""
            # Error stub entries (malformed query, no results) carry no real id.
            if not id_url or "arxiv.org" not in id_url:
                continue

            title_el = entry.find("atom:title", ns)
            title = " ".join((title_el.text or "").split()) if title_el is not None else "Untitled"

            authors = [
                a.find("atom:name", ns).text or ""
                for a in entry.findall("atom:author", ns)
                if a.find("atom:name", ns) is not None
            ]

            summary_el = entry.find("atom:summary", ns)
            abstract = " ".join((summary_el.text or "").split()) if summary_el is not None else ""

            arxiv_id = id_url.split("/abs/")[-1] if "/abs/" in id_url else ""

            published_el = entry.find("atom:published", ns)
            year = 0
            if published_el is not None and published_el.text:
                try:
                    year = datetime.fromisoformat(published_el.text.replace("Z", "+00:00")).year
                except (ValueError, AttributeError):
                    pass

            doi = ""
            for link in entry.findall("atom:link", ns):
                href = link.get("href", "")
                if "doi.org" in href:
                    doi = href

            papers.append({
                "title": title,
                "authors": authors,
                "year": year,
                "abstract": abstract,
                "url": id_url,
                "source": "arxiv",
                "doi": doi,
                "arxiv_id": arxiv_id,
                "key_findings": [],
                "methods": [],
                "datasets": [],
                "metrics": {},
                "text": f"{title}. {abstract}",
            })

        return papers

    async def get_paper_by_id(self, arxiv_id: str) -> Optional[dict]:
        """Look up a single paper by arxiv ID using the id_list endpoint."""
        params = {"id_list": arxiv_id, "max_results": 1}
        url = f"{ARXIV_API_BASE}?{urlencode(params)}"
        xml_text = await self._get_with_retry(url)
        if xml_text is None:
            return None
        results = self._parse_response(xml_text)
        return results[0] if results else None

    @staticmethod
    def pdf_url(arxiv_id: str) -> str:
        """Build the direct PDF URL for an arxiv ID (e.g. ``2301.00001v2``)."""
        clean = arxiv_id.strip()
        if clean.startswith(("http://", "https://")):
            clean = clean.split("/abs/")[-1].split("/pdf/")[-1]
        return f"{ARXIV_PDF_BASE}{clean}"

    async def download_pdf(self, arxiv_id: str, dest_dir: str = "./data/papers") -> Optional[str]:
        """Download the PDF for an arxiv ID. Returns the local file path or None."""
        # Imported lazily to avoid a hard dependency cycle at module load.
        from src.tools.pdf_parser import download_pdf

        return await download_pdf(self.pdf_url(arxiv_id), dest_dir=dest_dir)


_arxiv_client: Optional[ArxivClient] = None

def get_arxiv_client() -> ArxivClient:
    global _arxiv_client
    if _arxiv_client is None:
        _arxiv_client = ArxivClient()
    return _arxiv_client
