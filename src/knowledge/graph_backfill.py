"""从会话持久化（data/sessions.db）重建全局知识图谱。

图谱 schema 升级（确定性 ID / 全局累积 / 会话视图）后，历史会话里已经检索到
的 ``literature_results`` 不会自动出现在新图谱中。本模块做一次性回填：

* 只跑确定性通道（:func:`build_deterministic_graph`，零 LLM 成本）；
* 幂等 —— 全部 MERGE，可重复执行；
* 逐会话 best-effort，单个会话失败不影响其余；图谱整体不可达时返回降级统计。
"""

import logging

from src.agents.kg_builder.agent import build_deterministic_graph
from src.knowledge.graph_store import get_graph_store
from src.tools.paper_schema import normalize_paper

logger = logging.getLogger(__name__)


def _default_loader() -> dict:
    from src.api.session_store import load_sessions

    return load_sessions()


async def backfill(session_loader=None) -> dict:
    """Rebuild Paper/Entity nodes + session views from persisted sessions."""
    stats: dict = {
        "sessions": 0,
        "papers": 0,
        "entities": 0,
        "edges": 0,
        "unique_papers": 0,
        "degraded": False,
    }
    try:
        sessions = (session_loader or _default_loader)() or {}
    except Exception as exc:
        logger.warning("Backfill session load degraded: %s", exc)
        return {**stats, "degraded": True, "error": str(exc)}

    try:
        gs = await get_graph_store()
        await gs.ensure_constraints()
    except Exception as exc:
        logger.warning("Backfill graph connect degraded: %s", exc)
        return {**stats, "degraded": True, "error": str(exc)}

    unique_papers: set[str] = set()
    errors: list[str] = []
    for session_id, state in sessions.items():
        papers = (state or {}).get("literature_results") or []
        if not papers:
            continue
        kb_id = str((state or {}).get("kb_id") or "default")
        graph = build_deterministic_graph([normalize_paper(p) for p in papers], kb_id=kb_id)
        if not graph["papers"]:
            continue
        try:
            if kb_id != "default":
                await gs.upsert_papers(graph["papers"], kb_id=kb_id)
                await gs.upsert_entities(graph["entities"], session_id=session_id, kb_id=kb_id)
                await gs.upsert_edges(graph["edges"], session_id=session_id, kb_id=kb_id)
            else:  # 默认包走遗留调用形态（id 与属性语义相同）
                await gs.upsert_papers(graph["papers"])
                await gs.upsert_entities(graph["entities"], session_id=session_id)
                await gs.upsert_edges(graph["edges"], session_id=session_id)
            paper_ids = [p["paper_id"] for p in graph["papers"]]
            await gs.link_session(session_id, paper_ids)
        except Exception as exc:
            logger.warning("Backfill session %s degraded: %s", session_id, exc)
            errors.append(f"{session_id}: {exc}")
            continue
        stats["sessions"] += 1
        stats["papers"] += len(graph["papers"])
        stats["entities"] += len(graph["entities"])
        stats["edges"] += len(graph["edges"])
        unique_papers.update(paper_ids)

    stats["unique_papers"] = len(unique_papers)
    if errors:
        stats["degraded"] = True
        stats["errors"] = errors[:5]
    try:
        from src.observability.audit_store import record_audit

        record_audit("", "backfill", "backfill_complete", stats)
    except Exception as exc:  # 审计失败不影响回填结果
        logger.debug("Backfill audit degraded: %s", exc)
    return stats
