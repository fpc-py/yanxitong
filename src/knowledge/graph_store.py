"""Neo4j graph store: deterministic ids, global accumulation, session views.

Identity model (single source of truth, see ``src.tools.paper_schema``):

* Paper nodes are keyed by ``paper_id`` = ``ax:{arxiv}`` | ``th:{title_hash}``
  | ``url:{sha1}`` — the same paper always maps to the same node. With a
  domain pack (``kb_id``) the id embeds the pack: ``ax:{kb}:{arxiv}``.
* Entity nodes are keyed by ``entity_id`` = ``e:{sha1(normalized name)[:12]}``
  (``e:{kb}:{sha1}`` inside a pack) and carry both the generic ``:Entity``
  label and their schema type label.
* Every relationship carries ``edge_key = sha1(source|type|target)[:16]`` so
  MERGE is idempotent and the human-review sampling stays deterministic.

领域包分区（KnowledgeBase 隔离）：本体（schema）全局共享，实例按 ``kb_id``
属性过滤；空/``"default"`` 保持遗留 id 形态，升级零迁移（``kb_id`` 为 NULL
的旧节点视同默认包）。读侧 ``kb_id=None``=旧全局行为（兼容），``kb_id`` 有值
即收窄且不再有隐式全局 pass。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Optional

from neo4j import AsyncGraphDatabase, AsyncDriver

from src.core.config import get_settings

logger = logging.getLogger(__name__)

_PAPER_ID_PREFIXES = ("ax:", "th:", "url:")
_ENTITY_NOISE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")
_IDENTITY_PROPS = ("paper_id", "entity_id")

#: 显式声明跨域时的重排权重（主包 1.0，跨包结果打折后排序）
CROSS_KB_WEIGHT = 0.85

_CONSTRAINTS = (
    "CREATE CONSTRAINT paper_id_unique IF NOT EXISTS FOR (p:Paper) REQUIRE p.paper_id IS UNIQUE",
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
    "CREATE CONSTRAINT session_id_unique IF NOT EXISTS FOR (s:Session) REQUIRE s.session_id IS UNIQUE",
    "CREATE INDEX paper_title_hash IF NOT EXISTS FOR (p:Paper) ON (p.title_hash)",
    "CREATE INDEX entity_norm_name IF NOT EXISTS FOR (e:Entity) ON (e.norm_name)",
    "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
)


def _safe_label(value: str) -> str:
    """把 LLM 产出的类型名清洗成安全的 Cypher 标签，避免标签注入。"""
    cleaned = "".join(ch for ch in str(value) if ch.isalnum() or ch == "_")
    return cleaned or "Entity"


def normalize_entity_name(name: str) -> str:
    """Lowercase / strip punctuation / collapse whitespace (CJK preserved)."""
    return _ENTITY_NOISE.sub(" ", str(name or "").lower()).strip()


def make_entity_id(name: str, kb_id: str = "") -> str:
    """Deterministic entity id: same (kb, name) always resolves to the same node.

    领域包分区：空/``"default"`` 保持遗留形态 ``e:{sha1}``（与升级前数据天然
    合并）；实包形如 ``e:{kb}:{sha1}``，同名实体在不同领域包内分立（实例隔离）。
    """
    norm = normalize_entity_name(name)
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]
    kb = (kb_id or "").strip()
    if not kb or kb == "default":
        return "e:" + digest
    return f"e:{kb}:{digest}"


def kb_filter(alias: str = "n") -> str:
    """领域包过滤谓词：``$kb`` 为 None 不过滤；``"default"`` 含未迁移(Null)数据。"""
    return (
        f"($kb IS NULL OR {alias}.kb_id = $kb "
        f"OR ({alias}.kb_id IS NULL AND $kb = 'default'))"
    )


def kb_filter_multi(alias: str = "n", param: str = "kbs") -> str:
    """多包过滤谓词（主包 + 显式跨域包）；``$kbs`` 为 None 不过滤。"""
    return (
        f"(${param} IS NULL OR {alias}.kb_id IN ${param} "
        f"OR ({alias}.kb_id IS NULL AND 'default' IN ${param}))"
    )


def make_edge_key(source_id: str, rel_type: str, target_id: str) -> str:
    return hashlib.sha1(f"{source_id}|{rel_type}|{target_id}".encode("utf-8")).hexdigest()[:16]


def sample_needs_review(edge_key: str, rate: float) -> bool:
    """Deterministic sampling: the same edge_key always gets the same verdict."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    bucket = int(hashlib.sha1(edge_key.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < rate


def _node_kind(node_id: str) -> str:
    """Which label/property pair identifies this node id."""
    if isinstance(node_id, str) and node_id.startswith(_PAPER_ID_PREFIXES):
        return "Paper"
    return "Entity"


class GraphStore:
    """Async Neo4j wrapper for knowledge graph CRUD operations."""

    def __init__(self):
        settings = get_settings()
        self._driver: Optional[AsyncDriver] = None
        self._uri = settings.neo4j.uri
        self._user = settings.neo4j.user
        self._password = settings.neo4j.password
        self._constraints_ready = False
        self._constraints_lock = asyncio.Lock()

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

    async def ensure_constraints(self):
        """Create uniqueness constraints / indexes once per process (best effort)."""
        if self._constraints_ready:
            return
        async with self._constraints_lock:
            if self._constraints_ready:
                return
            for statement in _CONSTRAINTS:
                try:
                    await self._run(statement)
                except Exception as e:  # older servers / races: degrade, do not fail writes
                    logger.warning("Constraint statement degraded: %s", e)
            self._constraints_ready = True

    # ------------------------------------------------------------------ writes

    async def upsert_papers(self, papers: list[dict], kb_id: str = "") -> dict:
        """Batch-merge Paper nodes by deterministic ``paper_id``.

        Each row: {paper_id, title, year, authors, venue, arxiv_id, doi, url,
        title_hash, abstract, source, citations_count}. Rows without a
        paper_id are skipped. When an ``ax:`` record arrives for a title that
        already exists as a ``th:`` node, the duplicate is collapsed (edges
        rewired, session links moved, duplicate deleted) — 仅在同一个领域包
        内收敛，不跨包合并。``kb_id`` 写入节点属性（空 = 默认包）。
        """
        rows = []
        ts = time.time()
        kb = (kb_id or "").strip() or "default"
        for p in papers:
            pid = (p.get("paper_id") or "").strip()
            if not pid:
                continue
            rows.append({
                "paper_id": pid,
                "title": str(p.get("title") or ""),
                "year": int(p.get("year") or 0),
                "authors": [str(a) for a in (p.get("authors") or [])][:30],
                "venue": str(p.get("venue") or ""),
                "arxiv_id": str(p.get("arxiv_id") or ""),
                "doi": str(p.get("doi") or ""),
                "url": str(p.get("url") or ""),
                "title_hash": str(p.get("title_hash") or ""),
                "abstract": str(p.get("abstract") or "")[:4000],
                "source": str(p.get("source") or ""),
                "citations_count": int(p.get("citations_count") or 0),
            })
        if not rows:
            return {"papers": 0, "collapsed": 0}
        await self.ensure_constraints()
        await self._run(
            "UNWIND $rows AS row "
            "MERGE (p:Paper {paper_id: row.paper_id}) "
            "SET p += row "
            "SET p.kb_id = coalesce(p.kb_id, $kb) "
            "SET p.created_at = coalesce(p.created_at, $ts), p.updated_at = $ts",
            {"rows": rows, "ts": ts, "kb": kb},
        )
        collapsed = 0
        for row in rows:
            if row["paper_id"].startswith("ax:") and row["title_hash"]:
                collapsed += await self._collapse_title_duplicate(row["paper_id"], row["title_hash"], kb)
        return {"papers": len(rows), "collapsed": collapsed}

    async def _collapse_title_duplicate(self, canonical_id: str, th: str, kb_id: str = "") -> int:
        """Merge ``th:`` duplicates of a paper that now has an ``ax:`` id.

        Rare path (a title-only record later gains its arXiv id): fetch the
        duplicate's edges in both directions, rewrite them onto the canonical
        node (reusing upsert_edges so edge_key dedup applies), move session
        links, then delete the duplicate. 只收敛同一个领域包内的重复。
        """
        kb = (kb_id or "").strip() or "default"
        dups = await self._run(
            "MATCH (dup:Paper {title_hash: $th}) "
            "WHERE dup.paper_id <> $pid AND dup.paper_id STARTS WITH 'th:' "
            "AND coalesce(dup.kb_id, 'default') = $kb "
            "RETURN dup.paper_id AS dup_id",
            {"th": th, "pid": canonical_id, "kb": kb},
        )
        collapsed = 0
        for row in dups:
            dup_id = row["dup_id"]
            out_edges = await self._run(
                "MATCH (dup:Paper {paper_id: $dup})-[r]->(other) "
                "RETURN type(r) AS rel, properties(r) AS props, "
                "coalesce(other.paper_id, other.entity_id) AS other_id",
                {"dup": dup_id},
            )
            in_edges = await self._run(
                "MATCH (other)-[r]->(dup:Paper {paper_id: $dup}) "
                "RETURN type(r) AS rel, properties(r) AS props, "
                "coalesce(other.paper_id, other.entity_id) AS other_id",
                {"dup": dup_id},
            )
            rewired = []
            for e in out_edges:
                props = {k: v for k, v in (e.get("props") or {}).items() if k not in ("edge_key", "sessions", "created_at", "updated_at")}
                rewired.append({"source_id": canonical_id, "target_id": e["other_id"], "type": e["rel"], "properties": props})
            for e in in_edges:
                props = {k: v for k, v in (e.get("props") or {}).items() if k not in ("edge_key", "sessions", "created_at", "updated_at")}
                rewired.append({"source_id": e["other_id"], "target_id": canonical_id, "type": e["rel"], "properties": props})
            if rewired:
                await self.upsert_edges(rewired, session_id="", kb_id=kb)
            await self._run(
                "MATCH (s:Session)-[r:RETRIEVED]->(dup:Paper {paper_id: $dup}) "
                "MATCH (p:Paper {paper_id: $pid}) "
                "MERGE (s)-[r2:RETRIEVED]->(p) SET r2.ts = coalesce(r2.ts, r.ts)",
                {"dup": dup_id, "pid": canonical_id},
            )
            await self._run("MATCH (dup:Paper {paper_id: $dup}) DETACH DELETE dup", {"dup": dup_id})
            collapsed += 1
            logger.info("Collapsed title duplicate %s -> %s", dup_id, canonical_id)
        return collapsed

    async def upsert_entities(self, entities: list[dict], session_id: str = "", kb_id: str = "") -> int:
        """Batch-merge Entity nodes with deterministic ids.

        Each row: {entity_id, name, type}. The node always carries the generic
        ``:Entity`` label plus its schema type as a secondary label; entities
        accumulate across sessions of the same domain pack (kb_id 属性写入，
        实例隔离靠 id 内嵌的包前缀)。MERGE keys on ``:Entity`` alone — the
        type label is applied with ``SET`` so a node re-typed by a later paper
        never forks into a duplicate.
        """
        ts = time.time()
        kb = (kb_id or "").strip() or "default"
        grouped: dict[str, list[dict]] = {}
        for ent in entities:
            eid = (ent.get("entity_id") or "").strip()
            name = str(ent.get("name") or "").strip()
            if not eid or not name:
                continue
            etype = _safe_label(ent.get("type") or "Entity")
            grouped.setdefault(etype, []).append({
                "entity_id": eid,
                "name": name,
                "norm_name": normalize_entity_name(name),
                "type": etype,
            })
        written = 0
        sid = session_id or None
        for etype, rows in grouped.items():
            label_set = "" if etype == "Entity" else f"SET e:{etype} "
            await self._run(
                f"UNWIND $rows AS row "
                f"MERGE (e:Entity {{entity_id: row.entity_id}}) "
                f"SET e += row "
                f"{label_set}"
                f"SET e.kb_id = coalesce(e.kb_id, $kb) "
                f"SET e.sessions = coalesce(e.sessions, []) + [x IN [$sid] WHERE x IS NOT NULL AND NOT x IN coalesce(e.sessions, [])] "
                f"SET e.created_at = coalesce(e.created_at, $ts), e.updated_at = $ts",
                {"rows": rows, "sid": sid, "ts": ts, "kb": kb},
            )
            written += len(rows)
        return written

    async def upsert_edges(self, edges: list[dict], session_id: str = "", kb_id: str = "") -> int:
        """Batch-merge relationships. Each: {source_id, target_id, type, properties}.

        Rows are grouped by (type, source label, target label) so one UNWIND
        round-trip covers each combination. The edge key makes the MERGE
        idempotent; ``sessions`` accumulates contributing session ids and
        ``needs_review`` is set once (a reviewed edge is never re-flagged).
        边同样写入 ``kb_id`` 属性（空 = 默认包）供按包过滤。
        """
        ts = time.time()
        kb = (kb_id or "").strip() or "default"
        rate = get_settings().kg.review_sample_rate
        grouped: dict[tuple[str, str, str], list[dict]] = {}
        for edge in edges:
            source_id = (edge.get("source_id") or "").strip()
            target_id = (edge.get("target_id") or "").strip()
            rtype = _safe_label(edge.get("type") or "RELATED")
            if not source_id or not target_id:
                continue
            edge_key = edge.get("edge_key") or make_edge_key(source_id, rtype, target_id)
            props = dict(edge.get("properties") or {})
            props.pop("edge_key", None)
            key = (rtype, _node_kind(source_id), _node_kind(target_id))
            grouped.setdefault(key, []).append({
                "source_id": source_id,
                "target_id": target_id,
                "edge_key": edge_key,
                "props": props,
                "session_id": session_id or None,
                # force_review：证据未通过引文校验的边 100% 进入人工复核
                "needs_review": True if edge.get("force_review") else sample_needs_review(edge_key, rate),
                "ts": ts,
            })
        written = 0
        for (rtype, src_label, tgt_label), rows in grouped.items():
            src_key = "paper_id" if src_label == "Paper" else "entity_id"
            tgt_key = "paper_id" if tgt_label == "Paper" else "entity_id"
            await self._run(
                f"UNWIND $rows AS row "
                f"MATCH (a:{src_label} {{{src_key}: row.source_id}}), (b:{tgt_label} {{{tgt_key}: row.target_id}}) "
                f"MERGE (a)-[r:{rtype}]->(b) "
                f"SET r += row.props "
                f"SET r.edge_key = coalesce(r.edge_key, row.edge_key) "
                f"SET r.kb_id = coalesce(r.kb_id, $kb) "
                f"SET r.sessions = coalesce(r.sessions, []) + [x IN [row.session_id] WHERE x IS NOT NULL AND NOT x IN coalesce(r.sessions, [])] "
                f"SET r.needs_review = coalesce(r.needs_review, row.needs_review) "
                f"SET r.created_at = coalesce(r.created_at, row.ts), r.updated_at = row.ts",
                {"rows": rows, "kb": kb},
            )
            written += len(rows)
        return written

    async def link_session(self, session_id: str, paper_ids: list[str]) -> int:
        """Record which papers this research session retrieved (session view)."""
        ids = [pid for pid in (paper_ids or []) if pid]
        if not session_id or not ids:
            return 0
        await self.ensure_constraints()
        ts = time.time()
        await self._run(
            "MERGE (s:Session {session_id: $sid}) SET s.updated_at = $ts, s.created_at = coalesce(s.created_at, $ts)",
            {"sid": session_id, "ts": ts},
        )
        await self._run(
            "MATCH (s:Session {session_id: $sid}) "
            "UNWIND $ids AS pid "
            "MATCH (p:Paper {paper_id: pid}) "
            "MERGE (s)-[r:RETRIEVED]->(p) SET r.ts = coalesce(r.ts, $ts)",
            {"sid": session_id, "ids": ids, "ts": ts},
        )
        return len(ids)

    async def create_entities(self, entities: list[dict], kb_id: str = ""):
        """Legacy writer used by the KB NER path: {id, type, properties}.

        KB entities keep their ``kb:*|`` prefixed ids and stay outside the
        ``:Entity`` namespace, preserving the KB isolation boundary。上传时
        可带领域标签（``kb_id``），仅作为过滤属性附加，不改 id 前缀。
        """
        tag = (kb_id or "").strip()
        grouped: dict[str, list[dict]] = {}
        for ent in entities:
            props = dict(ent.get("properties", {}))
            props["entity_id"] = ent["id"]
            if tag:
                props["kb_id"] = tag
            grouped.setdefault(_safe_label(ent.get("type", "Entity")), []).append(props)

        for label, rows in grouped.items():
            query = (
                f"UNWIND $rows AS row "
                f"MERGE (e:{label} {{entity_id: row.entity_id}}) "
                f"SET e += row"
            )
            await self._run(query, {"rows": rows})

    async def create_relations(self, relations: list[dict], kb_id: str = ""):
        """Legacy writer used by the KB NER path: {source_id, target_id, type, properties}."""
        tag = (kb_id or "").strip()
        grouped: dict[str, list[dict]] = {}
        for rel in relations:
            props = dict(rel.get("properties", {}))
            if tag:
                props["kb_id"] = tag
            grouped.setdefault(_safe_label(rel.get("type", "RELATED")), []).append({
                "source_id": rel["source_id"],
                "target_id": rel["target_id"],
                "properties": props,
            })

        for rtype, rows in grouped.items():
            query = (
                f"UNWIND $rows AS row "
                f"MATCH (a {{entity_id: row.source_id}}), (b {{entity_id: row.target_id}}) "
                f"MERGE (a)-[r:{rtype}]->(b) "
                f"SET r += row.properties"
            )
            await self._run(query, {"rows": rows})

    # ------------------------------------------------------------------- reads

    async def query_entity(self, entity_id: str) -> Optional[dict]:
        results = await self._run(
            "MATCH (e {entity_id: $entity_id}) RETURN e", {"entity_id": entity_id}
        )
        return dict(results[0]["e"]) if results else None

    async def get_paper(self, paper_id: str) -> Optional[dict]:
        results = await self._run(
            "MATCH (p:Paper {paper_id: $pid}) RETURN p", {"pid": paper_id}
        )
        if not results:
            return None
        paper = dict(results[0]["p"])
        entity_ids_result = await self._run(
            "MATCH (p:Paper {paper_id: $pid})--(e:Entity) "
            "RETURN DISTINCT e LIMIT 60",
            {"pid": paper_id},
        )
        paper["entities"] = [dict(r["e"]) for r in entity_ids_result]
        return paper

    async def papers_existing(self, paper_ids: list[str], kb_id: Optional[str] = None) -> list[str]:
        """Which of these paper ids already exist in the fact base（可按领域包收窄）。"""
        ids = [pid for pid in (paper_ids or []) if pid]
        if not ids:
            return []
        rows = await self._run(
            f"MATCH (p:Paper) WHERE p.paper_id IN $ids AND {kb_filter('p')} "
            "RETURN p.paper_id AS paper_id",
            {"ids": ids, "kb": kb_id},
        )
        return [r["paper_id"] for r in rows]

    async def search_entities(
        self, keyword: str, entity_type: Optional[str] = None, kb_id: Optional[str] = None
    ) -> list[dict]:
        type_filter = f":{_safe_label(entity_type)}" if entity_type else ""
        results = await self._run(
            f"MATCH (e:Entity{type_filter}) WHERE e.name CONTAINS $keyword "
            f"AND {kb_filter('e')} "
            "RETURN e LIMIT 50",
            {"keyword": keyword, "kb": kb_id},
        )
        return [dict(r["e"]) for r in results]

    async def search_entities_multi(
        self,
        keywords: list[str],
        limit: int = 200,
        scope: Optional[str] = None,
        kb_id: Optional[str] = None,
        cross_kb_ids: Optional[list[str]] = None,
    ) -> list[dict]:
        """批量按多个关键词检索实体（一次往返）。

        scope（会话 id）只把检索限定到本会话论文关联的实体；kb_id 给定后
        再按领域包收窄（主包 + 显式声明的跨域包），跨包命中带 0.85 权重参与
        排序（跨域显式声明并重排）。未给 kb_id 时行为与旧版一致（全图检索）。
        """
        if not keywords:
            return []
        kbs: Optional[list[str]] = None
        primary = (kb_id or "").strip()
        if primary:
            kbs = [primary]
            for extra in (cross_kb_ids or []):
                extra = str(extra or "").strip()
                if extra and extra not in kbs:
                    kbs.append(extra)
        kb_cond = f"AND ({kb_filter_multi('e', 'kbs')}) " if kbs else ""
        params: dict = {"keywords": keywords, "limit": limit, "kbs": kbs}
        if scope:
            results = await self._run(
                "MATCH (s:Session {session_id: $scope})-[:RETRIEVED]->(:Paper)--(e:Entity) "
                "WHERE any(kw IN $keywords WHERE e.name CONTAINS kw) "
                f"{kb_cond}"
                "WITH DISTINCT e "
                "RETURN e, size([kw IN $keywords WHERE e.name CONTAINS kw]) AS kw_hits "
                "ORDER BY kw_hits DESC, e.name LIMIT $limit",
                {**params, "scope": scope},
            )
        else:
            results = await self._run(
                "MATCH (e:Entity) "
                "WHERE any(kw IN $keywords WHERE e.name CONTAINS kw) "
                f"{kb_cond}"
                "RETURN e, size([kw IN $keywords WHERE e.name CONTAINS kw]) AS kw_hits "
                "ORDER BY kw_hits DESC, e.name LIMIT $limit",
                params,
            )
        entities: list[dict] = []
        for row in results:
            entity = dict(row["e"])
            hits = int(row.get("kw_hits") or 1)
            ent_kb = str(entity.get("kb_id") or "default")
            entity["kb_id"] = ent_kb
            entity["kw_hits"] = hits
            weight = 1.0 if (not kbs or ent_kb == primary) else CROSS_KB_WEIGHT
            entity["weight"] = weight
            entity["score"] = hits * weight
            entities.append(entity)
        if kbs:
            entities.sort(key=lambda e: (-e["score"], str(e.get("name") or "").lower()))
        return entities

    async def find_sparse_entities(
        self, scope: Optional[str] = None, limit: int = 30, kb_id: Optional[str] = None
    ) -> list[dict]:
        """Entities with the fewest relationships — candidate research gaps.

        scope：只看本会话论文关联实体的稀疏度（会话视图内的研究空白）；
        kb_id：再按领域包收窄（默认包含未迁移数据）。
        """
        kb_cond = f"AND {kb_filter('e')} "
        if scope:
            results = await self._run(
                "MATCH (s:Session {session_id: $scope})-[:RETRIEVED]->(:Paper)--(e:Entity) "
                f"WHERE true {kb_cond}"
                "WITH DISTINCT e "
                "OPTIONAL MATCH (e)-[r]-() "
                "RETURN e AS entity, labels(e) AS labels, count(r) AS degree "
                "ORDER BY degree ASC LIMIT $limit",
                {"scope": scope, "limit": limit, "kb": kb_id},
            )
        else:
            results = await self._run(
                "MATCH (e:Entity) "
                f"WHERE {kb_filter('e')} "
                "OPTIONAL MATCH (e)-[r]-() "
                "RETURN e AS entity, labels(e) AS labels, count(r) AS degree "
                "ORDER BY degree ASC LIMIT $limit",
                {"limit": limit, "kb": kb_id},
            )
        sparse = []
        for row in results:
            entity = dict(row["entity"])
            labels = row.get("labels") or []
            sparse.append({
                "entity_id": entity.get("entity_id", ""),
                "name": entity.get("name", ""),
                "type": entity.get("type") or (labels[0] if labels else ""),
                "kb_id": entity.get("kb_id") or "default",
                "degree": row.get("degree", 0),
            })
        sparse.sort(key=lambda item: (item["degree"], item["name"]))
        return sparse

    async def _paths(self, query: str, params: dict) -> dict:
        """Run a path query and turn path rows into {nodes, edges, paths}."""
        rows = await self._run(query, params)
        nodes: dict[str, dict] = {}
        edges: dict[tuple, dict] = {}
        paths: list[dict] = []
        for row in rows:
            ids = row.get("ids") or []
            names = row.get("names") or []
            types = row.get("types") or []
            rtypes = row.get("rtypes") or []
            evidences = row.get("evidences") or []
            sources = row.get("sources") or []
            values = row.get("values") or []
            for node_id, name, ntype in zip(ids, names, types):
                if node_id and node_id not in nodes:
                    nodes[node_id] = {"id": node_id, "name": name or node_id, "type": ntype or "", "kind": _node_kind(node_id).lower()}
            chain = []
            for i, rtype in enumerate(rtypes):
                if i + 1 >= len(ids):
                    break
                source_id, target_id = ids[i], ids[i + 1]
                edge = {
                    "source": source_id,
                    "target": target_id,
                    "type": rtype,
                    "evidence": evidences[i] if i < len(evidences) else None,
                    "evidence_source": sources[i] if i < len(sources) else None,
                    "value": values[i] if i < len(values) else None,
                }
                key = (source_id, rtype, target_id)
                edges.setdefault(key, edge)
                chain.append(edge)
            if chain:
                paths.append({"edges": chain})
        return {"nodes": list(nodes.values()), "edges": list(edges.values()), "paths": paths}

    @staticmethod
    def _type_filter() -> str:
        types = [_safe_label(t) for t in get_settings().kg.relation_types]
        return "|".join(types) if types else "RELATED"

    async def multi_hop_paths(
        self,
        entry_ids: list[str],
        hops: int = 2,
        limit: int = 120,
        kb_ids: Optional[list[str]] = None,
    ) -> dict:
        """Expand a set of entry nodes along schema relations (both directions).

        kb_ids：只允许路径经过这些领域包内的节点（主包 + 显式跨域声明）；
        未给时与旧版一致（不限制）。防跨域穿越污染。
        """
        ids = [i for i in (entry_ids or []) if i]
        if not ids:
            return {"nodes": [], "edges": [], "paths": []}
        hops = max(1, min(int(hops or 1), 3))
        rel_filter = self._type_filter()
        kbs = [k for k in (kb_ids or []) if k] or None
        kb_cond = (
            f"AND all(n IN nodes(path) WHERE ({kb_filter_multi('n', 'kbs')})) "
            if kbs else ""
        )
        select = (
            "RETURN [n IN nodes(path) | coalesce(n.paper_id, n.entity_id)] AS ids, "
            "[n IN nodes(path) | coalesce(n.name, n.title, '')] AS names, "
            "[n IN nodes(path) | coalesce(n.type, '')] AS types, "
            "[r IN relationships(path) | type(r)] AS rtypes, "
            "[r IN relationships(path) | r.evidence] AS evidences, "
            "[r IN relationships(path) | r.evidence_source] AS sources, "
            "[r IN relationships(path) | r.value] AS values "
        )
        params = {"ids": ids, "limit": limit, "kbs": kbs}
        out = await self._paths(
            f"MATCH path = (start)-[:{rel_filter}*1..{hops}]->(other) "
            f"WHERE coalesce(start.paper_id, start.entity_id) IN $ids "
            f"{kb_cond}"
            f"{select} LIMIT $limit",
            params,
        )
        incoming = await self._paths(
            f"MATCH path = (start)<-[:{rel_filter}*1..{hops}]-(other) "
            f"WHERE coalesce(start.paper_id, start.entity_id) IN $ids "
            f"{kb_cond}"
            f"{select} LIMIT $limit",
            params,
        )
        nodes = {n["id"]: n for n in out["nodes"]}
        for n in incoming["nodes"]:
            nodes.setdefault(n["id"], n)
        edges = {(e["source"], e["type"], e["target"]): e for e in out["edges"]}
        for e in incoming["edges"]:
            edges.setdefault((e["source"], e["type"], e["target"]), e)
        return {"nodes": list(nodes.values()), "edges": list(edges.values()), "paths": out["paths"] + incoming["paths"]}

    async def get_neighbors(self, entity_id: str, depth: int = 1) -> dict:
        """Get entity and its neighborhood as a subgraph dict."""
        result = await self.multi_hop_paths([entity_id], hops=max(1, min(depth, 3)), limit=100)
        return {"nodes": result["nodes"], "edges": result["edges"]}

    async def chain_query(self, paper_ids: list[str], limit: int = 40) -> list[dict]:
        """The 论文→方法→数据集→指标→对比方法 chain for the given papers."""
        ids = [p for p in (paper_ids or []) if p]
        if not ids:
            return []
        methods = await self._run(
            "MATCH (p:Paper)-[:PROPOSES]->(m:Method) WHERE p.paper_id IN $ids "
            "RETURN p.paper_id AS pid, collect(DISTINCT m.name) AS names LIMIT $limit",
            {"ids": ids, "limit": limit},
        )
        datasets = await self._run(
            "MATCH (p:Paper)-[r:USES_DATASET]->(d:Dataset) WHERE p.paper_id IN $ids "
            "RETURN p.paper_id AS pid, collect(DISTINCT {name: d.name, n: r.n}) AS items LIMIT $limit",
            {"ids": ids, "limit": limit},
        )
        metrics = await self._run(
            "MATCH (p:Paper)-[r:USES_METRIC]->(mt:Metric) WHERE p.paper_id IN $ids "
            "RETURN p.paper_id AS pid, collect(DISTINCT {name: mt.name, value: r.value, unit: r.unit}) AS items LIMIT $limit",
            {"ids": ids, "limit": limit},
        )
        compared = await self._run(
            "MATCH (p:Paper)-[:PROPOSES]->(m:Method)-[r:OUTPERFORMS|IMPROVES_ON|COMPARES_WITH]->(m2:Method) "
            "WHERE p.paper_id IN $ids "
            "RETURN p.paper_id AS pid, collect(DISTINCT m2.name) AS names LIMIT $limit",
            {"ids": ids, "limit": limit},
        )
        by_pid: dict[str, dict] = {}
        for row in methods:
            by_pid.setdefault(row["pid"], {})["methods"] = row["names"]
        for row in datasets:
            by_pid.setdefault(row["pid"], {})["datasets"] = [i for i in row["items"] if i.get("name")]
        for row in metrics:
            by_pid.setdefault(row["pid"], {})["metrics"] = [i for i in row["items"] if i.get("name")]
        for row in compared:
            by_pid.setdefault(row["pid"], {})["compared_methods"] = row["names"]
        titles = await self._run(
            "MATCH (p:Paper) WHERE p.paper_id IN $ids "
            "RETURN p.paper_id AS pid, p.title AS title, p.arxiv_id AS arxiv_id, p.year AS year",
            {"ids": ids},
        )
        title_map = {r["pid"]: r for r in titles}
        chains = []
        for pid, parts in by_pid.items():
            info = title_map.get(pid, {})
            chains.append({
                "paper_id": pid,
                "paper_title": info.get("title", ""),
                "arxiv_id": info.get("arxiv_id", ""),
                "year": info.get("year", 0),
                "methods": parts.get("methods", []),
                "datasets": parts.get("datasets", []),
                "metrics": parts.get("metrics", []),
                "compared_methods": parts.get("compared_methods", []),
            })
        chains.sort(key=lambda c: (-int(c.get("year") or 0), c.get("paper_title", "")))
        return chains[:limit]

    async def verify_triples(self, triples: list[dict], kb_id: Optional[str] = None) -> list[dict]:
        """Reverse-lookup (method, dataset, metric) claims against the graph.

        Returns raw graph facts per triple — scoring lives in
        :mod:`src.safety.triple_check`:

        * which entities exist (id + name),
        * papers that link method and dataset together,
        * metric values those papers recorded (for numeric-conflict checks).

        ``kb_id`` 按领域包校验（None = 旧全局行为）。
        """
        facts = []
        for triple in triples or []:
            method = normalize_entity_name(triple.get("method", ""))
            dataset = normalize_entity_name(triple.get("dataset", ""))
            metric = normalize_entity_name(triple.get("metric", ""))
            fact = {
                "triple": dict(triple),
                "method": await self._lookup_entity(method, "Method", kb_id),
                "dataset": await self._lookup_entity(dataset, "Dataset", kb_id),
                "metric": await self._lookup_entity(metric, "Metric", kb_id),
                "supporting_papers": [],
            }
            m, d = fact["method"], fact["dataset"]
            if m.get("entity_id") and d.get("entity_id"):
                paper_rows = await self._run(
                    "MATCH (p:Paper)--(m:Method {entity_id: $mid}) "
                    "MATCH (p)--(d:Dataset {entity_id: $did}) "
                    f"WHERE {kb_filter('p')} "
                    "RETURN DISTINCT p.paper_id AS paper_id, p.title AS title",
                    {"mid": m["entity_id"], "did": d["entity_id"], "kb": kb_id},
                )
                metric_id = fact["metric"].get("entity_id") if fact["metric"] else ""
                if paper_rows and metric_id:
                    pids = [r["paper_id"] for r in paper_rows]
                    value_rows = await self._run(
                        "MATCH (p:Paper)-[r:USES_METRIC]->(mt:Metric {entity_id: $mtid}) "
                        "WHERE p.paper_id IN $pids "
                        "RETURN p.paper_id AS paper_id, r.value AS value, r.unit AS unit",
                        {"mtid": metric_id, "pids": pids},
                    )
                    values = {r["paper_id"]: {"value": r["value"], "unit": r["unit"]} for r in value_rows}
                    for row in paper_rows:
                        entry = {"paper_id": row["paper_id"], "title": row.get("title", "")}
                        if entry["paper_id"] in values:
                            entry.update(values[entry["paper_id"]])
                        fact["supporting_papers"].append(entry)
                elif paper_rows:
                    fact["supporting_papers"] = [{"paper_id": r["paper_id"], "title": r.get("title", "")} for r in paper_rows]
            facts.append(fact)
        return facts

    async def _lookup_entity(self, norm_name: str, label: str, kb_id: Optional[str] = None) -> dict:
        """Exact normalized-name lookup, then a CONTAINS fallback."""
        if not norm_name:
            return {}
        rows = await self._run(
            f"MATCH (e:{_safe_label(label)}) WHERE e.norm_name = $name "
            f"AND {kb_filter('e')} "
            "RETURN e.entity_id AS entity_id, e.name AS name LIMIT 3",
            {"name": norm_name, "kb": kb_id},
        )
        if not rows:
            rows = await self._run(
                f"MATCH (e:{_safe_label(label)}) WHERE (e.norm_name CONTAINS $name OR $name CONTAINS e.norm_name) "
                f"AND {kb_filter('e')} "
                "RETURN e.entity_id AS entity_id, e.name AS name LIMIT 3",
                {"name": norm_name, "kb": kb_id},
            )
        if not rows:
            return {}
        return {"entity_id": rows[0]["entity_id"], "name": rows[0]["name"], "alternatives": [r["name"] for r in rows[1:]]}

    async def evidence_path(self, target_id: str, limit: int = 30, kb_id: Optional[str] = None) -> list[dict]:
        """Trace a Paper/Entity node back to papers with verbatim edge evidence."""
        if not target_id:
            return []
        rows = await self._run(
            "MATCH (p:Paper)-[r]-(e:Entity) "
            "WHERE (p.paper_id = $tid OR e.entity_id = $tid) "
            f"AND {kb_filter('p')} AND {kb_filter('e')} "
            "RETURN p.paper_id AS paper_id, p.title AS paper_title, p.arxiv_id AS arxiv_id, "
            "e.entity_id AS entity_id, e.name AS entity_name, coalesce(e.type, '') AS entity_type, "
            "type(r) AS rel, r.evidence AS evidence, r.evidence_source AS evidence_source, "
            "r.value AS value, r.sessions AS sessions "
            "ORDER BY rel LIMIT $limit",
            {"tid": target_id, "limit": limit, "kb": kb_id},
        )
        return [
            {
                "paper_id": r["paper_id"],
                "paper_title": r["paper_title"],
                "arxiv_id": r["arxiv_id"] or "",
                "entity_id": r["entity_id"],
                "entity_name": r["entity_name"],
                "entity_type": r["entity_type"],
                "relation": r["rel"],
                "evidence": r["evidence"] or "",
                "evidence_source": r["evidence_source"] or "",
                "value": r["value"] or "",
                "sessions": r["sessions"] or [],
            }
            for r in rows
        ]

    async def search_papers(self, keyword: str, limit: int = 20) -> list[dict]:
        rows = await self._run(
            "MATCH (p:Paper) WHERE toLower(p.title) CONTAINS toLower($kw) "
            "RETURN p.paper_id AS paper_id, p.title AS title, p.year AS year, p.arxiv_id AS arxiv_id "
            "ORDER BY p.year DESC LIMIT $limit",
            {"kw": keyword, "limit": limit},
        )
        return rows

    # -------------------------------------------------------------- review ops

    async def review_queue(self, limit: int = 50, kb_id: Optional[str] = None) -> list[dict]:
        """Edges sampled for human review that have not been reviewed yet（可按包过滤）。"""
        rows = await self._run(
            "MATCH (a)-[r]->(b) "
            "WHERE r.needs_review = true AND coalesce(r.reviewed, false) = false "
            f"AND {kb_filter('r')} "
            "RETURN r.edge_key AS edge_key, type(r) AS rel, "
            "coalesce(a.paper_id, a.entity_id) AS source_id, coalesce(a.name, a.title, '') AS source_name, "
            "coalesce(b.paper_id, b.entity_id) AS target_id, coalesce(b.name, b.title, '') AS target_name, "
            "r.evidence AS evidence, r.evidence_source AS evidence_source, "
            "r.sessions AS sessions, coalesce(a.abstract, '') AS source_abstract, r.created_at AS created_at "
            "ORDER BY r.created_at DESC LIMIT $limit",
            {"limit": limit, "kb": kb_id},
        )
        return rows

    async def mark_reviewed(self, edge_key: str, decision: str, note: str = "", reviewer: str = "") -> bool:
        """Record a human verdict; ``rejected`` deletes the edge."""
        if not edge_key:
            return False
        rows = await self._run(
            "MATCH (a)-[r]->(b) WHERE r.edge_key = $key "
            "RETURN count(r) AS n",
            {"key": edge_key},
        )
        if not rows or not rows[0].get("n"):
            return False
        if decision == "rejected":
            await self._run("MATCH (a)-[r]->(b) WHERE r.edge_key = $key DELETE r", {"key": edge_key})
            return True
        await self._run(
            "MATCH (a)-[r]->(b) WHERE r.edge_key = $key "
            "SET r.reviewed = true, r.review_decision = $decision, r.review_note = $note, "
            "r.reviewed_by = $reviewer, r.reviewed_at = $ts, r.needs_review = false",
            {"key": edge_key, "decision": decision, "note": note, "reviewer": reviewer, "ts": time.time()},
        )
        return True

    # ---------------------------------------------------------- session views

    async def session_paper_ids(self, session_id: str) -> list[str]:
        if not session_id:
            return []
        rows = await self._run(
            "MATCH (s:Session {session_id: $sid})-[:RETRIEVED]->(p:Paper) "
            "RETURN p.paper_id AS pid",
            {"sid": session_id},
        )
        return [r["pid"] for r in rows]

    async def session_entity_ids(self, session_id: str, limit: int = 60) -> list[str]:
        if not session_id:
            return []
        rows = await self._run(
            "MATCH (s:Session {session_id: $sid})-[:RETRIEVED]->(:Paper)--(e:Entity) "
            "RETURN DISTINCT e.entity_id AS eid LIMIT $limit",
            {"sid": session_id, "limit": limit},
        )
        return [r["eid"] for r in rows]

    async def session_entity_count(self, session_id: str) -> int:
        ids = await self.session_entity_ids(session_id, limit=1000)
        return len(ids)

    async def session_subgraph(self, session_id: str, limit: int = 300) -> dict:
        """Papers of one session + one-hop entities + edges among them."""
        paper_rows = await self._run(
            "MATCH (s:Session {session_id: $sid})-[:RETRIEVED]->(p:Paper) "
            "RETURN p.paper_id AS paper_id, p.title AS title, p.year AS year, p.arxiv_id AS arxiv_id",
            {"sid": session_id},
        )
        paper_ids = [r["paper_id"] for r in paper_rows]
        if not paper_ids:
            return {"nodes": [], "edges": []}
        entity_ids = await self.session_entity_ids(session_id, limit=limit)
        ids = paper_ids + entity_ids
        sub = json.loads(await self.get_subgraph(ids))
        nodes = {n["id"]: n for n in sub.get("nodes", [])}
        for row in paper_rows:
            nodes.setdefault(row["paper_id"], {
                "id": row["paper_id"],
                "name": row.get("title") or row["paper_id"],
                "type": "Paper",
                "kind": "paper",
                "year": row.get("year") or 0,
                "arxiv_id": row.get("arxiv_id") or "",
            })
        edges = [e for e in sub.get("edges", []) if e["source"] in nodes and e["target"] in nodes]
        return {"nodes": list(nodes.values())[:limit], "edges": edges[:limit]}

    async def get_subgraph(self, entity_ids: list[str]) -> str:
        """Get subgraph (nodes + directed edges) as JSON string for the given ids."""
        ids = [i for i in (entity_ids or []) if i]
        if not ids:
            return json.dumps({"nodes": [], "edges": []}, ensure_ascii=False)
        rows = await self._run(
            "MATCH (a)-[r]->(b) "
            "WHERE coalesce(a.paper_id, a.entity_id) IN $ids AND coalesce(b.paper_id, b.entity_id) IN $ids "
            "RETURN properties(a) AS a, properties(b) AS b, type(r) AS rel, properties(r) AS props "
            "LIMIT 500",
            {"ids": ids},
        )
        nodes: dict[str, dict] = {}
        edges = []
        for row in rows:
            a, b = row["a"], row["b"]
            a_id = a.get("paper_id") or a.get("entity_id") or ""
            b_id = b.get("paper_id") or b.get("entity_id") or ""
            if a_id not in nodes:
                nodes[a_id] = {"id": a_id, "name": a.get("name") or a.get("title") or a_id, "type": a.get("type") or ("Paper" if a.get("paper_id") else ""), "kind": _node_kind(a_id).lower()}
            if b_id not in nodes:
                nodes[b_id] = {"id": b_id, "name": b.get("name") or b.get("title") or b_id, "type": b.get("type") or ("Paper" if b.get("paper_id") else ""), "kind": _node_kind(b_id).lower()}
            edges.append({
                "source": a_id,
                "target": b_id,
                "type": row["rel"],
                "properties": {k: v for k, v in (row["props"] or {}).items() if k not in ("sessions",)},
            })
        return json.dumps({"nodes": list(nodes.values()), "edges": edges}, ensure_ascii=False)

    # -------------------------------------------------------------- analytics

    async def graph_overview(self, kb_id: Optional[str] = None) -> dict:
        """Counts + schema for the KG overview endpoint（kb_id 过滤 = 包内视图）。"""
        kg = get_settings().kg
        papers = await self._run(
            f"MATCH (p:Paper) WHERE {kb_filter('p')} RETURN count(p) AS n", {"kb": kb_id}
        )
        if kb_id:
            sessions = await self._run(
                "MATCH (s:Session)-[:RETRIEVED]->(p:Paper) "
                f"WHERE {kb_filter('p')} RETURN count(DISTINCT s) AS n",
                {"kb": kb_id},
            )
        else:
            sessions = await self._run("MATCH (s:Session) RETURN count(s) AS n")
        entities = await self._run(
            "MATCH (e:Entity) "
            f"WHERE {kb_filter('e')} "
            "RETURN coalesce(e.type, head([l IN labels(e) WHERE l <> 'Entity']), 'Entity') AS t, count(e) AS n "
            "ORDER BY n DESC",
            {"kb": kb_id},
        )
        relations = await self._run(
            # 只统计论文事实图谱的边；知识库（kb:*| 命名空间）有独立 schema，不计入本总览
            "MATCH (a)-[r]->(b) WHERE type(r) <> 'RETRIEVED' "
            f"AND {kb_filter('r')} "
            "AND NOT (coalesce(a.entity_id, '') STARTS WITH 'kb:' OR coalesce(a.paper_id, '') STARTS WITH 'kb:') "
            "AND NOT (coalesce(b.entity_id, '') STARTS WITH 'kb:' OR coalesce(b.paper_id, '') STARTS WITH 'kb:') "
            "RETURN type(r) AS t, count(r) AS n ORDER BY n DESC",
            {"kb": kb_id},
        )
        pending = await self._run(
            "MATCH (a)-[r]->(b) WHERE r.needs_review = true AND coalesce(r.reviewed, false) = false "
            f"AND {kb_filter('r')} "
            "AND NOT (coalesce(a.entity_id, '') STARTS WITH 'kb:' OR coalesce(a.paper_id, '') STARTS WITH 'kb:') "
            "RETURN count(r) AS n",
            {"kb": kb_id},
        )
        entity_counts = {r["t"]: r["n"] for r in entities}
        relation_counts = {r["t"]: r["n"] for r in relations}
        return {
            "papers": papers[0]["n"] if papers else 0,
            "sessions": sessions[0]["n"] if sessions else 0,
            "entities": entity_counts,
            "entities_total": sum(entity_counts.values()),
            "relations": relation_counts,
            "relations_total": sum(relation_counts.values()),
            "pending_review": pending[0]["n"] if pending else 0,
            "kb_id": kb_id or "default",
            "schema": {
                "entity_types": list(kg.entity_types),
                "relation_types": list(kg.relation_types),
            },
        }

    async def kb_stats(self, kb_id: str) -> dict:
        """领域包统计（论文/实体/边），供包管理界面展示。"""
        kb = (kb_id or "").strip() or "default"
        papers = await self._run(
            f"MATCH (p:Paper) WHERE {kb_filter('p')} RETURN count(p) AS n", {"kb": kb}
        )
        entities = await self._run(
            f"MATCH (e:Entity) WHERE {kb_filter('e')} RETURN count(e) AS n", {"kb": kb}
        )
        relations = await self._run(
            "MATCH (a)-[r]->(b) WHERE type(r) <> 'RETRIEVED' "
            f"AND {kb_filter('r')} "
            "AND NOT (coalesce(a.entity_id, '') STARTS WITH 'kb:' OR coalesce(a.paper_id, '') STARTS WITH 'kb:') "
            "AND NOT (coalesce(b.entity_id, '') STARTS WITH 'kb:' OR coalesce(b.paper_id, '') STARTS WITH 'kb:') "
            "RETURN count(r) AS n",
            {"kb": kb},
        )
        return {
            "kb_id": kb,
            "papers": papers[0]["n"] if papers else 0,
            "entities": entities[0]["n"] if entities else 0,
            "relations": relations[0]["n"] if relations else 0,
        }

    async def timeline(self, kb_id: Optional[str] = None) -> list[dict]:
        """Papers grouped by year with their methods — roadmap input（可按包过滤）。"""
        rows = await self._run(
            f"MATCH (p:Paper) WHERE coalesce(p.year, 0) > 0 AND {kb_filter('p')} "
            "OPTIONAL MATCH (p)-[:PROPOSES]->(m:Method) "
            "RETURN p.year AS year, p.paper_id AS paper_id, p.title AS title, p.arxiv_id AS arxiv_id, "
            "collect(DISTINCT m.name) AS methods "
            "ORDER BY year ASC",
            {"kb": kb_id},
        )
        return rows

    async def evolution_edges(self, kb_id: Optional[str] = None) -> list[dict]:
        """Method-to-method evolution relations (EXTENDS/IMPROVES_ON/BASED_ON)."""
        rows = await self._run(
            "MATCH (a:Entity)-[r:EXTENDS|IMPROVES_ON|BASED_ON]->(b:Entity) "
            f"WHERE {kb_filter('a')} AND {kb_filter('b')} "
            "RETURN coalesce(a.name, a.entity_id) AS source, coalesce(b.name, b.entity_id) AS target, "
            "type(r) AS rel, r.evidence AS evidence "
            "LIMIT 200",
            {"kb": kb_id},
        )
        return rows

    async def contradictions(self, kb_id: Optional[str] = None) -> list[dict]:
        rows = await self._run(
            "MATCH (a)-[r:CONTRADICTS]-(b) "
            f"WHERE {kb_filter('a')} AND {kb_filter('b')} "
            "RETURN coalesce(a.name, a.title, a.entity_id, a.paper_id) AS source, "
            "coalesce(b.name, b.title, b.entity_id, b.paper_id) AS target, "
            "r.evidence AS evidence, r.evidence_source AS evidence_source "
            "LIMIT 100",
            {"kb": kb_id},
        )
        # CONTRADICTS 语义对称：LLM 可能双向建边，按无序对去重避免前端 A VS B 出现两次
        seen: set[tuple] = set()
        unique: list[dict] = []
        for row in rows:
            key = tuple(sorted((row.get("source") or "", row.get("target") or "")))
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return unique

    # ------------------------------------------------------------- maintenance

    async def purge_legacy(self, dry_run: bool = True) -> dict:
        """Delete pre-global-model nodes left by the old kg_builder.

        Legacy = anything that is neither a new-model node (``:Paper`` /
        ``:Entity`` / ``:Session``) nor part of the KB namespace (``kb:*|``
        prefixed ids). Old writes used free-form labels (Tool/Field/Theory/...)
        with LLM-generated ids (``e1``/``session|name``), invisible to all new
        read paths but still counted by the overview.

        ``coalesce`` 不可省：旧节点大多只有 entity_id 没有 paper_id，Neo4j 中
        ``null STARTS WITH ...`` 求值为 null，会把整行 WHERE 过滤掉（漏删旧节点）。
        Returns ``{"matched", "deleted", "dry_run"}``; runs in dry-run mode
        by default so the caller can confirm before destroying data.
        """
        match = (
            "MATCH (n) WHERE NOT n:Paper AND NOT n:Entity AND NOT n:Session "
            "AND NOT (coalesce(n.entity_id, '') STARTS WITH 'kb:' "
            "OR coalesce(n.paper_id, '') STARTS WITH 'kb:') "
        )
        if dry_run:
            rows = await self._run(match + "RETURN count(n) AS n")
            return {"matched": rows[0]["n"] if rows else 0, "deleted": 0, "dry_run": True}
        rows = await self._run(match + "DETACH DELETE n RETURN count(*) AS deleted")
        deleted = rows[0]["deleted"] if rows else 0
        logger.info("Purged %d legacy nodes", deleted)
        return {"matched": deleted, "deleted": deleted, "dry_run": False}

    async def clear_all(self):
        await self._run("MATCH (n) DETACH DELETE n")
        logger.info("Graph store cleared")


_graph_store: Optional[GraphStore] = None


async def get_graph_store() -> GraphStore:
    global _graph_store
    if _graph_store is None:
        _graph_store = GraphStore()
    return _graph_store
