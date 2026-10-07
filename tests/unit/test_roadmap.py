"""Unit tests for the P6 research roadmap builder."""

from src.analysis.roadmap import RoadmapBuilder, build_evolution, group_timeline


def test_group_timeline_sorts_and_skips_unknown_years():
    rows = [
        {"year": 2020, "paper_id": "ax:2", "title": "B", "arxiv_id": "2", "methods": ["FedAvg", ""]},
        {"year": 2018, "paper_id": "ax:1", "title": "A", "arxiv_id": "1", "methods": ["FedProx"]},
        {"year": 0, "paper_id": "th:x", "title": "C", "arxiv_id": "", "methods": []},
    ]
    timeline = group_timeline(rows)
    assert [g["year"] for g in timeline] == [2018, 2020]
    assert timeline[0]["paper_count"] == 1
    assert timeline[1]["papers"][0]["methods"] == ["FedAvg"]  # 空方法名被过滤


def test_build_evolution_chains_and_orders_by_year():
    edges = [
        {"source": "FedProx", "target": "SCAFFOLD", "rel": "EXTENDS", "evidence": "e1"},
        {"source": "SCAFFOLD", "target": "FedNova", "rel": "IMPROVES_ON", "evidence": "e2"},
        {"source": "Ditto", "target": "MOON", "rel": "BASED_ON", "evidence": ""},
    ]
    years = {"fedprox": 2018, "scaffold": 2020, "fednova": 2021, "ditto": 2022, "moon": 2022}

    chains = build_evolution(edges, years)

    assert len(chains) == 2
    assert chains[0]["text"] == "FedProx --EXTENDS--> SCAFFOLD --IMPROVES_ON--> FedNova"
    assert chains[0]["year"] == 2018
    assert chains[0]["evidence"] == ["e1", "e2"]
    assert chains[1]["nodes"] == ["Ditto", "MOON"]


def test_build_evolution_breaks_cycles_and_dedups_mirrors():
    edges = [
        {"source": "A", "target": "B", "rel": "EXTENDS"},
        {"source": "B", "target": "A", "rel": "IMPROVES_ON"},
    ]
    chains = build_evolution(edges, {})
    assert len(chains) == 1 and chains[0]["nodes"] == ["A", "B"]
    assert chains[0]["year"] == 0


class _FakeStore:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list = []

    async def timeline(self):
        if self.fail:
            raise RuntimeError("neo4j down")
        self.calls.append("timeline")
        return [{"year": 2020, "paper_id": "ax:1", "title": "T", "arxiv_id": "1", "methods": ["FedProx"]}]

    async def evolution_edges(self):
        return [{"source": "FedProx", "target": "SCAFFOLD", "rel": "EXTENDS", "evidence": "q"}]

    async def contradictions(self):
        return [{"source": "A", "target": "B", "evidence": "q"}]

    async def find_sparse_entities(self, scope=None, limit=30):
        self.calls.append(("sparse", scope))
        return [{"name": "Rare", "degree": 0}]


async def test_builder_full_structure():
    store = _FakeStore()

    roadmap = await RoadmapBuilder(graph_store=store).build(scope="s1")

    assert roadmap["degraded"] is False
    assert roadmap["timeline"][0]["year"] == 2020
    assert roadmap["evolution"][0]["text"] == "FedProx --EXTENDS--> SCAFFOLD"
    assert roadmap["contradictions"][0]["source"] == "A"
    assert roadmap["gaps"][0]["name"] == "Rare"
    assert ("sparse", "s1") in store.calls


async def test_builder_degrades_when_graph_down():
    roadmap = await RoadmapBuilder(graph_store=_FakeStore(fail=True)).build()

    assert roadmap["degraded"] is True
    assert roadmap["timeline"] == [] and roadmap["evolution"] == []
    assert roadmap["contradictions"] == [] and roadmap["gaps"] == []
