"""跨域重排单元测试：主包 1.0 / 声明跨包 0.85、跨域标签、零声明零跨包。

覆盖两条通道：
* GraphRAG 论文/引用 —— 主包命中权重 1.0、声明跨包 0.85 加权重排并带
  ``（跨域·{kb}）`` 标签；未声明 = 跨包范围不进检索参数；
* ``kb_id=None`` 旧行为 —— 向量检索仍走单参 ``scope=``，结果无 weight 字段。
"""

import pytest

from src.knowledge import graphrag as graphrag_mod
from src.knowledge.graph_store import CROSS_KB_WEIGHT
from src.knowledge.graphrag import GraphRAG, _format_kg_context

KB_A = "kb_aaaaaaaa"
KB_B = "kb_bbbbbbbb"


def test_cross_kb_weight_value():
    assert CROSS_KB_WEIGHT == 0.85


class _FakeVS:
    """把 docs 当作论文索引；记录每次 search 的调用形态。"""

    def __init__(self, docs):
        self.docs = docs
        self.calls: list[dict] = []

    def search(self, question, top_k=5, scope=None, scopes=None):
        self.calls.append({"top_k": top_k, "scope": scope, "scopes": scopes})
        if scopes is not None:
            return [d for d in self.docs if d.get("scope") in scopes]
        return [d for d in self.docs if scope is None or d.get("scope") == scope]


def _doc(scope, sim, title, kb_id=None):
    doc = {"scope": scope, "similarity": sim, "title": title, "abstract": "a", "year": 2024, "url": ""}
    if kb_id:
        doc["kb_id"] = kb_id
    return doc


@pytest.fixture()
def rag(monkeypatch):
    async def fake_fusion(self, question, papers, scope, kb_id=None, cross_kb_ids=None):
        return {"entities": [], "relations": [], "context": "", "chains": []}

    monkeypatch.setattr(GraphRAG, "_kg_fusion", fake_fusion)
    return GraphRAG()


async def test_primary_outranks_cross_at_equal_similarity(rag, monkeypatch):
    vs = _FakeVS([_doc(KB_A, 0.9, "主包论文"), _doc(KB_B, 0.9, "跨包论文")])
    monkeypatch.setattr(graphrag_mod, "get_vector_store", lambda: vs)
    out = await rag.query("q", top_k=5, kb_id=KB_A, cross_kb_ids=[KB_B])

    assert vs.calls[0]["scopes"] == [KB_A, KB_B]
    papers = out["papers"]
    assert [p["title"] for p in papers] == ["主包论文", "跨包论文"]
    assert papers[0]["weight"] == 1.0 and papers[0]["cross_kb"] is False
    assert papers[1]["weight"] == CROSS_KB_WEIGHT and papers[1]["cross_kb"] is True
    assert papers[1]["score"] == pytest.approx(0.9 * CROSS_KB_WEIGHT)
    assert "（跨域·kb_bbbbbbbb）" in out["fused_context"]
    cross_cite = out["citations"][1]
    assert cross_cite["kb_id"] == KB_B and cross_cite["cross_kb"] is True
    assert out["citations"][0]["kb_id"] == KB_A and "cross_kb" not in out["citations"][0]


async def test_undeclared_cross_never_queried(rag, monkeypatch):
    vs = _FakeVS([_doc(KB_A, 0.9, "主包论文"), _doc(KB_B, 0.9, "跨包论文")])
    monkeypatch.setattr(graphrag_mod, "get_vector_store", lambda: vs)
    out = await rag.query("q", top_k=5, kb_id=KB_A)
    assert vs.calls[0]["scopes"] == [KB_A]  # 只查主包 → 跨包零命中
    assert [p["title"] for p in out["papers"]] == ["主包论文"]
    assert all(not p["cross_kb"] for p in out["papers"])


async def test_legacy_without_kb_keeps_singular_scope_call(rag, monkeypatch):
    vs = _FakeVS([_doc("s1", 0.9, "旧会话论文")])
    monkeypatch.setattr(graphrag_mod, "get_vector_store", lambda: vs)
    out = await rag.query("q", top_k=5, scope="s1")
    call = vs.calls[0]
    assert call["scope"] == "s1" and call["scopes"] is None
    assert out["papers"][0].get("weight") is None  # 旧路径不加权


def test_format_kg_context_cross_label():
    nodes = {"e:1": {"name": "本包方法"}, "e:2": {"name": "跨包数据集"}}
    edges = [{"source": "e:1", "type": "USES_DATASET", "target": "e:2"}]

    ctx = _format_kg_context(nodes, edges, [], 2000, kb_of={"e:1": KB_A, "e:2": KB_B}, primary_kb=KB_A)
    assert "（跨域·kb_bbbbbbbb）" in ctx

    same = _format_kg_context(nodes, edges, [], 2000, kb_of={"e:1": KB_A, "e:2": KB_A}, primary_kb=KB_A)
    assert "跨域" not in same

    legacy = _format_kg_context(nodes, edges, [], 2000)
    assert "跨域" not in legacy
