"""Unit tests for the SQLite audit store and BaseAgent span recording."""

import pytest

from src.agents.base import AgentResult, BaseAgent
from src.observability import audit_store


@pytest.fixture()
def audit_db(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_store, "DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setattr(audit_store, "_conn", None)
    yield audit_store
    if audit_store._conn is not None:
        audit_store._conn.close()
    audit_store._conn = None


def test_trace_roundtrip_and_session_filter(audit_db):
    audit_db.record_trace("s1", "retriever", "success", 123.4, "abc12345", {"papers": 3})
    audit_db.record_trace("s2", "kg_builder", "failed", 9.0, "def", {"error": "neo4j down"})

    rows = audit_db.recent_traces()
    assert len(rows) == 2
    top = rows[0]
    assert top["agent"] == "kg_builder" and top["status"] == "failed"
    assert top["detail"] == {"error": "neo4j down"}

    only_s1 = audit_db.recent_traces("s1")
    assert [r["agent"] for r in only_s1] == ["retriever"]
    assert only_s1[0]["duration_ms"] == 123.4
    assert only_s1[0]["trace_id"] == "abc12345"
    assert only_s1[0]["detail"] == {"papers": 3}
    assert only_s1[0]["ts"]  # CURRENT_TIMESTAMP 已写入


def test_hallucination_flags(audit_db):
    audit_db.record_hallucination_flag("s1", "triple_check", "numeric_conflict",
                                      {"metric": "Accuracy", "reason": "值冲突"})
    audit_db.record_hallucination_flag("s1", "hallucination_defense", "high", {"summary": "x"})

    flags = audit_db.recent_flags("s1")
    assert {f["layer"] for f in flags} == {"triple_check", "hallucination_defense"}
    conflict = [f for f in flags if f["layer"] == "triple_check"][0]
    assert conflict["risk_level"] == "numeric_conflict"
    assert conflict["detail"]["metric"] == "Accuracy"
    assert audit_db.recent_flags("other") == []


def test_session_trace_combines_three_tables(audit_db):
    audit_db.record_trace("s1", "supervisor", "success", 5.0, "t1")
    audit_db.record_audit("s1", "kg_builder", "build_complete", {"papers": 4})
    audit_db.record_hallucination_flag("s1", "triple_check", "contradicted")

    bundle = audit_db.session_trace("s1")
    assert bundle["session_id"] == "s1"
    assert len(bundle["traces"]) == 1 and len(bundle["audit"]) == 1 and len(bundle["flags"]) == 1
    assert bundle["audit"][0]["action"] == "build_complete"


def test_writes_never_raise_on_storage_failure(audit_db, monkeypatch):
    def _boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(audit_db, "_get_conn", _boom)
    audit_db.record_trace("s1", "x")           # 不应抛出
    audit_db.record_audit("s1", "x", "y")
    audit_db.record_hallucination_flag("s1", "l", "r")
    assert audit_db.recent_traces() == []      # 读也降级为空


class _TinyAgent(BaseAgent):
    name = "tiny"
    description = "test agent"

    def __init__(self, fail: bool = False):
        super().__init__()
        self._fail = fail

    async def _execute_impl(self, state):
        if self._fail:
            return AgentResult(success=False, error="boom")
        return AgentResult(success=True, data={"ok": True}, confidence=0.9)


async def test_base_execute_records_span(audit_db):
    await _TinyAgent().execute({"session_id": "s9"})
    await _TinyAgent(fail=True).execute({"session_id": "s9"})

    rows = audit_db.recent_traces("s9")
    assert [(r["agent"], r["status"]) for r in rows] == [
        ("tiny", "failed"), ("tiny", "success"),
    ]
    failed = rows[0]
    assert failed["detail"]["error"] == "boom"
    assert rows[1]["duration_ms"] > 0
