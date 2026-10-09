"""领域包迁移：把遗留（无 kb_id）数据归属隐式默认包。

幂等，可重复执行：``python -m src.knowledge.migrate_kb``

* Neo4j：无 ``kb_id`` 属性的节点与关系统一 ``SET kb_id='default'``；
* FAISS 论文索引：``docs.json`` 里 ``scope=session_id`` 按 sessions.db 的
  会话绑定重写为其 kb_id（文献从此按领域包沉淀）；
* 先验存储：``experiments.kb_id=''`` 的行改为 ``'default'``。

逻辑上不迁移也兼容（读侧把 NULL 视同默认包），迁移只是让统计口径干净。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3

from src.core.config import get_settings

logger = logging.getLogger(__name__)

__all__ = ["migrate_neo4j", "migrate_faiss", "migrate_priors", "main"]


def _session_kb_map() -> dict[str, str]:
    """session_id → kb_id（直接读 sessions.db，避免引入 API 层依赖）。"""
    db = os.path.join("data", "sessions.db")
    if not os.path.exists(db):
        return {}
    out: dict[str, str] = {}
    try:
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute("SELECT session_id, state_json FROM sessions").fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("session kb map degraded: %s", exc)
        return {}
    for session_id, blob in rows:
        try:
            state = json.loads(blob)
        except (TypeError, ValueError):
            continue
        out[str(session_id)] = str((state or {}).get("kb_id") or "default")
    return out


async def migrate_neo4j() -> dict:
    from src.knowledge.graph_store import get_graph_store

    gs = await get_graph_store()
    nodes = await gs._run(
        "MATCH (n) WHERE n.kb_id IS NULL SET n.kb_id = 'default' RETURN count(n) AS n"
    )
    rels = await gs._run(
        "MATCH ()-[r]->() WHERE r.kb_id IS NULL SET r.kb_id = 'default' RETURN count(r) AS n"
    )
    return {
        "nodes": nodes[0]["n"] if nodes else 0,
        "relations": rels[0]["n"] if rels else 0,
    }


def migrate_faiss() -> dict:
    path = os.path.join(get_settings().retriever.index_path, "docs.json")
    if not os.path.exists(path):
        return {"docs": 0, "rewritten": 0}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("FAISS docs migration degraded: %s", exc)
        return {"docs": 0, "rewritten": 0, "error": str(exc)}
    docs = data.get("documents") or {}
    kb_of = _session_kb_map()
    kb_scopes = set(kb_of.values()) | {"default"}
    rewritten = 0
    for doc in docs.values():
        scope = str(doc.get("scope") or "")
        if not scope or scope.startswith("kb:"):
            continue
        # 会话 scope → 该会话所属包；已是包 scope 但缺 kb_id 字段的补齐
        target = kb_of.get(scope) or (scope if scope in kb_scopes else "")
        if not target:
            continue
        if doc.get("scope") == target and doc.get("kb_id") == target:
            continue
        doc["scope"] = target
        doc["kb_id"] = target
        rewritten += 1
    if rewritten:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
    return {"docs": len(docs), "rewritten": rewritten}


def migrate_priors() -> dict:
    from src.knowledge import prior_store

    prior_store._get_conn()  # 触发建表 + 列迁移
    rows = prior_store._rows("SELECT count(*) AS n FROM experiments WHERE kb_id = ''", ())
    pending = int(rows[0]["n"]) if rows else 0
    if pending:
        prior_store._write("UPDATE experiments SET kb_id = 'default' WHERE kb_id = ''", ())
    return {"updated": pending}


def main() -> dict:
    result: dict = {}
    try:
        result["neo4j"] = asyncio.run(migrate_neo4j())
    except Exception as exc:  # Neo4j 不可用不阻塞其余迁移
        result["neo4j"] = {"degraded": True, "error": str(exc)[:200]}
    try:
        result["faiss"] = migrate_faiss()
    except Exception as exc:
        result["faiss"] = {"degraded": True, "error": str(exc)[:200]}
    try:
        result["priors"] = migrate_priors()
    except Exception as exc:
        result["priors"] = {"degraded": True, "error": str(exc)[:200]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    main()
