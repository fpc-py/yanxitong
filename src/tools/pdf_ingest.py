"""PDF ingestion: parse uploaded bytes into page/char-anchored blocks.

Chain: PyMuPDF (primary) -> OCR (scanned pages / poor text layer). Every
block keeps ``page`` and ``char_start/char_end`` so downstream chunks can
cite the exact span they came from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

try:  # PyMuPDF >= 1.24 exposes the ``pymupdf`` name; ``fitz`` is deprecated.
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF releases
    import fitz

from src.core.config import get_settings
from src.tools import ocr
from src.tools.pdf_parser import _looks_like_heading

logger = logging.getLogger(__name__)

_MOJIBAKE_BUDGET = 0.3


@dataclass
class ParsedBlock:
    text: str
    page: int
    char_start: int
    char_end: int
    section_title: str = ""


@dataclass
class ParsedDoc:
    full_text: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    pages: int = 0
    parser_used: str = "pymupdf"
    ocr_used: bool = False
    ocr_pages: int = 0
    chars_per_page: float = 0.0


def _mojibake_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = sum(1 for ch in text if ch == "\ufffd" or 0xE000 <= ord(ch) <= 0xF8FF)
    bad += text.count("(cid:") * 9  # 每个 cid 记号约 9 字符的乱码成本
    return bad / max(len(text), 1)


def _finish(doc: ParsedDoc) -> ParsedDoc:
    doc.chars_per_page = (len(doc.full_text) / doc.pages) if doc.pages else 0.0
    return doc


def _extract_pymupdf(pdf_bytes: bytes) -> ParsedDoc:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        blocks: list[ParsedBlock] = []
        pieces: list[str] = []
        cursor = 0
        section = ""
        for page_index, page in enumerate(doc):
            page_no = page_index + 1
            for raw in page.get_text("blocks"):
                if len(raw) < 7 or raw[6] != 0:
                    continue  # 图片等非文本块
                text = (raw[4] or "").strip()
                if not text:
                    continue
                first_line = text.splitlines()[0]
                if _looks_like_heading(first_line):
                    section = first_line.strip().rstrip(":").strip()
                if cursor:
                    pieces.append("\n")
                    cursor += 1
                start = cursor
                pieces.append(text)
                cursor += len(text)
                blocks.append(ParsedBlock(text=text, page=page_no, char_start=start, char_end=cursor, section_title=section))
        return ParsedDoc(
            full_text="".join(pieces),
            blocks=blocks,
            pages=doc.page_count,
            parser_used="pymupdf",
        )
    finally:
        doc.close()


async def _ocr_fallback(pdf_bytes: bytes, page_count: int, max_pages: int) -> ParsedDoc:
    """Render up to ``max_pages`` pages and OCR them; returns a ParsedDoc."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        limit = min(doc.page_count, max_pages)
        blocks: list[ParsedBlock] = []
        pieces: list[str] = []
        cursor = 0
        for page_index in range(limit):
            png = doc[page_index].get_pixmap(dpi=ocr.OCR_DPI).tobytes("png")
            text = (await ocr.ocr_page_png(png)).strip()
            if not text:
                continue
            if cursor:
                pieces.append("\n")
                cursor += 1
            start = cursor
            pieces.append(text)
            cursor += len(text)
            blocks.append(ParsedBlock(text=text, page=page_index + 1, char_start=start, char_end=cursor))
        return ParsedDoc(
            full_text="".join(pieces),
            blocks=blocks,
            pages=page_count or limit,
            parser_used="ocr",
            ocr_used=True,
            ocr_pages=limit,
        )
    finally:
        doc.close()


def _is_low_quality(doc: ParsedDoc, min_chars_per_page: int) -> bool:
    if doc.chars_per_page < min_chars_per_page:
        return True
    return _mojibake_ratio(doc.full_text) > _MOJIBAKE_BUDGET


async def parse_pdf(pdf_bytes: bytes, filename: str = "document.pdf") -> ParsedDoc:
    """Run the two-tier chain and return the best parse available.

    PyMuPDF always runs first; OCR only when the text layer is sparse or
    garbled and rapidocr is present.
    """
    settings = get_settings().pdf
    doc = _finish(_extract_pymupdf(pdf_bytes))

    if _is_low_quality(doc, settings.min_chars_per_page) and settings.ocr_enabled and ocr.ocr_available():
        logger.info("OCR fallback engaged (chars_per_page=%.1f)", doc.chars_per_page)
        ocr_doc = _finish(await _ocr_fallback(pdf_bytes, doc.pages, settings.ocr_max_pages))
        if len(ocr_doc.full_text) > len(doc.full_text):
            doc = ocr_doc

    return doc


def _split_block(block: ParsedBlock, para: int, size: int, overlap: int) -> list[dict]:
    text = block.text
    if not text:
        return []
    overlap = min(overlap, size // 2)
    step = max(size - overlap, 1)
    out: list[dict] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        piece = text[start:end]
        if piece.strip():
            out.append({
                "text": piece,
                "page": block.page,
                "para": para,
                "section_title": block.section_title,
                "char_start": block.char_start + start,
                "char_end": block.char_start + end,
            })
        if end >= len(text):
            break
        start += step
    return out


def chunk_parsed_doc(doc: ParsedDoc, chunk_size: int | None = None, overlap: int | None = None) -> list[dict]:
    """Cut parsed blocks into overlapping chunks carrying span metadata."""
    settings = get_settings().kb
    size = chunk_size or settings.chunk_size
    ov = settings.chunk_overlap if overlap is None else overlap
    chunks: list[dict] = []
    for para, block in enumerate(doc.blocks):
        for piece in _split_block(block, para, size, ov):
            piece["chunk_index"] = len(chunks)
            chunks.append(piece)
    return chunks
