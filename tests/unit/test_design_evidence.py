"""规格②证据检索单元测试：KG 路径 + 知识库召回 + 文献锚点，全部可降级。

用假 graph store / 假 recall 替换外部依赖；断言 anchor id 契约（kg:ent:/
kg:chain:/kb:/paper:）——候选与诊断的引用门控依赖这些 id 前缀。
"""

import pytest

from src.agents.experiment_designer import evidence as evidence_mod
from src.knowledge import knowledge_libs
from src.tools.paper_schema import make_paper_id

PAPER = {
    "title": "FedProx: Federated Optimization",
    "arxiv_id": "1812.06127",
    "year": 2020,
    "key_findings": ["proximal term improves stability"],
    "methods": ["FedProx"],
}
PAPER_ID = make_paper_id(PAPER)


class _StubStore:
    def __init__(self):
        self.calls = []

    async def search_entities_multi(self, keywords, limit=200, scope=None):
        self.calls.append(("entities", list(keywords), scope))
        return [
            {"entity_id": "ent:resnet50", "name": "ResNet-50", "type": "Method"},
            {"entity_id": "ent:imagenet", "name": "ImageNet", "type": "Dataset"},
        ]

    async def chain_query(self, paper_ids, limit=40):
        self.calls.append(("chains", list(paper_ids)))
        return [{
            "paper_id": PAPER_ID, "paper_title": PAPER["title"], "year": 2020,
            "methods": ["FedProx"], "datasets": [{"name": "FEMNIST", "n": 100}],
            "metrics": [{"name": "Accuracy", "value": 0.91, "unit": ""}],
            "compared_methods": ["FedAvg"],
        }]

    async def multi_hop_paths(self, entry_ids, hops=2, limit=120):
        self.calls.append(("paths", list(entry_ids), hops))
        return {"nodes": [], "edges": [], "paths": [{"edges": [
            {"source": entry_ids[0], "target": "ent:imagenet", "type": "USES_DATASET",
             "evidence": "trained on ImageNet", "evidence_source": "p1"},
        ]}]}


@pytest.fixture()
def _stub_kb(monkeypatch):
    calls = []

    def _recall(intent, profile=None, top_k=None, libraries=None):
        calls.append((intent, tuple(libraries or []), top_k))
        return {
            "query": intent,
            "libraries": {
                "design": {"label": "实验设计库", "chunks": [
                    {"section": "超参先验区间（学习率）", "text": "CNN 用 SGD 0.05–0.1",
                     "source": "实验设计库/design_priors.md", "similarity": 0.81},
                ]},
            },
            "degraded": False,
            "sources": 1,
        }

    monkeypatch.setattr(knowledge_libs, "recall", _recall)
    return calls


async def test_collect_evidence_aggregates_and_anchors(monkeypatch, _stub_kb):
    store = _StubStore()

    async def _gs():
        return store

    monkeypatch.setattr(evidence_mod, "get_graph_store", _gs)

    result = await evidence_mod.collect_evidence(
        "在 ImageNet 上微调 ResNet-50", {"model": "ResNet50"}, "sess-1", [PAPER]
    )

    assert result["degraded"] is False
    ids = [a["id"] for a in result["anchors"]]
    assert "kg:ent:ent:resnet50" in ids
    assert f"kg:chain:{PAPER_ID}" in ids
    assert any(i.startswith("kg:path:") for i in ids)
    assert "kb:design:0" in ids
    assert f"paper:{PAPER_ID}" in ids
    assert len(ids) == len(set(ids))  # 锚点 id 唯一
    # 计数契约：entities + chains + paths + kb.sources + literature
    assert result["sources"] == 2 + 1 + 1 + 1 + 1
    # 知识库召回必须使用设计器库集
    assert _stub_kb == [("在 ImageNet 上微调 ResNet-50", tuple(knowledge_libs.DESIGN_LIBRARIES), 6)]
    # scope 传递会话 id 限定检索
    assert store.calls[0][2] == "sess-1"


async def test_collect_evidence_degrades_when_graph_down(monkeypatch, _stub_kb):
    async def _boom():
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(evidence_mod, "get_graph_store", _boom)

    result = await evidence_mod.collect_evidence("设计实验", None, "", [PAPER])

    assert result["kg"]["degraded"] is True
    assert "neo4j" in result["kg"]["error"]
    assert result["degraded"] is True
    assert result["kg"]["entities"] == [] and result["kg"]["chains"] == []
    # 降级后仍有 KB 与文献证据
    assert any(a["id"].startswith("kb:") for a in result["anchors"])
    assert any(a["id"].startswith("paper:") for a in result["anchors"])


async def test_collect_evidence_degrades_when_kb_down(monkeypatch):
    store = _StubStore()

    async def _gs():
        return store

    def _boom(*args, **kwargs):
        raise RuntimeError("encoder unavailable")

    monkeypatch.setattr(evidence_mod, "get_graph_store", _gs)
    monkeypatch.setattr(knowledge_libs, "recall", _boom)

    result = await evidence_mod.collect_evidence("设计实验", None, "", [PAPER])

    assert result["kb"]["degraded"] is True
    assert result["degraded"] is True
    assert any(a["id"].startswith("kg:ent:") for a in result["anchors"])


def test_extract_keywords_filters_and_caps():
    keywords = evidence_mod.extract_keywords(
        "the model for using based experiment design 研究",
        {"model": "EfficientNet-B3", "datasets": ["CIFAR-10", "ImageNet"]},
    )
    assert "EfficientNet-B3" in keywords
    assert "CIFAR-10" in keywords and "ImageNet" in keywords
    lowered = [k.lower() for k in keywords]
    for stop in ("the", "model", "method", "数据", "设计"):
        assert stop not in lowered
    assert len(keywords) <= evidence_mod.MAX_KEYWORDS


def test_extract_keywords_empty_input():
    assert evidence_mod.extract_keywords("", None) == []


async def test_papers_without_identity_are_skipped(monkeypatch, _stub_kb):
    async def _gs():
        return _StubStore()

    monkeypatch.setattr(evidence_mod, "get_graph_store", _gs)

    result = await evidence_mod.collect_evidence(
        "设计实验", None, "", [{"title": "", "abstract": "no identity"}]
    )
    assert result["literature"] == []
    assert not any(a["id"].startswith("paper:") for a in result["anchors"])
