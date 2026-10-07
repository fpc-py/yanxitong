"""研究路线图构建器（规格⑤）：时间线 / 技术演进链 / 矛盾 / 研究空白。

全部数据来自知识图谱（Neo4j 全局事实底座）；图不可用时返回降级空结构，
调用方（/api/kg/roadmap、kg_build 节点）照常工作。

* ``timeline``       — 按年分组的论文（附其方法），年份升序
* ``evolution``      — EXTENDS / IMPROVES_ON / BASED_ON 两两关系串成的演进链，
                       按源方法发布日期排序
* ``contradictions`` — CONTRADICTS 边（带引文证据）
* ``gaps``           — 低度数实体（会话视图内），研究空白候选
"""

import logging
from typing import Optional

from src.knowledge.graph_store import get_graph_store

logger = logging.getLogger(__name__)

MAX_CHAIN_LEN = 6


def _empty(degraded: bool = True) -> dict:
    return {
        "timeline": [],
        "evolution": [],
        "contradictions": [],
        "gaps": [],
        "degraded": degraded,
    }


def group_timeline(rows: list[dict]) -> list[dict]:
    """把图谱的 (year, paper, methods) 行按年分组，年份升序。"""
    by_year: dict[int, list[dict]] = {}
    for row in rows:
        year = int(row.get("year") or 0)
        if year <= 0:
            continue
        by_year.setdefault(year, []).append({
            "paper_id": row.get("paper_id", ""),
            "title": row.get("title", ""),
            "arxiv_id": row.get("arxiv_id", "") or "",
            "methods": [m for m in (row.get("methods") or []) if m],
        })
    return [
        {"year": year, "paper_count": len(papers), "papers": papers}
        for year, papers in sorted(by_year.items())
    ]


def _method_years(timeline: list[dict]) -> dict[str, int]:
    """方法名（小写）→ 首次出现年份，用于演进链排序。"""
    years: dict[str, int] = {}
    for group in timeline:
        for paper in group["papers"]:
            for method in paper["methods"]:
                years.setdefault(method.lower(), group["year"])
    return years


def build_evolution(edges: list[dict], method_years: dict[str, int]) -> list[dict]:
    """把两两演进关系串成链（A --EXTENDS--> B --IMPROVES_ON--> C）。"""
    adjacency: dict[str, list[dict]] = {}
    incoming: set[str] = set()
    for edge in edges:
        source = edge.get("source", "")
        target = edge.get("target", "")
        if not source or not target or source == target:
            continue
        adjacency.setdefault(source, []).append({
            "target": target,
            "rel": edge.get("rel", ""),
            "evidence": edge.get("evidence") or "",
        })
        incoming.add(target)

    chains: list[dict] = []
    seen: set[frozenset] = set()
    roots = [node for node in adjacency if node not in incoming] or list(adjacency)
    for root in roots:
        nodes = [root]
        relations: list[str] = []
        evidence: list[str] = []
        cursor = root
        while len(nodes) < MAX_CHAIN_LEN and adjacency.get(cursor):
            step = adjacency[cursor][0]
            if step["target"] in nodes:  # 防环
                break
            relations.append(step["rel"])
            if step["evidence"]:
                evidence.append(step["evidence"])
            nodes.append(step["target"])
            cursor = step["target"]
        key = frozenset(nodes)
        if len(nodes) < 2 or key in seen:
            continue
        seen.add(key)
        chains.append({
            "year": method_years.get(root.lower(), 0),
            "nodes": nodes,
            "relations": relations,
            "text": nodes[0] + "".join(
                f" --{rel}--> {node}" for rel, node in zip(relations, nodes[1:])
            ),
            "evidence": evidence[:3],
        })
    chains.sort(key=lambda c: (c["year"] or 9999, c["nodes"][0].lower()))
    return chains


class RoadmapBuilder:
    """聚合图谱分析结果，产出研究洞察面板所需的完整结构。"""

    def __init__(self, graph_store=None) -> None:
        self._gs = graph_store

    async def _store(self):
        return self._gs if self._gs is not None else await get_graph_store()

    async def build(self, scope: Optional[str] = None) -> dict:
        try:
            gs = await self._store()
            timeline_rows = await gs.timeline()
            evolution_edges = await gs.evolution_edges()
            contradictions = await gs.contradictions()
            gaps = await gs.find_sparse_entities(scope=scope or None, limit=20)
        except Exception as exc:
            logger.warning("Roadmap degraded: %s", exc)
            return _empty(degraded=True)

        timeline = group_timeline(timeline_rows)
        return {
            "timeline": timeline,
            "evolution": build_evolution(evolution_edges, _method_years(timeline)),
            "contradictions": contradictions,
            "gaps": gaps,
            "degraded": False,
        }


async def get_roadmap(scope: Optional[str] = None) -> dict:
    return await RoadmapBuilder().build(scope=scope)
