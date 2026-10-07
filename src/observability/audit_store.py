"""SQLite audit store: agent traces, audit log, and hallucination flags.

Three append-only tables in ``data/audit.db`` back the explainability
requirements:

* ``traces``              — one span per agent execution (name/status/latency)
* ``hallucination_flags`` — triple-check conflicts and quality-gate escalations
* ``audit_log``           — free-form notable events (build stats, review ops)

Every write is best-effort: storage problems are logged and swallowed so
observability can never break the research pipeline. The connection/locking
pattern mirrors :mod:`src.api.session_store`.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "record_trace",
    "record_audit",
    "record_hallucination_flag",
    "recent_traces",
    "recent_flags",
    "recent_audit",
    "session_trace",
]

DB_PATH = os.path.join("data", "audit.db")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    trace_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    trace_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'success',
    duration_ms REAL NOT NULL DEFAULT 0,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS hallucination_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT NOT NULL DEFAULT '',
    layer TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);
CREATE INDEX IF NOT EXISTS idx_flags_session ON hallucination_flags(session_id);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id);
"""


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def _write(sql: str, params: tuple) -> None:
    try:
        with _lock:
            conn = _get_conn()
            conn.execute(sql, params)
            conn.commit()
    except Exception as exc:  # 审计存储绝不阻塞主流程
        logger.warning("audit_store write degraded: %s", exc)


def _rows(sql: str, params: tuple) -> list[dict]:
    try:
        with _lock:
            conn = _get_conn()
            cur = conn.execute(sql, params)
            keys = [d[0] for d in cur.description]
            return [dict(zip(keys, row)) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("audit_store read degraded: %s", exc)
        return []


def _decode(rows: list[dict], field: str = "detail_json") -> list[dict]:
    for row in rows:
        try:
            row["detail"] = json.loads(row.pop(field) or "{}")
        except (TypeError, ValueError):
            row["detail"] = {}
    return rows


# ---- writes ------------------------------------------------------------------

def record_trace(
    session_id: str,
    agent: str,
    status: str = "success",
    duration_ms: float = 0.0,
    trace_id: str = "",
    detail: dict | None = None,
) -> None:
    """One agent execution span (推理轨迹面板的数据源)."""
    _write(
        "INSERT INTO traces (session_id, trace_id, agent, status, duration_ms, detail_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            session_id or "",
            trace_id or "",
            agent,
            status,
            float(duration_ms or 0.0),
            json.dumps(detail or {}, ensure_ascii=False, default=str),
        ),
    )


def record_audit(
    session_id: str,
    agent: str,
    action: str,
    detail: dict | None = None,
    trace_id: str = "",
) -> None:
    _write(
        "INSERT INTO audit_log (session_id, agent, action, detail_json, trace_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            session_id or "",
            agent or "",
            action,
            json.dumps(detail or {}, ensure_ascii=False, default=str),
            trace_id or "",
        ),
    )


def record_hallucination_flag(
    session_id: str,
    layer: str,
    risk_level: str,
    detail: dict | None = None,
) -> None:
    _write(
        "INSERT INTO hallucination_flags (session_id, layer, risk_level, detail_json) "
        "VALUES (?, ?, ?, ?)",
        (
            session_id or "",
            layer,
            risk_level,
            json.dumps(detail or {}, ensure_ascii=False, default=str),
        ),
    )


# ---- reads -------------------------------------------------------------------

def recent_traces(session_id: str = "", limit: int = 100) -> list[dict]:
    if session_id:
        rows = _rows(
            "SELECT * FROM traces WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
    else:
        rows = _rows("SELECT * FROM traces ORDER BY id DESC LIMIT ?", (limit,))
    return _decode(rows)


def recent_flags(session_id: str = "", limit: int = 100) -> list[dict]:
    if session_id:
        rows = _rows(
            "SELECT * FROM hallucination_flags WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
    else:
        rows = _rows("SELECT * FROM hallucination_flags ORDER BY id DESC LIMIT ?", (limit,))
    return _decode(rows)


def recent_audit(session_id: str = "", limit: int = 200) -> list[dict]:
    if session_id:
        rows = _rows(
            "SELECT * FROM audit_log WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
    else:
        rows = _rows("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    return _decode(rows)


def session_trace(session_id: str, limit: int = 500) -> dict:
    """Combined reasoning trace for one session (AgentTrace 面板)."""
    return {
        "session_id": session_id,
        "traces": recent_traces(session_id, limit),
        "flags": recent_flags(session_id, limit),
        "audit": recent_audit(session_id, limit),
    }
