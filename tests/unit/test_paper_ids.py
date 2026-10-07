"""Deterministic graph-identity tests: the same paper must always map to the
same node id, regardless of source payload shape or how often it is seen."""

from src.tools.paper_schema import make_paper_id, paper_identity, title_hash


def test_arxiv_id_wins_over_title():
    p = {"title": "Whatever", "arxiv_id": "2404.12345v2", "url": "https://x"}
    assert make_paper_id(p) == "ax:2404.12345"


def test_arxiv_id_extracted_from_url():
    p = {"title": "T", "url": "https://arxiv.org/abs/2401.00001"}
    assert make_paper_id(p) == "ax:2401.00001"


def test_title_hash_fallback_stable_across_sources():
    a = {"title": "Federated Learning: A Survey!", "url": "https://a"}
    b = {"title": "federated   learning a survey", "url": "https://b"}
    assert make_paper_id(a) == make_paper_id(b) == f"th:{title_hash(a['title'])}"


def test_url_fallback_when_no_title():
    p = {"title": "  ", "url": "https://example.com/x"}
    assert make_paper_id(p).startswith("url:")


def test_empty_paper_has_no_id():
    assert make_paper_id({}) == ""


def test_identity_bundle():
    ident = paper_identity({"title": "X", "arxiv_id": "2405.00001"})
    assert ident["paper_id"] == "ax:2405.00001"
    assert ident["arxiv_id"] == "2405.00001"
    assert ident["title_hash"] == title_hash("X")
