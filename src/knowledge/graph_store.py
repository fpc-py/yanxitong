"""Neo4j graph store wrapper for knowledge graph operations."""

import json
import logging
from typing import Optional

from neo4j import AsyncGraphDatabase, AsyncDriver

from src.core.config import get_settings

logger = logging.getLogger(__name__)


def _safe_label(value: str) -> str:
    """把 LLM 产出的类型名清洗成安全的 Cypher 标签，避免标签注入。"""
    cleaned = "".join(ch for ch in str(value) if ch.isalnum() or ch == "_")
    return cleaned or "Entity"


class GraphStore:
    """Async Neo4j wrapper for knowledge graph CRUD operations."""

    def __init__(self):
        settings = get_settings()
        self._driver: Optional[AsyncDriver] = None
        self._uri = settings.neo4j.uri
        self._user = settings.neo4j.user
        self._password = settings.neo4j.password

    async def connect(self):
        if self._driver is None:
            self._driver = AsyncGraphDatabase.driver(
                self._uri, auth=(self._user, self._password)
            )
            await self._driver.verify_connectivity()
            logger.info(f"Connected to Neo4j at {self._uri}")

    async def close(self):
        if self._driver:
            await self._driver.close()
            self._driver = None

    async def _run(self, query: str, params: dict = None) -> list[dict]:
        await self.connect()
        records, _, _ = await self._driver.execute_query(query, params or {})
        return [dict(r) for r in records]

    async def create_entities(self, entities: list[dict]):
        """Create or merge entities. Each: {id, type, properties: {name, ...}}

        按类型分组后用 UNWIND 批量写入：几百个实体从「一次一个 Cypher 往返」
        降为「每个类型一次往返」。
        """
        grouped: dict[str, list[dict]] = {}
        for ent in entities:
            props = dict(ent.get("properties", {}))
            props["entity_id"] = ent["id"]
            grouped.setdefault(_safe_label(ent.get("type", "Entity")), []).append(props)

        for label, rows in grouped.items():
            query = (
                f"UNWIND $rows AS row "
                f"MERGE (e:{label} {{entity_id: row.entity_id}}) "
                f"SET e += row"
            )
            await self._run(query, {"rows": rows})

    async def create_relations(self, relations: list[dict]):
        """Create relationships. Each: {source_id, target_id, type, properties: {}}"""
        grouped: dict[str, list[dict]] = {}
        for rel in relations:
            grouped.setdefault(_safe_label(rel.get("type", "RELATED")), []).append({
                "source_id": rel["source_id"],
                "target_id": rel["target_id"],
                "properties": dict(rel.get("properties", {})),
            })

        for rtype, rows in grouped.items():
            query = (
                f"UNWIND $rows AS row "
                f"MATCH (a {{entity_id: row.source_id}}), (b {{entity_id: row.target_id}}) "
                f"MERGE (a)-[r:{rtype}]->(b) "
                f"SET r += row.properties"
            )
            await self._run(query, {"rows": rows})

    async def query_entity(self, entity_id: str) -> Optional[dict]:
        results = await self._run(
            "MATCH (e {entity_id: $entity_id}) RETURN e", {"entity_id": entity_id}
        )
        return dict(results[0]["e"]) if results else None

    async def get_neighbors(self, entity_id: str, depth: int = 1) -> dict:
        """Get entity and its neighborhood as a subgraph dict."""
        results = await self._run(
            f"MATCH path = (e {{entity_id: $entity_id}})-[*1..{depth}]-(neighbor) "
            "RETURN e, neighbor, relationships(path) as rels LIMIT 100",
            {"entity_id": entity_id}
        )
        nodes = {}
        edges = []
        for r in results:
            e_data = dict(r["e"])
            nodes[e_data["entity_id"]] = e_data
            n_data = dict(r["neighbor"])
            nodes[n_data["entity_id"]] = n_data
            for rel in r.get("rels", []):
                edges.append({
                    "source": rel.start_node.get("entity_id", ""),
                    "target": rel.end_node.get("entity_id", ""),
                    "type": rel.type,
                })
        return {"nodes": list(nodes.values()), "edges": edges}

    async def search_entities(self, keyword: str, entity_type: Optional[str] = None) -> list[dict]:
        type_filter = f":{entity_type}" if entity_type else ""
        results = await self._run(
            f"MATCH (e{type_filter}) WHERE e.name CONTAINS $keyword "
            "RETURN e LIMIT 50",
            {"keyword": keyword}
        )
        return [dict(r["e"]) for r in results]

    async def search_entities_multi(self, keywords: list[str], limit: int = 200) -> list[dict]:
        """批量按多个关键词检索实体（一次往返，替代逐个关键词循环查询）。"""
        if not keywords:
            return []
        results = await self._run(
            "UNWIND $keywords AS kw MATCH (e) WHERE e.name CONTAINS kw "
            "RETURN DISTINCT e LIMIT $limit",
            {"keywords": keywords, "limit": limit},
        )
        return [dict(r["e"]) for r in results]

    async def get_subgraph(self, entity_ids: list[str]) -> str:
        """Get subgraph as JSON string containing nodes and relationships."""
        results = await self._run(
            "MATCH (a)-[r]-(b) WHERE a.entity_id IN $ids AND b.entity_id IN $ids "
            "RETURN a, b, r",
            {"ids": entity_ids}
        )
        nodes = {}
        edges = []
        for row in results:
            a = dict(row["a"])
            b = dict(row["b"])
            nodes[a["entity_id"]] = a
            nodes[b["entity_id"]] = b
            edges.append({
                "source": row["r"].start_node.get("entity_id"),
                "target": row["r"].end_node.get("entity_id"),
                "type": row["r"].type,
                "properties": dict(row["r"]),
            })
        return json.dumps({"nodes": list(nodes.values()), "edges": edges}, ensure_ascii=False)

    async def clear_all(self):
        await self._run("MATCH (n) DETACH DELETE n")
        logger.info("Graph store cleared")


_graph_store: Optional[GraphStore] = None


async def get_graph_store() -> GraphStore:
    global _graph_store
    if _graph_store is None:
        _graph_store = GraphStore()
    return _graph_store
