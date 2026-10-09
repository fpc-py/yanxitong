"""GraphRAG dual-engine: vector search + knowledge graph neighborhood expansion.

Fusion pipeline (5 steps):

1. Vector search over the FAISS paper index — partitioned by domain pack
   (``kb_id``): the primary pack plus explicitly declared ``cross_kb_ids``
   (weighted 0.85 and labeled); there is no silent whole-index pass.
2. Dual-zone KB retrieval (``kb:team`` + ``kb:{user}``) — plus an optional
   pack-tag filter (untagged chunks are 通用, visible in every pack).
3. Deterministic entity linking: retrieved papers are the entry anchors;
   question keywords are matched against the entity name index — session view
   plus a pack-wide pass (which is how a session reuses facts accumulated by
   earlier sessions in the same pack), and declared cross-pack hits arrive
   weighted and labeled.
4. Multi-hop expansion (``kg.hops``, schema relations only, confined to the
   primary + declared packs) plus the ``论文→方法→数据集→指标→对比方法`` chain.
5. Fused context with a dedicated KG section under ``kg.context_chars``;
   cross-pack hits carry ``（跨域·{kb_id}）`` provenance labels.

Without ``kb_id`` the legacy behavior (session-scoped vector search + two-pass
linking over the whole graph) is preserved verbatim.
"""

import json
import logging
import re
from typing import Optional

from src.knowledge.kb import query_chunks as kb_query_chunks, team_scope
from src.knowledge.vector_store import get_vector_store
from src.knowledge.reranker import rerank as _rerank
from src.knowledge.graph_store import CROSS_KB_WEIGHT, get_graph_store
from src.core.config import get_settings
from src.tools.paper_schema import make_paper_id

logger = logging.getLogger(__name__)

KB_TOP_K = 4

_STOPWORDS = {
    "什么", "怎么", "怎样", "如何", "哪些", "请问", "帮我", "关于", "这个", "那个",
    "是否", "可以", "一下", "介绍", "研究", "介绍下", "什么样", "the", "and", "for",
    "with", "what", "how", "which", "about", "please", "paper", "papers",
}


def _question_keywords(text: str, limit: int = 24) -> list[str]:
    """Deterministic keyword extraction for entity linking (no LLM needed)."""
    text = text or ""
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for size in (2, 3, 4):
            for i in range(0, len(run) - size + 1):
                tokens.append(run[i:i + size])
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        key = token.lower()
        if key in _STOPWORDS or key in seen:
            continue
        seen.add(key)
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _dedup_kb(chunks: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for chunk in chunks:
        key = chunk.get("chunk_hash") or chunk.get("text", "")[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(chunk)
    return out


def _format_kg_context(
    nodes: dict[str, dict],
    edges: list[dict],
    chains: list[dict],
    budget: int,
    kb_of: Optional[dict[str, str]] = None,
    primary_kb: str = "",
) -> str:
    """Render the KG section: edges with evidence, then paper→method→... chains.

    ``kb_of`` maps node id → kb_id for linked entities; with ``primary_kb``
    set, edges touching a node from a declared cross-domain pack are labeled
    ``（跨域·{kb_id}）`` so provenance survives into the answer prompt.
    """
    def name_of(node_id: str) -> str:
        return (nodes.get(node_id) or {}).get("name") or node_id

    def cross_tag(node_id: str) -> str:
        kb = (kb_of or {}).get(node_id) or ""
        if not kb or not primary_kb or kb == primary_kb:
            return ""
        return f"（跨域·{kb}）"

    lines: list[str] = []
    seen: set[tuple] = set()
    for edge in edges:
        key = (edge["source"], edge["type"], edge["target"])
        if key in seen:
            continue
        seen.add(key)
        line = f"- {name_of(edge['source'])} --{edge['type']}--> {name_of(edge['target'])}"
        tag = cross_tag(edge["source"]) or cross_tag(edge["target"])
        if tag:
            line += tag
        if edge.get("value"):
            line += f" (value: {edge['value']})"
        evidence = (edge.get("evidence") or "").strip()
        if evidence:
            line += f'  「{evidence[:120]}」'
        lines.append(line)
    chain_lines: list[str] = []
    for chain in chains:
        parts = [f"{chain.get('paper_title') or chain.get('paper_id')}"]
        if chain.get("methods"):
            parts.append("/".join(chain["methods"][:2]))
        if chain.get("datasets"):
            parts.append("/".join(d.get("name", "") for d in chain["datasets"][:2]))
        if chain.get("metrics"):
            metric = chain["metrics"][0]
            parts.append(f"{metric.get('name')}={metric.get('value')}{metric.get('unit') or ''}")
        if chain.get("compared_methods"):
            parts.append("vs " + "/".join(chain["compared_methods"][:2]))
        chain_lines.append("- " + " → ".join(p for p in parts if p))
    body = ""
    if lines:
        body += "Graph relations:\n" + "\n".join(lines)
    if chain_lines:
        body += ("\n" if body else "") + "Paper→Method→Dataset→Metric chains:\n" + "\n".join(chain_lines)
    if len(body) > budget:
        body = body[:budget].rstrip() + " …"
    return body


class GraphRAG:
    """Fuses vector similarity search with knowledge graph traversal for enriched retrieval."""

    def __init__(self):
        self.settings = get_settings()

    async def _kg_fusion(
        self,
        question: str,
        papers: list[dict],
        scope: Optional[str],
        kb_id: Optional[str] = None,
        cross_kb_ids: Optional[list[str]] = None,
    ) -> dict:
        """Steps 3–4: entity linking + multi-hop expansion. Degrades to empties.

        包分区（``kb_id`` 给定）：实体链接 = 本包会话视图（精确锚点）+ 本包
        全局匹配（跨会话复用，显式声明的跨域包以 0.85 权重并入、否则零跨包
        命中）；多跳路径只允许经过主包 + 声明包内的节点。未给 kb_id 时保持
        旧版双 pass（会话视图 + 全图）。
        """
        kg = self.settings.kg
        paper_ids = [pid for pid in (make_paper_id(p, str(p.get("kb_id") or "")) for p in papers) if pid]
        if not paper_ids:
            return {"entities": [], "relations": [], "context": "", "chains": []}
        primary = (kb_id or "").strip()
        cross = [str(x).strip() for x in (cross_kb_ids or []) if str(x or "").strip()]
        try:
            gs = await get_graph_store()
            keywords = _question_keywords(question)
            entities: dict[str, dict] = {}
            if primary:
                link_passes = [
                    {"scope": scope or None, "kb_id": primary},
                    {"scope": None, "kb_id": primary, "cross_kb_ids": cross or None},
                ]
            else:
                # 兼容：会话视图内匹配（精确）+ 全图匹配（跨会话复用）
                link_passes = [{"scope": scope or None}, {"scope": None}]
            for pass_kwargs in link_passes:
                if keywords:
                    for ent in await gs.search_entities_multi(keywords[:12], limit=20, **pass_kwargs):
                        eid = ent.get("entity_id")
                        if eid:
                            entities.setdefault(eid, ent)
            entry_ids = paper_ids + list(entities)[:30]
            hop_kwargs = {"kb_ids": [primary, *cross]} if primary else {}
            subgraph = await gs.multi_hop_paths(entry_ids, hops=kg.hops, limit=120, **hop_kwargs)
            chains = await gs.chain_query(paper_ids, limit=20)
            node_map = {n["id"]: n for n in subgraph.get("nodes", [])}
            kb_of = {
                eid: str(ent.get("kb_id") or "")
                for eid, ent in entities.items()
                if ent.get("kb_id")
            }
            context = _format_kg_context(
                node_map, subgraph.get("edges", []), chains, kg.context_chars,
                kb_of=kb_of, primary_kb=primary,
            )
            relations = [
                {"source": e["source"], "target": e["target"], "type": e["type"]}
                for e in subgraph.get("edges", [])
            ]
            return {"entities": list(entities.values()), "relations": relations, "context": context, "chains": chains}
        except Exception as e:
            logger.warning("KG fusion degraded: %s", e)
            return {"entities": [], "relations": [], "context": "", "chains": []}

    async def query(
        self,
        question: str,
        top_k: Optional[int] = None,
        scope: Optional[str] = None,
        user_id: str = "",
        kb_id: Optional[str] = None,
        cross_kb_ids: Optional[list[str]] = None,
    ) -> dict:
        """Execute dual-engine query.

        ``scope`` (the session id) restricts the legacy session-view vector
        search and prefers session-linked entities during linking.

        ``kb_id``（领域包分区）：给定时 Step1 向量检索按 ``[kb_id] + cross``
        取数——主包命中权重 1.0、声明跨包 0.85 加权重排并带来源标签；Step2
        知识库块按包标签过滤（未打标签=通用）；Step3/4 实体链接与多跳限定在
        主包 + 声明包内（跨包带 ``（跨域·…）`` 标注）。未声明 = 零跨包命中。
        未给 ``kb_id`` 时与旧版行为一致。

        ``user_id`` (an identity label like ``user:3`` / ``anon:x``) enables
        the dual-zone knowledge-base merge: shared ``kb:team`` plus the
        caller's private ``kb:{user_id}`` chunks are fused into the context
        even when no papers match — an uploaded-document-only demo still works.

        Returns dict with:
            - papers: list of matched papers with similarity scores
            - kb_docs: uploaded-document chunks shaped like papers
            - kg_entities / kg_relations: linked entities and expanded edges
            - kg_chains: 论文→方法→数据集→指标→对比方法 chains
            - fused_context: combined text context for the LLM
        """
        k = top_k or self.settings.retriever.top_k
        primary = (kb_id or "").strip()
        cross = [str(x).strip() for x in (cross_kb_ids or []) if str(x or "").strip()]
        vs = get_vector_store()

        # Step 1: Vector search —— 论文索引按领域包分区（scope=kb_id，跨会话沉淀）
        if primary:
            scopes = [primary] + [x for x in cross if x != primary]
            recall_n = max(k * 2, get_settings().retriever.reranker_candidates)
            candidates = []
            for doc in vs.search(question, top_k=recall_n, scopes=scopes):
                doc = dict(doc)
                doc_kb = str(doc.get("kb_id") or doc.get("scope") or primary)
                doc["kb_id"] = doc_kb
                doc["cross_kb"] = doc_kb != primary
                doc["weight"] = CROSS_KB_WEIGHT if doc["cross_kb"] else 1.0
                doc["score"] = float(doc.get("similarity") or 0.0) * doc["weight"]
                candidates.append(doc)
            # B1: cross-encoder rerank on the dense shortlist; degrades to dense order
            paper_results = _rerank(question, candidates, top_k=k)
        else:
            paper_results = vs.search(question, top_k=k, scope=scope)

        # Step 2: Dual-zone KB retrieval — must run before any early return so
        # a session whose topic only exists in uploaded documents still answers.
        kb_hits: list[dict] = []
        if user_id:
            try:
                kb_kwargs = {"kb_id": primary} if primary else {}
                kb_hits = kb_query_chunks(
                    question, scopes=[team_scope(), f"kb:{user_id}"], top_k=KB_TOP_K, **kb_kwargs
                )
            except Exception as e:
                logger.warning("KB retrieval degraded: %s", e)
        kb_hits = _dedup_kb(kb_hits)

        if not paper_results and not kb_hits:
            return {
                "papers": [],
                "kb_docs": [],
                "kg_entities": [],
                "kg_relations": [],
                "kg_chains": [],
                "citations": [],
                "fused_context": "No relevant papers or uploaded knowledge-base documents found.",
            }

        # Steps 3–4: knowledge-graph fusion (entity linking + multi-hop)
        fusion = await self._kg_fusion(
            question, paper_results, scope, kb_id=primary or None, cross_kb_ids=cross or None
        )

        # Step 5: Build fused context (papers first, then KB chunks, then KG)
        context_parts = []
        citations = []
        kb_docs = []

        for paper in paper_results:
            n = len(citations) + 1
            label = f"（跨域·{paper.get('kb_id')}）" if paper.get("cross_kb") else ""
            ctx = f"[{n}] {paper.get('title', 'Untitled')} "
            ctx += f"({paper.get('year', 'N/A')}){label} — {paper.get('abstract', '')[:300]}"
            context_parts.append(ctx)
            citation = {
                "index": n,
                "kind": "paper",
                "title": paper.get("title", ""),
                "url": paper.get("url", ""),
                "authors": paper.get("authors", []),
                "year": paper.get("year", 0),
                "similarity": paper.get("similarity", 0.0),
            }
            if primary:
                citation["kb_id"] = paper.get("kb_id") or primary
                if paper.get("cross_kb"):
                    citation["cross_kb"] = True
            citations.append(citation)

        for chunk in kb_hits:
            n = len(citations) + 1
            filename = chunk.get("filename", "?")
            page = chunk.get("page", 0)
            context_parts.append(f"[{n}] [KB:{filename} p{page}] {chunk.get('text', '')[:300]}")
            citations.append({
                "index": n,
                "kind": "knowledge",
                "title": f"[KB] {filename}",
                "url": "",
                "authors": [],
                "year": 0,
                "similarity": chunk.get("similarity", 0.0),
                "filename": filename,
                "page": page,
                "chunk_hash": chunk.get("chunk_hash", ""),
                "library": chunk.get("library", "personal"),
                "section_title": chunk.get("section_title", ""),
            })
            kb_docs.append({
                "title": f"[KB] {filename}",
                "text": chunk.get("text", ""),
                "kind": "knowledge",
                "filename": filename,
                "page": page,
                "chunk_hash": chunk.get("chunk_hash", ""),
                "url": "",
                "authors": [],
                "year": 0,
            })

        if fusion["context"]:
            context_parts.append("Knowledge Graph facts (evidence-anchored):\n" + fusion["context"])

        fused_context = "\n\n".join(context_parts)

        return {
            "papers": paper_results,
            "kb_docs": kb_docs,
            "kg_entities": fusion["entities"],
            "kg_relations": fusion["relations"],
            "kg_chains": fusion["chains"],
            "fused_context": fused_context,
            "citations": citations,
        }

    async def enrich_paper(self, paper_id: str) -> dict:
        """Enrich a single paper with KG connections."""
        gs = await get_graph_store()
        try:
            neighbors = await gs.get_neighbors(paper_id, depth=2)
            return neighbors
        except Exception:
            return {"nodes": [], "edges": []}


_graphrag: Optional[GraphRAG] = None


async def get_graphrag() -> GraphRAG:
    global _graphrag
    if _graphrag is None:
        _graphrag = GraphRAG()
    return _graphrag
