"""GraphRAG dual-engine: vector search + knowledge graph neighborhood expansion.

Fusion pipeline (5 steps):

1. Vector search over the session's FAISS index (entry papers).
2. Dual-zone KB retrieval (``kb:team`` + ``kb:{user}``) — unchanged.
3. Deterministic entity linking: retrieved papers are the entry anchors;
   question keywords are matched against the global entity name index, which
   is how a session reuses facts accumulated by earlier sessions (team
   knowledge base) without cross-session noise dominating the context.
4. Multi-hop expansion (``kg.hops``, schema relations only) plus the
   ``论文→方法→数据集→指标→对比方法`` chain query.
5. Fused context with a dedicated KG section under ``kg.context_chars``.
"""

import json
import logging
import re
from typing import Optional

from src.knowledge.kb import query_chunks as kb_query_chunks, team_scope
from src.knowledge.vector_store import get_vector_store
from src.knowledge.graph_store import get_graph_store
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


def _format_kg_context(nodes: dict[str, dict], edges: list[dict], chains: list[dict], budget: int) -> str:
    """Render the KG section: edges with evidence, then paper→method→... chains."""
    def name_of(node_id: str) -> str:
        return (nodes.get(node_id) or {}).get("name") or node_id

    lines: list[str] = []
    seen: set[tuple] = set()
    for edge in edges:
        key = (edge["source"], edge["type"], edge["target"])
        if key in seen:
            continue
        seen.add(key)
        line = f"- {name_of(edge['source'])} --{edge['type']}--> {name_of(edge['target'])}"
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

    async def _kg_fusion(self, question: str, papers: list[dict], scope: Optional[str]) -> dict:
        """Steps 3–4: entity linking + multi-hop expansion. Degrades to empties."""
        kg = self.settings.kg
        paper_ids = [pid for pid in (make_paper_id(p) for p in papers) if pid]
        if not paper_ids:
            return {"entities": [], "relations": [], "context": "", "chains": []}
        try:
            gs = await get_graph_store()
            keywords = _question_keywords(question)
            entities: dict[str, dict] = {}
            # 会话视图内匹配（精确）+ 全图匹配（跨会话复用，课题组知识底座）
            for hit_scope in (scope or None, None):
                if keywords:
                    for ent in await gs.search_entities_multi(keywords[:12], limit=20, scope=hit_scope):
                        eid = ent.get("entity_id")
                        if eid:
                            entities.setdefault(eid, ent)
            entry_ids = paper_ids + list(entities)[:30]
            subgraph = await gs.multi_hop_paths(entry_ids, hops=kg.hops, limit=120)
            chains = await gs.chain_query(paper_ids, limit=20)
            node_map = {n["id"]: n for n in subgraph.get("nodes", [])}
            context = _format_kg_context(node_map, subgraph.get("edges", []), chains, kg.context_chars)
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
    ) -> dict:
        """Execute dual-engine query.

        ``scope`` (the session id) restricts the vector search and prefers
        session-linked entities during linking; the graph itself is global
        (team fact base) and multi-hop expansion may traverse facts written by
        other sessions.

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
        vs = get_vector_store()

        # Step 1: Vector search (scoped to this research question)
        paper_results = vs.search(question, top_k=k, scope=scope)

        # Step 2: Dual-zone KB retrieval — must run before any early return so
        # a session whose topic only exists in uploaded documents still answers.
        kb_hits: list[dict] = []
        if user_id:
            try:
                kb_hits = kb_query_chunks(question, scopes=[team_scope(), f"kb:{user_id}"], top_k=KB_TOP_K)
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
        fusion = await self._kg_fusion(question, paper_results, scope)

        # Step 5: Build fused context (papers first, then KB chunks, then KG)
        context_parts = []
        citations = []
        kb_docs = []

        for paper in paper_results:
            n = len(citations) + 1
            ctx = f"[{n}] {paper.get('title', 'Untitled')} "
            ctx += f"({paper.get('year', 'N/A')}) — {paper.get('abstract', '')[:300]}"
            context_parts.append(ctx)
            citations.append({
                "index": n,
                "kind": "paper",
                "title": paper.get("title", ""),
                "url": paper.get("url", ""),
                "authors": paper.get("authors", []),
                "year": paper.get("year", 0),
                "similarity": paper.get("similarity", 0.0),
            })

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
