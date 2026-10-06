"""会话状态 SQLite 持久化单元测试（不依赖后端进程）。

覆盖：状态往返读写、同会话覆盖更新、删除会话、不可序列化值降级为字符串。
"""

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api import session_store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    """把存储指向临时库并重置连接，避免污染 data/sessions.db。"""
    if session_store._conn is not None:
        session_store._conn.close()
    old_path = session_store.DB_PATH
    session_store._conn = None
    session_store.DB_PATH = str(tmp_path / "sessions.db")
    yield
    if session_store._conn is not None:
        session_store._conn.close()
    session_store._conn = None
    session_store.DB_PATH = old_path


class TestSessionStore:
    def test_save_and_load_roundtrip(self):
        state = {
            "user_id": "anon:test-1",
            "topic": "图神经网络在药物分子性质预测中的应用",
            "papers": [{"title": "GNN", "year": 2024}],
            "count": 2,
        }
        session_store.save_session("s1", state)
        assert session_store.load_sessions() == {"s1": state}

    def test_upsert_overwrites_previous_state(self):
        session_store.save_session("s1", {"v": 1})
        session_store.save_session("s1", {"v": 2})
        assert session_store.load_sessions() == {"s1": {"v": 2}}

    def test_delete_removes_only_target_session(self):
        session_store.save_session("s1", {"v": 1})
        session_store.save_session("s2", {"v": 2})
        session_store.delete_session("s1")
        assert session_store.load_sessions() == {"s2": {"v": 2}}

    def test_non_serialisable_values_are_stringified(self):
        session_store.save_session("s1", {"ts": datetime(2026, 10, 6, 9, 0)})
        assert session_store.load_sessions()["s1"]["ts"].startswith("2026-10-06")
