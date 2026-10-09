"""领域包（KnowledgeBase 分区）注册表。

一个"领域包"= 一个知识分区（kb_id）：图谱实例、论文向量索引、文献库、
设计先验全按 kb_id 隔离；换研究问题换包，领域不串味。本体（实体/关系
schema）全局共享，包的隔离仅落在实例层。

``DEFAULT_KB`` 是隐式包（不落行）——遗留数据与未选择领域的会话都归属
它，保证升级零迁移。行存储于 ``data/packs.db``；读写全部 best-effort
降级，绝不阻塞主流程。连接/锁模式与 :mod:`src.observability.audit_store`
和 :mod:`src.knowledge.prior_store` 一致。
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import uuid

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_KB",
    "DEFAULT_KB_NAME",
    "new_kb_id",
    "is_pack",
    "list_packs",
    "get_pack",
    "create_pack",
    "update_pack",
    "validate_kb_id",
]

DEFAULT_KB = "default"
DEFAULT_KB_NAME = "未分类（默认）"

#: kb_id 需可安全嵌入图 id 串（``e:{kb}:{hash}``），只允许小写字母数字下划线
_KB_RE = re.compile(r"^kb_[0-9a-f]{8}$")

DB_PATH = os.path.join("data", "packs.db")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS packs (
    kb_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_packs_owner ON packs(owner);
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


def _write(sql: str, params: tuple) -> None:
    try:
        with _lock:
            conn = _get_conn()
            conn.execute(sql, params)
            conn.commit()
    except Exception as exc:  # 包注册绝不阻塞主流程
        logger.warning("pack_store write degraded: %s", exc)


def _rows(sql: str, params: tuple) -> list[dict]:
    try:
        with _lock:
            conn = _get_conn()
            cur = conn.execute(sql, params)
            keys = [d[0] for d in cur.description]
            return [dict(zip(keys, row)) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("pack_store read degraded: %s", exc)
        return []


def new_kb_id() -> str:
    return "kb_" + uuid.uuid4().hex[:8]


def is_pack(kb_id: str) -> bool:
    """是否实包（非空且非隐式默认包）——决定写路径是否走分区 id/kwargs。"""
    kb = str(kb_id or "").strip()
    return bool(kb) and kb != DEFAULT_KB


def _default_row() -> dict:
    return {
        "kb_id": DEFAULT_KB,
        "name": DEFAULT_KB_NAME,
        "description": "遗留数据与未选择领域的会话统一归属此包",
        "owner": "",
        "created_at": "",
    }


def list_packs(owner: str) -> list[dict]:
    """当前身份可见的包：自己的包 + 隐式默认包（永远排第一）。"""
    rows = _rows(
        "SELECT kb_id, name, description, owner, created_at FROM packs"
        " WHERE owner = ? ORDER BY created_at, kb_id",
        (str(owner),),
    )
    return [_default_row(), *rows]


def get_pack(kb_id: str) -> dict | None:
    """按 id 取包；默认包恒存在（合成行），未知返回 None。"""
    kb_id = str(kb_id or "").strip()
    if kb_id == DEFAULT_KB:
        return _default_row()
    rows = _rows(
        "SELECT kb_id, name, description, owner, created_at FROM packs WHERE kb_id = ?",
        (kb_id,),
    )
    return rows[0] if rows else None


def create_pack(owner: str, name: str, description: str = "") -> dict | None:
    """新建包；名称非法或存储降级时返回 None。"""
    name = str(name or "").strip()
    if not name:
        return None
    kb_id = new_kb_id()
    _write(
        "INSERT INTO packs (kb_id, name, description, owner) VALUES (?, ?, ?, ?)",
        (kb_id, name[:64], str(description or "")[:500], str(owner)),
    )
    return get_pack(kb_id)


def update_pack(kb_id: str, owner: str, name: str = "", description: str = "") -> dict | None:
    """改名/描述（仅 owner 可改）；默认包不可改。"""
    kb_id = str(kb_id or "").strip()
    if kb_id == DEFAULT_KB or not _KB_RE.match(kb_id):
        return None
    pack = get_pack(kb_id)
    if not pack or pack.get("owner") != str(owner):
        return None
    _write(
        "UPDATE packs SET name = ?, description = ? WHERE kb_id = ? AND owner = ?",
        (
            (name.strip()[:64] or pack["name"]),
            str(description or "").strip()[:500] or pack.get("description", ""),
            kb_id,
            str(owner),
        ),
    )
    return get_pack(kb_id)


def validate_kb_id(kb_id: str, owner: str) -> str:
    """校验会话可绑定的包；返回规范化 kb_id，非法返回 ''。"""
    kb_id = str(kb_id or "").strip() or DEFAULT_KB
    if kb_id == DEFAULT_KB:
        return DEFAULT_KB
    pack = get_pack(kb_id)
    if pack and pack.get("owner") == str(owner):
        return kb_id
    return ""
