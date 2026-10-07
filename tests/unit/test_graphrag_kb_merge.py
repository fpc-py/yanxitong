"""Unit tests for the dual-zone KB merge inside GraphRAG.query."""

import pytest

from src.knowledge import graphrag as graphrag_mod
from src.knowledge import kb as kb_mod
from src.knowledge.vector_store import VectorStore


class _StubPaperStore:
    def __init__(self, docs=None):
        self.docs = docs or []

    def search(self, query, top_k=10, scope=None, scopes=None):
        return [dict(d) for d in self.docs[:top_k]]


class _StubGraphStore:
    async def search_entities_multi(self, names, limit=200, scope=None):
        return []

    async def multi_hop_paths(self, entry_ids, hops=2, limit=120):
        return {"nodes": [], "edges": [], "paths": []}

    async def chain_query(self, paper_ids, limit=40):
        return []


def _chunks(*texts: str) -> list[dict]:
    return [
        {"text": t, "page": i + 1, "para": i, "section_title": "Body",
         "char_start": 0, "char_end": len(t), "chunk_index": i}
        for i, t in enumerate(texts)
    ]


@pytest.fixture()
def kb_env(tmp_path, monkeypatch, fake_encoder_cls):
    store = VectorStore(dim=8, encoder=fake_encoder_cls())
    monkeypatch.setattr(kb_mod, "_kb_store", store)
    monkeypatch.setattr(kb_mod, "KB_DIR", str(tmp_path / "kb"))
    return kb_mod


@pytest.fixture()
def rag_env(monkeypatch):
    async def _gs():
        return _StubGraphStore()

    monkeypatch.setattr(graphrag_mod, "get_graph_store", _gs)


async def test_kb_only_answer_context(rag_env, kb_env, monkeypatch):
    kb_env.add_chunks("实验手册.pdf", _chunks("联邦学习的近端项稳定性分析 " * 20), "team", "user:1", "h1")
    monkeypatch.setattr(graphrag_mod, "get_vector_store", lambda: _StubPaperStore())

    rag = await graphrag_mod.GraphRAG().query("近端项稳定性", scope="sess-1", user_id="anon:probe")

    assert rag["papers"] == []
    assert len(rag["kb_docs"]) == 1
    assert "[KB:实验手册.pdf p1]" in rag["fused_context"]
    citation = rag["citations"][0]
    assert citation["kind"] == "knowledge"
    assert citation["filename"] == "实验手册.pdf"
    assert citation["page"] == 1
    assert citation["chunk_hash"]


async def test_personal_zone_private_and_papers_numbered_first(rag_env, kb_env, monkeypatch):
    kb_env.add_chunks("mine.pdf", _chunks("my private note " * 20), "personal", "user:2", "h2")
    kb_env.add_chunks("other.pdf", _chunks("someone elses secret " * 20), "personal", "user:3", "h3")
    papers = [{"title": "FedProx", "abstract": "federated optimization", "year": 2020, "url": "u", "similarity": 0.9}]
    monkeypatch.setattr(graphrag_mod, "get_vector_store", lambda: _StubPaperStore(papers))

    rag = await graphrag_mod.GraphRAG().query("note", scope="sess-1", user_id="user:2")

    filenames = {d["filename"] for d in rag["kb_docs"]}
    assert filenames == {"mine.pdf"}  # user:3 私有块绝不泄露
    assert [c["index"] for c in rag["citations"]] == [1, 2]
    assert rag["citations"][0]["kind"] == "paper"
    assert rag["citations"][1]["kind"] == "knowledge"


async def test_cross_zone_chunk_dedup(rag_env, kb_env, monkeypatch):
    same = "identical text in both zones " * 10
    kb_env.add_chunks("a.pdf", _chunks(same), "team", "user:1", "ha")
    kb_env.add_chunks("a.pdf", _chunks(same), "personal", "user:2", "hb")
    monkeypatch.setattr(graphrag_mod, "get_vector_store", lambda: _StubPaperStore())

    rag = await graphrag_mod.GraphRAG().query("identical", scope="sess-1", user_id="user:2")
    assert len(rag["kb_docs"]) == 1


async def test_empty_everything(rag_env, kb_env, monkeypatch):
    monkeypatch.setattr(graphrag_mod, "get_vector_store", lambda: _StubPaperStore())
    rag = await graphrag_mod.GraphRAG().query("nothing matches", scope="sess-1", user_id="user:2")
    assert rag["papers"] == [] and rag["kb_docs"] == [] and rag["citations"] == []
    assert "No relevant papers" in rag["fused_context"]


async def test_no_user_id_skips_kb(rag_env, kb_env, monkeypatch):
    kb_env.add_chunks("team.pdf", _chunks("team content " * 10), "team", "user:1", "h1")
    papers = [{"title": "P", "abstract": "x" * 50, "year": 2021, "url": "", "similarity": 0.5}]
    monkeypatch.setattr(graphrag_mod, "get_vector_store", lambda: _StubPaperStore(papers))
    rag = await graphrag_mod.GraphRAG().query("team", scope="sess-1")
    assert rag["kb_docs"] == []
