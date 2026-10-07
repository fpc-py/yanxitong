"""Unit tests for the P3 GraphRAG fusion upgrade: keyword linking + KG context."""

from src.knowledge import graphrag as graphrag_mod

PAPER = {
    "title": "FedProx: Federated Optimization in Heterogeneous Networks",
    "abstract": "We propose FedProx to handle heterogeneity.",
    "year": 2020,
    "url": "https://arxiv.org/abs/1812.06127",
    "similarity": 0.93,
}


class _StubPaperStore:
    def __init__(self, docs=None):
        self.docs = docs or []

    def search(self, query, top_k=10, scope=None):
        return [dict(d) for d in self.docs[:top_k]]


class _FakeGraphStore:
    """Canned KG: one entity link, one 3-edge neighborhood, one full chain."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def search_entities_multi(self, names, limit=200, scope=None):
        self.calls.append(("search_entities_multi", list(names), scope))
        if any("fedprox" in n.lower() for n in names):
            return [{"entity_id": "e:fedprox0001", "name": "FedProx"}]
        return []

    async def multi_hop_paths(self, entry_ids, hops=2, limit=120):
        self.calls.append(("multi_hop_paths", list(entry_ids), hops))
        return {
            "nodes": [
                {"id": "ax:1812.06127", "name": "FedProx: Federated Optimization in Heterogeneous Networks"},
                {"id": "e:fedprox0001", "name": "FedProx"},
                {"id": "e:femnist0001", "name": "FEMNIST"},
                {"id": "e:accuracy0001", "name": "Accuracy"},
            ],
            "edges": [
                {"source": "ax:1812.06127", "type": "PROPOSES", "target": "e:fedprox0001"},
                {"source": "e:fedprox0001", "type": "USES_DATASET", "target": "e:femnist0001"},
                {"source": "e:fedprox0001", "type": "USES_METRIC", "target": "e:accuracy0001",
                 "value": "4.1", "evidence": "FedProx improves accuracy."},
            ],
            "paths": [],
        }

    async def chain_query(self, paper_ids, limit=40):
        self.calls.append(("chain_query", list(paper_ids)))
        return [{
            "paper_id": "ax:1812.06127",
            "paper_title": "Federated Optimization in Heterogeneous Networks",
            "methods": ["FedProx"],
            "datasets": [{"name": "FEMNIST"}],
            "metrics": [{"name": "Accuracy", "value": "4.1", "unit": "%"}],
            "compared_methods": ["FedAvg"],
        }]


def _patch(monkeypatch, gs, papers):
    async def _get_gs():
        return gs

    monkeypatch.setattr(graphrag_mod, "get_graph_store", _get_gs)
    monkeypatch.setattr(graphrag_mod, "get_vector_store", lambda: _StubPaperStore(papers))


def test_question_keywords_latin_and_cjk():
    kw = graphrag_mod._question_keywords("What does FedProx achieve on FEMNIST 联邦学习 近端项")
    lowered = [k.lower() for k in kw]
    assert "fedprox" in lowered and "femnist" in lowered
    assert "what" not in lowered  # 停用词剔除
    assert "联邦学" in kw and "学习" in kw  # CJK n-gram 滑窗


def test_format_kg_context_budget_truncation():
    nodes = {"e:1": {"name": "A"}, "e:2": {"name": "B"}}
    edges = [{"source": "e:1", "type": "EXTENDS", "target": "e:2", "evidence": "x" * 200}]
    body = graphrag_mod._format_kg_context(nodes, edges, [], budget=120)
    assert body.endswith("…")
    assert len(body) <= 123
    full = graphrag_mod._format_kg_context(nodes, edges, [], budget=2000)
    assert "A --EXTENDS--> B" in full
    assert "「" in full and "」" in full  # 证据引文进入上下文


async def test_kg_fusion_full_pipeline(monkeypatch):
    gs = _FakeGraphStore()
    _patch(monkeypatch, gs, [PAPER])

    rag = await graphrag_mod.GraphRAG().query(
        "What does FedProx achieve on FEMNIST?", scope="sess-1"
    )

    # 实体链接：双查（会话视图 + 全图复用）
    searches = [c for c in gs.calls if c[0] == "search_entities_multi"]
    assert [c[2] for c in searches] == ["sess-1", None]
    assert [e["name"] for e in rag["kg_entities"]] == ["FedProx"]

    # 入口锚点 = 会话论文（确定性 id）+ 链接到的实体
    hop_calls = [c for c in gs.calls if c[0] == "multi_hop_paths"]
    assert hop_calls[0][1] == ["ax:1812.06127", "e:fedprox0001"]
    assert hop_calls[0][2] == graphrag_mod.get_settings().kg.hops
    chain_calls = [c for c in gs.calls if c[0] == "chain_query"]
    assert chain_calls[0][1] == ["ax:1812.06127"]

    ctx = rag["fused_context"]
    assert "Knowledge Graph facts (evidence-anchored):" in ctx
    assert "FedProx: Federated Optimization in Heterogeneous Networks --PROPOSES--> FedProx" in ctx
    assert "FedProx --USES_DATASET--> FEMNIST" in ctx
    assert "(value: 4.1)" in ctx
    assert "「FedProx improves accuracy.」" in ctx
    assert "→ FEMNIST → Accuracy=4.1% → vs FedAvg" in ctx
    assert rag["kg_chains"][0]["paper_id"] == "ax:1812.06127"


async def test_kg_fusion_degrades_when_graph_down(monkeypatch):
    async def _boom():
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(graphrag_mod, "get_graph_store", _boom)
    monkeypatch.setattr(graphrag_mod, "get_vector_store", lambda: _StubPaperStore([PAPER]))

    rag = await graphrag_mod.GraphRAG().query("FedProx", scope="sess-1")

    assert rag["papers"]  # 向量检索不受影响
    assert rag["kg_entities"] == [] and rag["kg_relations"] == [] and rag["kg_chains"] == []
    assert "Knowledge Graph facts" not in rag["fused_context"]
