"""Unit tests for the two-tier PDF ingestion chain (PyMuPDF -> OCR)."""

import pymupdf
import pytest

from src.tools import ocr as ocr_mod
from src.tools import pdf_ingest
from src.tools.pdf_ingest import ParsedBlock, ParsedDoc, chunk_parsed_doc, parse_pdf

GOOD_TEXT = (
    "Federated learning trains models across decentralized clients. "
    "We propose a proximal term that stabilizes updates under heterogeneity. "
    "Results on FEMNIST with 3500 clients show a 4.1% accuracy gain. "
) * 2


def _make_pdf(pages: list[str]) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(72, 72, 540, 700), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def _no_ocr(monkeypatch):
    monkeypatch.setattr(pdf_ingest.ocr, "ocr_available", lambda: False)


async def test_text_pdf_passes_through_pymupdf(monkeypatch):
    _no_ocr(monkeypatch)
    pdf = _make_pdf(["Abstract\n" + GOOD_TEXT, GOOD_TEXT])
    doc = await parse_pdf(pdf, "paper.pdf")
    assert doc.parser_used == "pymupdf"
    assert doc.pages == 2
    assert doc.ocr_used is False
    assert doc.chars_per_page >= 100
    assert 0 < len(doc.blocks)
    for block in doc.blocks:
        assert block.char_start < block.char_end
        assert doc.full_text[block.char_start:block.char_end] == block.text


async def test_mojibake_triggers_ocr(monkeypatch):
    pdf = _make_pdf([""])
    garbled = "(cid:123) " * 200

    def _fake_extract(pdf_bytes):
        return ParsedDoc(full_text=garbled, blocks=[], pages=1, chars_per_page=len(garbled))

    ocr_text = "修复后的干净文字 " * 300

    async def _fake_ocr(png_bytes):
        return ocr_text

    monkeypatch.setattr(pdf_ingest, "_extract_pymupdf", _fake_extract)
    monkeypatch.setattr(pdf_ingest.ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(pdf_ingest.ocr, "ocr_page_png", _fake_ocr)
    doc = await parse_pdf(pdf, "broken.pdf")
    assert doc.parser_used == "ocr"
    assert doc.ocr_used is True
    assert ocr_text.strip() in doc.full_text


async def test_low_quality_without_ocr_degrades_gracefully(monkeypatch):
    pdf = _make_pdf([""])
    _no_ocr(monkeypatch)
    doc = await parse_pdf(pdf, "scan.pdf")
    assert doc.parser_used == "pymupdf"
    assert doc.ocr_used is False
    assert doc.full_text == ""


async def test_ocr_fallback_used_when_available(monkeypatch):
    pdf = _make_pdf([""])
    ocr_text = "扫描页文字内容 " * 20

    async def _fake_ocr(png_bytes):
        assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        return ocr_text

    monkeypatch.setattr(pdf_ingest.ocr, "ocr_available", lambda: True)
    monkeypatch.setattr(pdf_ingest.ocr, "ocr_page_png", _fake_ocr)
    doc = await parse_pdf(pdf, "scan.pdf")
    assert doc.parser_used == "ocr"
    assert doc.ocr_used is True
    assert doc.ocr_pages == 1
    assert ocr_text.strip() in doc.full_text


async def test_ocr_guard_returns_empty_without_dependency(monkeypatch):
    monkeypatch.setattr(ocr_mod, "OCR_AVAILABLE", False)
    assert ocr_mod.ocr_available() is False
    assert await ocr_mod.ocr_page_png(b"whatever") == ""


def test_mojibake_and_quality_heuristics():
    assert pdf_ingest._mojibake_ratio("(cid:123)(cid:456)") > 0.3
    good = ParsedDoc(full_text="a" * 500, pages=2, chars_per_page=250)
    assert pdf_ingest._is_low_quality(good, 100) is False
    sparse = ParsedDoc(full_text="a" * 50, pages=2, chars_per_page=25)
    assert pdf_ingest._is_low_quality(sparse, 100) is True


def test_chunk_parsed_doc_overlap_and_span():
    text = "字" * 2000
    block = ParsedBlock(text=text, page=4, char_start=100, char_end=2100, section_title="Method")
    chunks = chunk_parsed_doc(ParsedDoc(full_text=text, blocks=[block], pages=6), chunk_size=800, overlap=100)
    assert len(chunks) == 3
    assert chunks[0]["text"][-100:] == chunks[1]["text"][:100]
    assert chunks[1]["text"][-100:] == chunks[2]["text"][:100]
    assert [c["chunk_index"] for c in chunks] == [0, 1, 2]
    assert all(c["page"] == 4 and c["section_title"] == "Method" and c["para"] == 0 for c in chunks)
    assert chunks[0]["char_start"] == 100
    assert chunks[0]["char_end"] == 900
    assert chunks[1]["char_start"] == 800
