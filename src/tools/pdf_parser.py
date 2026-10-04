"""PDF parser using PyMuPDF for text extraction and metadata retrieval."""

import logging
import re
from pathlib import Path
from typing import Optional

try:  # PyMuPDF >= 1.24 exposes the ``pymupdf`` name; ``fitz`` is deprecated.
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF releases
    import fitz  # PyMuPDF
import httpx

logger = logging.getLogger(__name__)

# Maximum characters kept per extracted section (keeps downstream prompts bounded).
MAX_SECTION_CHARS = 5000

# Heuristic academic section headings. Matched at line start, case-insensitive.
_SECTION_NAMES = (
    "abstract|introduction|related work|background|methodology|methods?"
    "|approach|model|framework|experiments?|experimental results?"
    "|results?( and discussion)?|discussion|evaluation|analysis"
    "|conclusions?|future work|references?|appendix"
)

# e.g. "3. Method", "2 Experiments", "IV. EVALUATION", "Abstract:", "Related Work"
_HEADING_RE = re.compile(
    rf"^\s*(?:(?:\d+|[IVXLCDM]+)[.)]\s+)?(?:{_SECTION_NAMES})\s*:?\s*$",
    re.IGNORECASE,
)

# All-caps lines like "INTRODUCTION" or "EXPERIMENTAL RESULTS".
_ALL_CAPS_RE = re.compile(r"^[A-Z][A-Z\s]{4,60}$")


def _looks_like_heading(line: str) -> bool:
    """Return True if a text line is likely a section heading."""
    stripped = line.strip()
    if not stripped or len(stripped) > 70:
        return False
    if _HEADING_RE.match(stripped):
        return True
    if _ALL_CAPS_RE.match(stripped) and not any(ch.isdigit() for ch in stripped):
        return True
    return False


class PDFParser:
    """Extracts text and metadata from PDF files using PyMuPDF."""

    @staticmethod
    def extract_text(file_path: str) -> str:
        """Extract full text from a local PDF file."""
        try:
            doc = fitz.open(file_path)
        except Exception as e:
            logger.error(f"PDF extraction error for {file_path}: {e}")
            return ""
        try:
            text = ""
            for page in doc:
                text += page.get_text("text") + "\n"
            return text.strip()
        except Exception as e:
            logger.error(f"PDF extraction error for {file_path}: {e}")
            return ""
        finally:
            doc.close()

    @staticmethod
    def extract_sections(file_path: str) -> list[dict]:
        """Extract text split by detected section headings.

        Returns a list of ``{"heading": str, "content": str}`` dicts. Falls
        back to a single "Full Text" section when no headings are detected.
        """
        full_text = PDFParser.extract_text(file_path)
        if not full_text:
            return []

        lines = full_text.splitlines()
        sections: list[dict] = []
        current_heading = "Preamble"
        buffer: list[str] = []

        def flush() -> None:
            content = "\n".join(buffer).strip()
            if content:
                sections.append({"heading": current_heading, "content": content[:MAX_SECTION_CHARS]})
            buffer.clear()

        for line in lines:
            if _looks_like_heading(line):
                flush()
                current_heading = line.strip().rstrip(":").strip()
            else:
                buffer.append(line)
        flush()

        if not sections:
            return [{"heading": "Full Text", "content": full_text[:MAX_SECTION_CHARS]}]
        return sections

    @staticmethod
    def extract_metadata(file_path: str) -> dict:
        """Extract PDF metadata (title, author, subject) plus page count."""
        try:
            doc = fitz.open(file_path)
            meta = doc.metadata or {}
            page_count = doc.page_count
            doc.close()
            return {
                "title": meta.get("title", "") or "",
                "author": meta.get("author", "") or "",
                "subject": meta.get("subject", "") or "",
                "creator": meta.get("creator", "") or "",
                "page_count": page_count,
            }
        except Exception as e:
            logger.error(f"PDF metadata error for {file_path}: {e}")
            return {}

    @staticmethod
    def extract_first_page_snippet(file_path: str, max_chars: int = 3000) -> str:
        """Get first page text as a snippet."""
        try:
            doc = fitz.open(file_path)
        except Exception:
            return ""
        try:
            text = doc[0].get_text("text") if len(doc) > 0 else ""
            return text[:max_chars]
        except Exception:
            return ""
        finally:
            doc.close()

    @classmethod
    def parse(cls, file_path: str) -> dict:
        """Parse a local PDF into a structured dict.

        Returns:
            ``{"full_text": str, "sections": list[dict], "metadata": dict,
            "snippet": str}``. ``full_text`` is empty when the file cannot be
            read, so callers can treat an empty string as failure.
        """
        full_text = cls.extract_text(file_path)
        return {
            "full_text": full_text,
            "sections": cls.extract_sections(file_path) if full_text else [],
            "metadata": cls.extract_metadata(file_path),
            "snippet": cls.extract_first_page_snippet(file_path),
        }

    @classmethod
    async def parse_url(cls, url: str, dest_dir: str = "./data/papers") -> Optional[dict]:
        """Download a PDF from ``url`` and parse it in one step."""
        file_path = await download_pdf(url, dest_dir=dest_dir)
        if file_path is None:
            return None
        return cls.parse(file_path)


async def download_pdf(url: str, dest_dir: str = "./data/papers") -> Optional[str]:
    """Download a PDF from URL to local storage. Returns file path."""
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    filename = url.split("/")[-1].split("?")[0]
    if not filename or not filename.endswith(".pdf"):
        filename = (filename or "paper").removesuffix(".pdf") + ".pdf"
        # arxiv-style ids like "2301.12345v1" become "2301.12345v1.pdf"
    filepath = Path(dest_dir) / filename

    # Reuse already-downloaded files (content-addressed enough by arxiv id).
    if filepath.exists() and filepath.stat().st_size > 0:
        return str(filepath)

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.content
            if not content.startswith(b"%PDF"):
                logger.error(f"Downloaded file is not a PDF: {url}")
                return None
            filepath.write_bytes(content)
        return str(filepath)
    except Exception as e:
        logger.error(f"PDF download error for {url}: {e}")
        # Remove partial/empty files so a later retry is not shadowed.
        try:
            if filepath.exists() and filepath.stat().st_size == 0:
                filepath.unlink()
        except OSError:
            pass
        return None
