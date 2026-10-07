"""GraphRAG dual-engine: vector search + knowledge graph neighborhood expansion."""

import json
import logging
from typing import Optional

from src.knowledge.kb import query_chunks as kb_query_chunks, team_scope
from src.knowledge.vector_store import get_vector_store
from src.knowledge.graph_store import get_graph_store
from src.core.config import get_settings

logger = logging.getLogger(__name__)

KB_TOP_K = 4


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


class GraphRAG:
    """Fuses vector similarity search with knowledge graph traversal for enriched retrieval."""

    def __init__(self):
        self.settings = get_settings()

    async def query(
        self,
        question: str,
        top_k: Optional[int] = None,
        scope: Optional[str] = None,
        user_id: str = "",
    ) -> dict:
        """Execute dual-engine query.

        ``scope`` (the session id) restricts both the vector search and the
        knowledge-graph lookup to the current research question, so one
        session's corpus never enriches another's answers.

        ``user_id`` (an identity label like ``user:3`` / ``anon:x``) enables
        the dual-zone knowledge-base merge: shared ``kb:team`` plus the
        caller's private ``kb:{user_id}`` chunks are fused into the context
        even when no papers match — an uploaded-document-only demo still works.

        Returns dict with:
            - papers: list of matched papers with similarity scores
            - kb_docs: uploaded-document chunks shaped like papers (for
              defenses/citation chain)
            - kg_entities: related knowledge graph entities
            - kg_relations: relations between retrieved entities
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
                "citations": [],
                "fused_context": "No relevant papers or uploaded knowledge-base documents found.",
            }

        # Step 3: Extract entity mentions from retrieved papers
        entity_names = set()
        for paper in paper_results:
            title_words = paper.get("title", "").split()
            # Collect potential entity names (capitalized multi-word terms)
            for word in title_words:
                if word[0].isupper() if word else False:
                    entity_names.add(word.strip(".,;:()[]{}"))

        # Step 4: Query knowledge graph for related entities
        kg_entities = []
        kg_relations = []

        try:
            gs = await get_graph_store()
            # 一次批量查询替代对每个实体名逐个调用 search_entities 的串行往返；
            # scope 限定只命中本会话构建的实体（图谱按研究问题隔离）
            try:
                kg_entities = await gs.search_entities_multi(list(entity_names)[:20], scope=scope)
            except Exception:
                kg_entities = []

            # Get relations among found entities
            if len(kg_entities) >= 2:
                entity_ids = [e.get("entity_id", "") for e in kg_entities[:30] if e.get("entity_id")]
                try:
                    subgraph_json = await gs.get_subgraph(entity_ids)
                    subgraph = json.loads(subgraph_json)
                    kg_relations = subgraph.get("edges", [])
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"KG query degraded: {e}")

        # Step 5: Build fused context (papers first, then KB chunks)
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

        if kg_entities:
            entities_str = "\n".join(
                f"  - {e.get('name', e.get('entity_id', '?'))} [{e.get('type', 'Entity')}]"
                for e in kg_entities[:15]
            )
            context_parts.append(f"\nRelated Knowledge Graph Entities:\n{entities_str}")

        fused_context = "\n\n".join(context_parts)

        return {
            "papers": paper_results,
            "kb_docs": kb_docs,
            "kg_entities": kg_entities,
            "kg_relations": kg_relations,
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
