"""SQLite persistence for research session states.

Sessions live in memory during a request lifecycle; every state snapshot is
written through to ``data/sessions.db`` so a backend restart keeps history
sessions, citation chains and paper summaries instead of losing them.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["save_session", "load_sessions", "delete_session"]

DB_PATH = os.path.join("data", "sessions.db")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute(_SCHEMA)
        _conn.commit()
    return _conn


def save_session(session_id: str, state: dict) -> None:
    """Upsert one session state snapshot (non-serialisable values stringified)."""
    blob = json.dumps(state, ensure_ascii=False, default=str)
    with _lock:
        conn = _get_conn()
        conn.execute(
            """
            INSERT INTO sessions (session_id, state_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, blob),
        )
        conn.commit()


def load_sessions() -> dict[str, dict]:
    """Load all persisted session states; corrupt rows are skipped."""
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT session_id, state_json FROM sessions").fetchall()
    sessions: dict[str, dict] = {}
    for session_id, blob in rows:
        try:
            sessions[session_id] = json.loads(blob)
        except (TypeError, ValueError):
            logger.warning("Skipping corrupt persisted session %s", session_id)
    return sessions


def delete_session(session_id: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()
