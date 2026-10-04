"""GraphRAG dual-engine: vector search + knowledge graph neighborhood expansion."""

import json
import logging
from typing import Optional

from src.knowledge.vector_store import get_vector_store
from src.knowledge.graph_store import get_graph_store
from src.core.config import get_settings

logger = logging.getLogger(__name__)


class GraphRAG:
    """Fuses vector similarity search with knowledge graph traversal for enriched retrieval."""

    def __init__(self):
        self.settings = get_settings()

    async def query(self, question: str, top_k: Optional[int] = None) -> dict:
        """Execute dual-engine query.

        Returns dict with:
            - papers: list of matched papers with similarity scores
            - kg_entities: related knowledge graph entities
            - kg_relations: relations between retrieved entities
            - fused_context: combined text context for the LLM
        """
        k = top_k or self.settings.retriever.top_k
        vs = get_vector_store()

        # Step 1: Vector search
        paper_results = vs.search(question, top_k=k)
        if not paper_results:
            return {
                "papers": [],
                "kg_entities": [],
                "kg_relations": [],
                "fused_context": "No relevant papers found in the knowledge base.",
            }

        # Step 2: Extract entity mentions from retrieved papers
        entity_names = set()
        for paper in paper_results:
            title_words = paper.get("title", "").split()
            # Collect potential entity names (capitalized multi-word terms)
            for word in title_words:
                if word[0].isupper() if word else False:
                    entity_names.add(word.strip(".,;:()[]{}"))

        # Step 3: Query knowledge graph for related entities
        kg_entities = []
        kg_relations = []

        try:
            gs = await get_graph_store()
            # 一次批量查询替代对每个实体名逐个调用 search_entities 的串行往返
            try:
                kg_entities = await gs.search_entities_multi(list(entity_names)[:20])
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

        # Step 4: Build fused context
        context_parts = []
        citations = []

        for i, paper in enumerate(paper_results):
            ctx = f"[{i+1}] {paper.get('title', 'Untitled')} "
            ctx += f"({paper.get('year', 'N/A')}) — {paper.get('abstract', '')[:300]}"
            context_parts.append(ctx)
            citations.append({
                "index": i + 1,
                "title": paper.get("title", ""),
                "url": paper.get("url", ""),
                "authors": paper.get("authors", []),
                "year": paper.get("year", 0),
                "similarity": paper.get("similarity", 0.0),
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
