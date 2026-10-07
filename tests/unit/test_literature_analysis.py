"""Unit tests for the literature matrix and the conflict/gap analyzer (no LLM)."""

import json

from src.analysis.literature_analysis import (
    LiteratureAnalyzer,
    build_matrix,
    matrix_to_csv,
)

ABSTRACT_A = (
    "We show that federated averaging achieves 95.2% accuracy on FEMNIST with 3500 clients, "
    "outperforming all baselines."
)
ABSTRACT_B = (
    "Our experiments demonstrate that federated averaging collapses to 61.4% accuracy on "
    "heterogeneous FEMNIST partitions, far below centralized training."
)


def _enriched(title: str, abstract: str, score: int, results: list[dict], limitations: list[str]) -> dict:
    return {
        "title": title,
        "abstract": abstract,
        "url": f"https://example.org/{title}",
        "year": 2024,
        "source": "arxiv",
        "sources": ["arxiv", "openalex"],
        "enrichment": {
            "research_problem": "联邦学习的异构性问题",
            "methods": [{"name": "FedAvg", "baseline": "SGD"}],
            "datasets": [{"name": "FEMNIST", "sample_size": "3500"}],
            "results": results,
            "limitations": limitations,
            "relevance": {"score": score, "reason": "高度相关"},
            "evidence": {"results": [{"quote": "95.2% accuracy", "page": None, "source": "abstract"}]},
        },
    }


def test_build_matrix_renders_enrichment():
    papers = [
        _enriched("Paper A", ABSTRACT_A, 6, [{"metric": "accuracy", "value": "95.2", "unit": "%"}], ["small scale"]),
        {"title": "Unenriched", "enrichment": None},
    ]
    rows = build_matrix(papers)
    assert rows[0]["index"] == 1
    assert rows[0]["relevance"]["score"] == 6
    assert rows[0]["sources"] == ["arxiv", "openalex"]
    assert rows[0]["evidence"]["results"][0]["quote"] == "95.2% accuracy"
    # Papers without enrichment degrade to neutral rows instead of crashing.
    assert rows[1]["relevance"]["score"] == 0
    assert rows[1]["research_problem"] == ""


def test_matrix_to_csv_has_bom_and_chinese_header():
    rows = build_matrix([_enriched("Paper A", ABSTRACT_A, 6, [{"metric": "accuracy", "value": "95.2", "unit": "%"}], ["small scale"])])
    csv_text = matrix_to_csv(rows)
    assert csv_text.startswith("\ufeff")
    header = csv_text.splitlines()[0]
    assert "研究问题" in header and "相关度" in header
    body = csv_text.splitlines()[1]
    assert "Paper A" in body
    assert "accuracy: 95.2%" in body
    assert "FedAvg (基线: SGD)" in body
    assert "FEMNIST (n=3500)" in body
    assert "arxiv/openalex" in body


async def test_detect_conflicts_maps_indexes_and_verifies_evidence(monkeypatch):
    papers = [
        _enriched("Paper A", ABSTRACT_A, 6, [{"metric": "accuracy", "value": "95.2", "unit": "%"}], []),
        _enriched("Paper B", ABSTRACT_B, 5, [{"metric": "accuracy", "value": "61.4", "unit": "%"}], []),
    ]
    analyzer = LiteratureAnalyzer()

    async def fake(prompt, system_prompt="", json_mode=False, enable_thinking=True):
        return json.dumps({"conflicts": [
            {
                "claim_a": "联邦平均在异构数据上表现良好",
                "source_a": 1,
                "claim_b": "联邦平均在异构数据上崩溃",
                "source_b": 2,
                "suggested_resolution": "数据异构程度不同",
                "evidence_a": "achieves 95.2% accuracy on FEMNIST",
                "evidence_b": "this quote was invented by the model",
            },
            {"claim_a": "x", "source_a": 1, "claim_b": "y", "source_b": 9, "suggested_resolution": ""},
            {"claim_a": "z", "source_a": 1, "claim_b": "w", "source_b": 1, "suggested_resolution": ""},
        ]})

    monkeypatch.setattr(analyzer, "_call_llm", fake)
    conflicts = await analyzer.detect_conflicts(papers, "federated learning")

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["source_a"] == "[1] Paper A"
    assert conflict["source_b"] == "[2] Paper B"
    assert conflict["source_a_url"] == "https://example.org/Paper A"
    assert conflict["evidence_a_verified"] is True
    assert conflict["evidence_b_verified"] is False


async def test_detect_conflicts_short_circuits_without_results():
    analyzer = LiteratureAnalyzer()
    papers = [{"title": "plain", "abstract": "x"}]
    assert await analyzer.detect_conflicts(papers, "topic") == []


async def test_identify_gaps_uses_sparse_entities_and_limitations(monkeypatch):
    papers = [_enriched("Paper A", ABSTRACT_A, 6, [], ["limited to 3500 clients"])]
    analyzer = LiteratureAnalyzer()
    captured = {}

    async def fake(prompt, system_prompt="", json_mode=False, enable_thinking=True):
        captured["prompt"] = prompt
        return json.dumps({"gaps": [{
            "gap": "超大规模联邦场景缺乏验证",
            "reason": "现有工作局限在数千客户端",
            "related_entities": ["FEMNIST"],
            "from_limitations": ["limited to 3500 clients"],
        }]})

    monkeypatch.setattr(analyzer, "_call_llm", fake)
    gaps = await analyzer.identify_gaps(
        papers, [{"name": "FEMNIST", "type": "Dataset", "degree": 1}], "federated learning"
    )

    assert "FEMNIST" in captured["prompt"]
    assert "limited to 3500 clients" in captured["prompt"]
    assert gaps[0]["gap"] == "超大规模联邦场景缺乏验证"
    assert gaps[0]["related_entities"] == ["FEMNIST"]


async def test_identify_gaps_returns_empty_without_material():
    analyzer = LiteratureAnalyzer()
    assert await analyzer.identify_gaps([{"title": "x"}], [], "topic") == []
