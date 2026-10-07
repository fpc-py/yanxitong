"""Unit tests for enrichment evidence validation and paper enrichment flow."""

import json

from src.agents.retriever.enricher import (
    PaperEnricher,
    build_enrichment,
    empty_enrichment,
    flatten_enrichment,
)
from src.analysis.evidence import verify_quote

ABSTRACT = (
    "We introduce FedProx, a federated optimization method that handles heterogeneity. "
    "Experiments on the FEMNIST dataset with 3500 clients show 95.2% accuracy, "
    "outperforming FedAvg by 4.1%. The main limitation is sensitivity to the proximal term."
)
SOURCE = f"Federated Learning with Heterogeneity. {ABSTRACT}"


def _raw(**overrides) -> dict:
    base = {
        "research_problem": {"text": "如何在异构数据下做联邦优化", "quote": "handles heterogeneity"},
        "methods": [{"name": "FedProx", "baseline": "FedAvg", "quote": "a federated optimization method that handles heterogeneity"}],
        "datasets": [{"name": "FEMNIST", "sample_size": "3500 clients", "quote": "FEMNIST dataset with 3500 clients"}],
        "results": [{"metric": "accuracy", "value": "95.2", "unit": "%", "quote": "95.2% accuracy"}],
        "limitations": [{"text": "对近端项敏感", "quote": "sensitivity to the proximal term"}],
        "relevance": {"score": 5, "reason": "同一问题"},
    }
    base.update(overrides)
    return base


def test_verify_quote_tolerates_punctuation_and_case():
    assert verify_quote("Federated Optimization, METHOD", SOURCE)
    assert verify_quote("95.2% accuracy", SOURCE)
    assert not verify_quote("This sentence is nowhere in the source", SOURCE)
    assert not verify_quote("accuracy", SOURCE)  # too short


def test_build_enrichment_keeps_verified_fields():
    enrichment = build_enrichment(_raw(), SOURCE)
    assert enrichment["research_problem"] == "如何在异构数据下做联邦优化"
    assert enrichment["methods"] == [{"name": "FedProx", "baseline": "FedAvg"}]
    assert enrichment["datasets"] == [{"name": "FEMNIST", "sample_size": "3500 clients"}]
    assert enrichment["results"] == [{"metric": "accuracy", "value": "95.2", "unit": "%"}]
    assert enrichment["limitations"] == ["对近端项敏感"]
    assert enrichment["relevance"]["score"] == 5
    assert enrichment["dropped_fields"] == []
    assert enrichment["evidence"]["results"][0]["quote"] == "95.2% accuracy"
    assert enrichment["evidence"]["results"][0]["source"] == "abstract"
    assert enrichment["evidence"]["results"][0]["page"] is None


def test_build_enrichment_drops_hallucinated_quote():
    raw = _raw(
        research_problem={"text": "编造的问题", "quote": "a sentence the model made up entirely"},
        results=[{"metric": "accuracy", "value": "99", "unit": "%", "quote": "99% accuracy in our new experiments"}],
    )
    enrichment = build_enrichment(raw, SOURCE)
    assert enrichment["research_problem"] == ""
    assert enrichment["results"] == []
    assert "research_problem" in enrichment["dropped_fields"]
    assert "results" in enrichment["dropped_fields"]
    # Untouched valid fields survive alongside the dropped ones.
    assert enrichment["methods"]


def test_build_enrichment_rejects_out_of_range_relevance():
    enrichment = build_enrichment(_raw(relevance={"score": 9, "reason": "x"}), SOURCE)
    assert enrichment["relevance"]["score"] == 0
    assert "relevance" in enrichment["dropped_fields"]
    # String scores from JSON-ish payloads are coerced when valid.
    enrichment = build_enrichment(_raw(relevance={"score": "4", "reason": "x"}), SOURCE)
    assert enrichment["relevance"]["score"] == 4


def test_flatten_enrichment_projects_legacy_fields():
    enrichment = build_enrichment(_raw(), SOURCE)
    flat = flatten_enrichment(enrichment)
    assert flat["key_findings"] == ["accuracy: 95.2%"]
    assert flat["methods"] == ["FedProx (baseline: FedAvg)"]
    assert flat["datasets"] == ["FEMNIST (n=3500 clients)"]
    assert flat["metrics"] == {"accuracy": "95.2%"}


def test_empty_enrichment_is_neutral():
    enrichment = empty_enrichment()
    assert enrichment["relevance"]["score"] == 0
    assert flatten_enrichment(enrichment) == {}


def _paper(title: str, abstract: str) -> dict:
    return {"title": title, "abstract": abstract, "source": "arxiv", "citations_count": 0}


async def test_enrich_papers_sorts_by_relevance(monkeypatch):
    paper_a = _paper("Paper A", ABSTRACT)
    paper_b = _paper("Paper B", ABSTRACT)
    enricher = PaperEnricher()

    async def fake(prompt, system_prompt="", json_mode=False, enable_thinking=True):
        score = 6 if "Paper A" in prompt else 2
        return json.dumps(_raw(relevance={"score": score, "reason": "r"}))

    monkeypatch.setattr(enricher, "_call_llm", fake)
    result = await enricher.enrich_papers([paper_b, paper_a], "federated learning")

    assert [p["title"] for p in result] == ["Paper A", "Paper B"]
    assert result[0]["enrichment"]["relevance"]["score"] == 6
    assert result[0]["key_findings"] == ["accuracy: 95.2%"]


async def test_enrich_papers_skips_short_source_without_llm(monkeypatch):
    calls = []
    paper_short = _paper("Tiny", "too short")
    paper_ok = _paper("Paper A", ABSTRACT)
    enricher = PaperEnricher()

    async def fake(prompt, system_prompt="", json_mode=False, enable_thinking=True):
        calls.append(prompt)
        return json.dumps(_raw())

    monkeypatch.setattr(enricher, "_call_llm", fake)
    result = await enricher.enrich_papers([paper_short, paper_ok], "topic")

    assert len(calls) == 1
    assert result[0]["title"] == "Paper A"
    assert result[-1]["title"] == "Tiny"
    assert result[-1]["enrichment"]["relevance"]["score"] == 0


async def test_enrich_papers_survives_llm_failure(monkeypatch):
    paper = _paper("Paper A", ABSTRACT)
    enricher = PaperEnricher()

    async def failing(prompt, system_prompt="", json_mode=False, enable_thinking=True):
        raise RuntimeError("llm down")

    monkeypatch.setattr(enricher, "_call_llm", failing)
    result = await enricher.enrich_papers([paper], "topic")

    assert result[0]["enrichment"]["relevance"]["score"] == 0
