"""Unit tests for the OpenAlex client parsing layer (no network)."""

from src.tools.openalex import OpenAlexClient


def test_reconstruct_abstract_orders_words():
    inverted = {"We": [0], "study": [1], "transformers.": [2], "use": [3]}
    assert OpenAlexClient._reconstruct_abstract(inverted) == "We study transformers. use"


def test_reconstruct_abstract_handles_missing():
    assert OpenAlexClient._reconstruct_abstract(None) == ""
    assert OpenAlexClient._reconstruct_abstract({}) == ""
    assert OpenAlexClient._reconstruct_abstract("not-a-dict") == ""


def test_reconstruct_abstract_skips_bad_positions():
    inverted = {"ok": [1], "bad": "nope", "later": [0, 2]}
    assert OpenAlexClient._reconstruct_abstract(inverted) == "later ok later"


def test_parse_works_field_mapping():
    raw = [{
        "id": "https://openalex.org/W123",
        "doi": "https://doi.org/10.1234/ABC.1",
        "title": "Federated Learning Survey",
        "publication_year": 2023,
        "cited_by_count": 42,
        "type": "review",
        "authorships": [
            {"author": {"display_name": "Alice"}},
            {"author": {"display_name": "Bob"}},
            {"author": {}},
        ],
        "primary_location": {
            "landing_page_url": "https://example.org/paper",
            "source": {"display_name": "Journal of ML"},
        },
        "open_access": {"is_oa": True, "oa_url": "https://example.org/paper.pdf"},
        "abstract_inverted_index": {"Federated": [0], "learning": [1]},
        "ids": {"openalex": "https://openalex.org/W123", "doi": "https://doi.org/10.1234/ABC.1"},
    }]

    papers = OpenAlexClient()._parse_works(raw)
    assert len(papers) == 1
    p = papers[0]
    assert p["title"] == "Federated Learning Survey"
    assert p["authors"] == ["Alice", "Bob"]
    assert p["year"] == 2023
    assert p["doi"] == "https://doi.org/10.1234/ABC.1"
    assert p["url"] == "https://example.org/paper"
    assert p["source"] == "openalex"
    assert p["citations_count"] == 42
    assert p["is_open_access"] is True
    assert p["venue"] == "Journal of ML"
    assert p["abstract"] == "Federated learning"
    assert p["text"] == "Federated Learning Survey. Federated learning"


def test_parse_works_fallbacks_for_sparse_record():
    raw = [{"display_name": "Untitled work", "ids": {"openalex": "https://openalex.org/W9"}}]
    p = OpenAlexClient()._parse_works(raw)[0]
    assert p["title"] == "Untitled work"
    assert p["authors"] == []
    assert p["year"] == 0
    assert p["url"] == "https://openalex.org/W9"
    assert p["abstract"] == ""
