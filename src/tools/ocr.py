"""OCR fallback for scanned PDFs, backed by rapidocr-onnxruntime (CPU only).

The import is guarded: when the optional dependency is missing, OCR is simply
unavailable and ingestion reports ``ocr_used=False`` instead of failing.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

try:
    from rapidocr_onnxruntime import RapidOCR

    OCR_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    RapidOCR = None
    OCR_AVAILABLE = False

OCR_DPI = 200
_engine = None


def ocr_available() -> bool:
    return OCR_AVAILABLE


def _get_engine():
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


def _ocr_image(image: bytes) -> str:
    result, _elapsed = _get_engine()(image)
    if not result:
        return ""
    return "\n".join(line[1] for line in result if len(line) > 1 and line[1])


async def ocr_page_png(png_bytes: bytes) -> str:
    """OCR one rendered page (PNG bytes) off the event loop."""
    if not OCR_AVAILABLE:
        return ""
    try:
        return await asyncio.to_thread(_ocr_image, png_bytes)
    except Exception as exc:
        logger.warning("OCR failed for a page: %s", exc)
        return ""
