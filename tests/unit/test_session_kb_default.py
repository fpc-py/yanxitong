"""会话 kb 绑定单元测试：工厂默认、跨域规范化、旧会话懒归属、往返持久化。

旧会话（无 kb_id 字段）在读取时懒归属默认包——与遗留图/向量数据
（kb_id 为空）天然一致，升级零迁移。
"""

import pytest
from fastapi import HTTPException

from src.api import routes
from src.api import session_store
from src.api.session_store import load_sessions, save_session
from src.knowledge.pack_store import DEFAULT_KB
from src.workflows.state import create_initial_state


class _Identity:
    def __init__(self, label: str):
        self.label = label


def test_create_initial_state_defaults_to_default_kb():
    state = create_initial_state(session_id="s1", user_id="anon:x", topic="t", query="q")
    assert state["kb_id"] == "default"
    assert state["cross_kb_ids"] == []


def test_create_initial_state_keeps_binding_and_normalizes_cross():
    state = create_initial_state(
        session_id="s1", user_id="anon:x", topic="t", query="q",
        kb_id="kb_aaaaaaaa",
        cross_kb_ids=["kb_bbbbbbbb", "", "  ", "kb_cccccccc"],
    )
    assert state["kb_id"] == "kb_aaaaaaaa"
    assert state["cross_kb_ids"] == ["kb_bbbbbbbb", "kb_cccccccc"]


def test_get_owned_state_lazy_default_for_legacy_state(monkeypatch):
    legacy = {"user_id": "anon:x"}
    monkeypatch.setitem(routes._sessions, "s_legacy", legacy)
    got = routes._get_owned_state("s_legacy", _Identity("anon:x"))
    assert got["kb_id"] == DEFAULT_KB
    assert routes._session_kb(got) == DEFAULT_KB


def test_get_owned_state_rejects_foreign_owner(monkeypatch):
    monkeypatch.setitem(routes._sessions, "s_other", {"user_id": "anon:y"})
    with pytest.raises(HTTPException) as err:
        routes._get_owned_state("s_other", _Identity("anon:x"))
    assert err.value.status_code == 404


def test_session_kb_fallback_for_empty_value():
    assert routes._session_kb({}) == DEFAULT_KB
    assert routes._session_kb({"kb_id": ""}) == DEFAULT_KB
    assert routes._session_kb({"kb_id": "kb_aaaaaaaa"}) == "kb_aaaaaaaa"


def test_roundtrip_persists_kb_binding(tmp_path, monkeypatch):
    if session_store._conn is not None:
        session_store._conn.close()
    monkeypatch.setattr(session_store, "DB_PATH", str(tmp_path / "sessions.db"))
    monkeypatch.setattr(session_store, "_conn", None)
    try:
        state = create_initial_state(
            session_id="s1", user_id="anon:x", topic="t", query="q",
            kb_id="kb_aaaaaaaa", cross_kb_ids=["kb_bbbbbbbb"],
        )
        save_session("s1", state)
        loaded = load_sessions()["s1"]
        assert loaded["kb_id"] == "kb_aaaaaaaa"
        assert loaded["cross_kb_ids"] == ["kb_bbbbbbbb"]
    finally:
        if session_store._conn is not None:
            session_store._conn.close()
            session_store._conn = None
