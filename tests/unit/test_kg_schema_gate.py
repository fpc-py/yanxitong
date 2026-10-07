"""KG builder tests: deterministic edges from verified enrichment + schema gate.

The LLM and Neo4j are never touched — ``build_deterministic_graph`` and
``KGBuilderAgent._gate_extraction`` are pure functions over their inputs.
"""

from src.agents.kg_builder.agent import (
    KGBuilderAgent,
    build_deterministic_graph,
    entity_prompt_template,
    schema_entity_type,
    schema_relation_type,
)
from src.core.config import get_settings
from src.knowledge.graph_store import make_edge_key, make_entity_id

ENRICHED_PAPER = {
    "title": "FedProx: Proximal Term for Federated Learning",
    "abstract": "We propose FedProx. Experiments on FEMNIST show 4.1% improvement over FedAvg.",
    "year": 2020,
    "arxiv_id": "1812.06127",
    "url": "https://arxiv.org/abs/1812.06127",
    "authors": ["Tian Li"],
    "citations_count": 120,
    "enrichment": {
        "research_problem": "异构联邦学习的收敛稳定性",
        "methods": [{"name": "FedProx", "baseline": "FedAvg"}],
        "datasets": [{"name": "FEMNIST", "sample_size": "3500 clients"}],
        "results": [{"metric": "Accuracy", "value": "4.1", "unit": "%"}],
        "limitations": ["只在大规模客户端场景验证"],
        "evidence": {
            "research_problem": [{"quote": "We propose FedProx", "page": None, "source": "abstract"}],
            "methods": [{"quote": "We propose FedProx", "page": None, "source": "abstract"}],
            "datasets": [{"quote": "Experiments on FEMNIST", "page": None, "source": "abstract"}],
            "results": [{"quote": "4.1% improvement over FedAvg", "page": None, "source": "abstract"}],
        },
    },
}


def test_schema_type_lookup_is_case_insensitive():
    assert schema_entity_type("method") == "Method"
    assert schema_entity_type(" Theory ") == ""
    assert schema_relation_type("evaluated_on") == "EVALUATED_ON"
    assert schema_relation_type("EVALUATES") == ""


def test_prompt_covers_full_schema():
    prompt = entity_prompt_template()
    for entity_type in get_settings().kg.entity_types:
        assert entity_type in prompt
    for relation_type in get_settings().kg.relation_types:
        assert relation_type in prompt
    assert "{title}" in prompt and "{abstract}" in prompt


def test_deterministic_graph_from_verified_enrichment():
    graph = build_deterministic_graph([ENRICHED_PAPER])
    assert len(graph["papers"]) == 1
    row = graph["papers"][0]
    assert row["paper_id"] == "ax:1812.06127"
    assert row["limitations"] == ["只在大规模客户端场景验证"]

    names = {e["name"]: e for e in graph["entities"]}
    assert {"FedProx", "FedAvg", "FEMNIST", "Accuracy", "异构联邦学习的收敛稳定性"} == set(names)
    assert names["FedProx"]["entity_id"] == make_entity_id("FedProx")
    assert names["FedProx"]["type"] == "Method"

    pid = "ax:1812.06127"
    by_key = {(e["source_id"], e["type"], e["target_id"]): e for e in graph["edges"]}
    proposes = by_key[(pid, "PROPOSES", names["FedProx"]["entity_id"])]
    assert proposes["edge_key"] == make_edge_key(pid, "PROPOSES", names["FedProx"]["entity_id"])
    assert proposes["properties"]["evidence"] == "We propose FedProx"
    assert by_key[(names["FedProx"]["entity_id"], "COMPARES_WITH", names["FedAvg"]["entity_id"])]
    dataset_edge = by_key[(pid, "USES_DATASET", names["FEMNIST"]["entity_id"])]
    assert dataset_edge["properties"]["n"] == "3500 clients"
    metric_edge = by_key[(pid, "USES_METRIC", names["Accuracy"]["entity_id"])]
    assert metric_edge["properties"]["value"] == "4.1" and metric_edge["properties"]["unit"] == "%"
    assert by_key[(pid, "APPLIED_TO", names["异构联邦学习的收敛稳定性"]["entity_id"])]


def test_deterministic_graph_skips_unenriched_papers():
    graph = build_deterministic_graph([{"title": "No enrichment", "arxiv_id": "2401.00002"}])
    assert len(graph["papers"]) == 1
    assert graph["entities"] == [] and graph["edges"] == []


def test_gate_drops_out_of_schema_and_dangling():
    agent = KGBuilderAgent()
    raw = {
        "entities": [
            {"id": "u1", "type": "method", "name": "FedProx"},
            {"id": "u2", "type": "Dataset", "name": "FEMNIST"},
            {"id": "u3", "type": "Theory", "name": "Osmosis"},  # 不在 schema
            {"id": "u4", "type": "Author", "name": "Tian Li"},  # 无关系类型可连边
        ],
        "relations": [
            {"source_id": "u1", "target_id": "u2", "type": "evaluated_on",
             "evidence": "Experiments on FEMNIST"},
            {"source_id": "u1", "target_id": "u3", "type": "PROPOSES", "evidence": "x"},  # 端点悬空
            {"source_id": "u1", "target_id": "u2", "type": "EVALUATES", "evidence": "x"},  # 不在 schema
            {"source_id": "u1", "target_id": "u2", "type": "COMPARES_WITH",
             "evidence": "totally made up"},  # 引文校验失败
        ],
    }
    source_text = "We propose FedProx. Experiments on FEMNIST show gains over baselines."
    entities, edges, dropped = agent._gate_extraction(raw, source_text, "ax:1")

    assert {e["name"] for e in entities} == {"FedProx", "FEMNIST"}
    assert dropped == {"entity_types": 1, "relations": 2, "unlinked_types": 1}

    # 实体挂到论文上（防止孤立节点）+ 一条已验证关系 + 一条强制复核的关系
    paper_links = [e for e in edges if e["source_id"] == "ax:1"]
    assert {e["type"] for e in paper_links} == {"PROPOSES", "USES_DATASET"}

    verified = [e for e in edges if e["type"] == "EVALUATED_ON"][0]
    assert verified["properties"]["evidence"] == "Experiments on FEMNIST"
    assert verified.get("force_review") is False

    unverified = [e for e in edges if e["type"] == "COMPARES_WITH"][0]
    assert unverified["force_review"] is True
    assert unverified["properties"]["evidence_source"] == "unverified"
    assert "evidence" not in unverified["properties"]
