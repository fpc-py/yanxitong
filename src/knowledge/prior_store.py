"""Experiment-prior store: finished-experiment rows and method/dataset/metric priors.

Backs the designer closed loop (⑩): experiment feedback (or KG seeds) append
one row per finished experiment; priors — statistics per
method/dataset/metric plus numeric ranges of declared hyper-parameters — are
aggregated on read.  The priors feed the Optuna search space (先验区间剪枝)
and the resource model.

Append-only rows in ``data/experiments.db``; every write is best-effort so
prior storage can never break the design pipeline.  Connection/locking pattern
mirrors :mod:`src.observability.audit_store`.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "record_experiment",
    "query_matrix",
    "query_priors",
    "recent_experiments",
    "count_experiments",
]

DB_PATH = os.path.join("data", "experiments.db")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    candidate_id TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',
    dataset TEXT NOT NULL DEFAULT '',
    metric TEXT NOT NULL DEFAULT '',
    value REAL,
    cost REAL,
    duration_hours REAL,
    config_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'feedback',
    notes TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_experiments_mdm ON experiments(method, dataset, metric);
CREATE INDEX IF NOT EXISTS idx_experiments_session ON experiments(session_id);
CREATE INDEX IF NOT EXISTS idx_experiments_task ON experiments(task);
"""


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        parent = os.path.dirname(DB_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def _write(sql: str, params: tuple) -> Optional[int]:
    try:
        with _lock:
            conn = _get_conn()
            cur = conn.execute(sql, params)
            conn.commit()
            return int(cur.lastrowid or 0)
    except Exception as exc:  # 先验存储绝不阻塞设计流程
        logger.warning("prior_store write degraded: %s", exc)
        return None


def _rows(sql: str, params: tuple) -> list[dict]:
    try:
        with _lock:
            conn = _get_conn()
            cur = conn.execute(sql, params)
            keys = [d[0] for d in cur.description]
            return [dict(zip(keys, row)) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("prior_store read degraded: %s", exc)
        return []


def record_experiment(
    *,
    session_id: str = "",
    run_id: str = "",
    candidate_id: str = "",
    task: str = "",
    method: str = "",
    dataset: str = "",
    metric: str = "",
    value: Optional[float] = None,
    cost: Optional[float] = None,
    duration_hours: Optional[float] = None,
    config: Optional[dict] = None,
    source: str = "feedback",
    notes: str = "",
) -> Optional[int]:
    """Append one finished-experiment row; returns the row id (None on failure)."""
    return _write(
        "INSERT INTO experiments (session_id, run_id, candidate_id, task, method, dataset,"
        " metric, value, cost, duration_hours, config_json, source, notes)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            str(session_id), str(run_id), str(candidate_id), str(task), str(method),
            str(dataset), str(metric),
            float(value) if value is not None else None,
            float(cost) if cost is not None else None,
            float(duration_hours) if duration_hours is not None else None,
            json.dumps(config or {}, ensure_ascii=False, default=str),
            str(source), str(notes),
        ),
    )


def query_matrix(
    task: str = "",
    method: str = "",
    limit: int = 200,
) -> list[dict]:
    """Aggregate (method, dataset, metric) rows into n/mean/std/min/max stats.

    std is the population standard deviation computed from AVG(v²)−AVG(v)²,
    which SQLite does not provide natively.
    """
    where = ["value IS NOT NULL"]
    params: list[Any] = []
    if task:
        where.append("task = ?")
        params.append(task)
    if method:
        where.append("method = ?")
        params.append(method)
    params.append(int(limit))
    rows = _rows(
        "SELECT method, dataset, metric, COUNT(*) AS n, AVG(value) AS mean,"
        " AVG(value * value) AS mean_sq, MIN(value) AS min, MAX(value) AS max,"
        " MAX(created_at) AS last_at"
        f" FROM experiments WHERE {' AND '.join(where)}"
        " GROUP BY method, dataset, metric ORDER BY n DESC, metric LIMIT ?",
        tuple(params),
    )
    for row in rows:
        mean = row.pop("mean_sq", None)
        if mean is not None and row.get("mean") is not None:
            variance = mean - row["mean"] ** 2
            row["std"] = round(max(variance, 0.0) ** 0.5, 6)
        else:
            row["std"] = None
        for key in ("mean", "min", "max"):
            if row.get(key) is not None:
                row[key] = round(row[key], 6)
    return rows


def _percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in [0, 1]); data sets here are tiny."""
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def query_priors(
    method: str = "",
    task: str = "",
    dataset: str = "",
    metric: str = "",
) -> dict:
    """Priors for one optimisation run.

    Returns ``{rows, metrics: [...], hyperparams: {name: {n,min,max,mean,p10,p90}},
    cost: {...}|None, duration: {...}|None}``.  ``hyperparams`` aggregates the
    numeric values found in each row's ``config.hyperparams`` plus any numeric
    top-level config keys, which is what bounds the Optuna search space.
    """
    where = []
    params: list[Any] = []
    if method:
        where.append("method = ?")
        params.append(method)
    if task:
        where.append("task = ?")
        params.append(task)
    if dataset:
        where.append("dataset = ?")
        params.append(dataset)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    rows = _rows(
        "SELECT config_json, value, cost, duration_hours, metric FROM experiments"
        f"{clause} ORDER BY id DESC LIMIT 500",
        tuple(params),
    )

    buckets: dict[str, list[float]] = {}
    costs: list[float] = []
    durations: list[float] = []
    for row in rows:
        if row.get("cost") is not None:
            costs.append(float(row["cost"]))
        if row.get("duration_hours") is not None:
            durations.append(float(row["duration_hours"]))
        try:
            cfg = json.loads(row.get("config_json") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(cfg, dict):
            continue
        candidates: dict[str, Any] = {}
        hyper = cfg.get("hyperparams")
        if isinstance(hyper, dict):
            candidates.update(hyper)
        for key, val in cfg.items():
            if key != "hyperparams" and isinstance(val, (int, float)) and not isinstance(val, bool):
                candidates[key] = val
        for key, val in candidates.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                buckets.setdefault(str(key), []).append(float(val))

    hyperparams: dict[str, dict] = {}
    for key, values in buckets.items():
        values.sort()
        hyperparams[key] = {
            "n": len(values),
            "min": round(values[0], 6),
            "max": round(values[-1], 6),
            "mean": round(sum(values) / len(values), 6),
            "p10": round(_percentile(values, 0.10), 6),
            "p90": round(_percentile(values, 0.90), 6),
        }

    def _stat(values: list[float]) -> Optional[dict]:
        if not values:
            return None
        values = sorted(values)
        return {
            "n": len(values),
            "min": round(values[0], 6),
            "max": round(values[-1], 6),
            "mean": round(sum(values) / len(values), 6),
        }

    return {
        "rows": len(rows),
        "metrics": query_matrix(task=task, method=method, limit=100),
        "hyperparams": hyperparams,
        "cost": _stat(costs),
        "duration": _stat(durations),
    }


def recent_experiments(limit: int = 20, session_id: str = "") -> list[dict]:
    """Most recent rows (newest first) with ``config`` decoded for display."""
    where = " WHERE session_id = ?" if session_id else ""
    params: tuple = (session_id, int(limit)) if session_id else (int(limit),)
    rows = _rows(
        f"SELECT * FROM experiments{where} ORDER BY id DESC LIMIT ?",
        params,
    )
    for row in rows:
        try:
            row["config"] = json.loads(row.pop("config_json") or "{}")
        except (TypeError, ValueError):
            row["config"] = {}
    return rows


def count_experiments() -> int:
    rows = _rows("SELECT COUNT(*) AS n FROM experiments", ())
    return int(rows[0]["n"]) if rows else 0
