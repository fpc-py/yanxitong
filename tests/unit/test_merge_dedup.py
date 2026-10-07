"""Unit tests for canonical paper normalization + cross-source merge/dedup."""

from src.tools.paper_schema import (
    arxiv_key,
    extract_arxiv_id,
    merge_dedup,
    normalize_doi,
    normalize_paper,
    normalize_title,
)


def _paper(**overrides) -> dict:
    base = {
        "title": "Some Paper",
        "authors": ["A"],
        "year": 2024,
        "abstract": "abstract",
        "url": "",
        "source": "arxiv",
        "doi": "",
        "arxiv_id": "",
    }
    base.update(overrides)
    return base


def test_normalize_doi_variants():
    assert normalize_doi("https://doi.org/10.1234/ABC") == "10.1234/abc"
    assert normalize_doi("doi:10.1234/abc") == "10.1234/abc"
    assert normalize_doi("") == ""
    assert normalize_doi("10.5555/x") == "10.5555/x"


def test_normalize_title_strips_punctuation_and_case():
    assert normalize_title("Attention Is All You Need!") == "attention is all you need"
    assert normalize_title("BERT:  Pre-training") == "bert pre training"
    assert normalize_title("") == ""


def test_arxiv_key_strips_version():
    assert arxiv_key("2301.00001v2") == "2301.00001"
    assert arxiv_key("2301.00001") == "2301.00001"
    assert arxiv_key("") == ""


def test_extract_arxiv_id_from_url():
    assert extract_arxiv_id({"url": "https://arxiv.org/abs/2301.00001v1"}) == "2301.00001"
    assert extract_arxiv_id({"arxiv_id": "2301.00001"}) == "2301.00001"
    assert extract_arxiv_id({"url": "https://example.org/x"}) == ""


def test_normalize_paper_fills_defaults():
    p = normalize_paper({"title": "T", "doi": "https://doi.org/10.1/x"})
    assert p["authors"] == []
    assert p["metrics"] == {}
    assert p["doi"] == "10.1/x"
    assert p["text"] == "T."


def test_merge_dedup_by_doi_across_sources():
    arxiv = _paper(title="On Widgets", doi="https://doi.org/10.1/w", source="arxiv")
    s2 = _paper(title="On Widgets", doi="10.1/w", source="semantic_scholar", abstract="a much longer abstract about widgets")
    merged = merge_dedup([arxiv, s2])
    assert len(merged) == 1
    assert merged[0]["sources"] == ["arxiv", "semantic_scholar"]
    assert merged[0]["abstract"] == "a much longer abstract about widgets"


def test_merge_dedup_by_arxiv_id_across_versions():
    a = _paper(title="v1 title", arxiv_id="2301.00001v1", source="arxiv")
    b = _paper(title="v2 title", arxiv_id="2301.00001v2", source="openalex")
    merged = merge_dedup([a, b])
    assert len(merged) == 1


def test_merge_dedup_by_normalized_title():
    a = _paper(title="Attention Is All You Need!", source="arxiv", doi="10.1/a")
    b = _paper(title="attention is all you need", source="openalex", doi="10.2/b")
    merged = merge_dedup([a, b])
    assert len(merged) == 1
    assert merged[0]["title"] == "Attention Is All You Need!"


def test_merge_dedup_keeps_distinct_papers():
    a = _paper(title="Paper A", doi="10.1/a")
    b = _paper(title="Paper B", doi="10.1/b")
    assert len(merge_dedup([a, b])) == 2


def test_merge_dedup_keeps_max_citations():
    a = _paper(title="X", doi="10.1/x", citations_count=3)
    b = _paper(title="X", doi="10.1/x", citations_count=99, source="openalex")
    merged = merge_dedup([a, b])
    assert merged[0]["citations_count"] == 99
