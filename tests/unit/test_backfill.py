"""Unit tests for the P6 graph backfill (sessions.db → Neo4j, deterministic only)."""

import pytest

from src.knowledge import graph_backfill as backfill_mod
from src.observability import audit_store


@pytest.fixture(autouse=True)
def _isolated_audit_db(tmp_path, monkeypatch):
    """backfill 内部会写审计日志 — 隔离到临时 DB，避免污染 data/audit.db。"""
    monkeypatch.setattr(audit_store, "DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setattr(audit_store, "_conn", None)
    yield
    if audit_store._conn is not None:
        audit_store._conn.close()
    audit_store._conn = None

PAPER = {
    "title": "FedProx: Federated Optimization in Heterogeneous Networks",
    "arxiv_id": "1812.06127",
    "year": 2020,
    "enrichment": {
        "methods": [{"name": "FedProx", "baseline": "FedAvg"}],
        "datasets": [{"name": "FEMNIST", "sample_size": "N=100"}],
        "results": [{"metric": "Accuracy", "value": "4.1", "unit": "%"}],
        "research_problem": "heterogeneous federated optimization",
        "evidence": {
            "methods": [{"quote": "We propose FedProx"}],
            "datasets": [{"quote": "on FEMNIST"}],
            "results": [{"quote": "improves accuracy by 4.1%"}],
            "research_problem": [{"quote": "heterogeneous networks"}],
        },
    },
}

SESSIONS = {
    "s1": {"literature_results": [PAPER]},
    "s2": {"literature_results": []},  # 无文献 → 跳过
    "s3": {"literature_results": [{"title": "Same paper", "arxiv_id": "1812.06127"}]},  # 同一论文（MERGE 幂等）
}


class _Recorder:
    def __init__(self):
        self.calls: list = []

    async def ensure_constraints(self):
        self.calls.append("ensure")

    async def upsert_papers(self, papers):
        self.calls.append(("papers", sorted(p["paper_id"] for p in papers)))
        return {"papers": len(papers)}

    async def upsert_entities(self, entities, session_id=""):
        self.calls.append(("entities", session_id, len(entities)))

    async def upsert_edges(self, edges, session_id=""):
        self.calls.append(("edges", session_id, len(edges)))

    async def link_session(self, session_id, paper_ids):
        self.calls.append(("link", session_id, list(paper_ids)))


async def test_backfill_rebuilds_papers_entities_edges(monkeypatch):
    rec = _Recorder()

    async def _gs():
        return rec

    monkeypatch.setattr(backfill_mod, "get_graph_store", _gs)

    stats = await backfill_mod.backfill(session_loader=lambda: SESSIONS)

    assert stats["degraded"] is False
    assert stats["sessions"] == 2            # s2 无文献被跳过
    assert stats["papers"] == 2              # s1 与 s3 各写一次（同 paper_id，MERGE 幂等）
    assert stats["unique_papers"] == 1
    assert stats["entities"] == 5            # FedProx/FedAvg/FEMNIST/Accuracy/ResearchProblem
    assert stats["edges"] == 5               # PROPOSES/COMPARES_WITH/USES_DATASET/USES_METRIC/APPLIED_TO
    assert rec.calls[0] == "ensure"
    assert ("papers", ["ax:1812.06127"]) in rec.calls
    links = {c[1]: c[2] for c in rec.calls if c[0] == "link"}
    assert set(links) == {"s1", "s3"}
    assert links["s1"] == ["ax:1812.06127"]


async def test_backfill_is_repeatable(monkeypatch):
    rec = _Recorder()

    async def _gs():
        return rec

    monkeypatch.setattr(backfill_mod, "get_graph_store", _gs)
    first = await backfill_mod.backfill(session_loader=lambda: SESSIONS)
    rec.calls.clear()
    second = await backfill_mod.backfill(session_loader=lambda: SESSIONS)

    assert first == second  # 纯 MERGE，重放结果一致


async def test_backfill_degrades_when_graph_down(monkeypatch):
    async def _boom():
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(backfill_mod, "get_graph_store", _boom)

    stats = await backfill_mod.backfill(session_loader=lambda: SESSIONS)

    assert stats["degraded"] is True and stats["papers"] == 0
    assert "neo4j" in stats["error"]


async def test_backfill_degrades_when_session_store_unreadable(monkeypatch):
    def _boom():
        raise RuntimeError("sessions.db corrupted")

    stats = await backfill_mod.backfill(session_loader=_boom)

    assert stats["degraded"] is True and "corrupted" in stats["error"]
