"""Retriever Agent - 文献检索与解析智能体."""

import asyncio, logging
from src.agents.base import BaseAgent, AgentResult
from src.tools.arxiv import get_arxiv_client
from src.tools.semantic_scholar import get_ss_client
from src.knowledge.vector_store import get_vector_store

logger = logging.getLogger(__name__)


class RetrieverAgent(BaseAgent):
    name = "retriever"
    description = "文献检索与解析智能体"
    model_role = "lightweight"

    async def _execute_impl(self, state: dict) -> AgentResult:
        query = state.get("user_query", state.get("research_topic", ""))
        if not query:
            return AgentResult(success=False, error="No query provided", confidence=0.0)
        self._audit("search_start", {"query": query})
        ac, sc = get_arxiv_client(), get_ss_client()
        ar, sr = await asyncio.gather(ac.search(query), sc.search(query), return_exceptions=True)
        if isinstance(ar, Exception):
            logger.warning("Arxiv search failed: %s", ar)
            ar = []
        if isinstance(sr, Exception):
            logger.warning("SS search failed: %s", sr)
            sr = []
        all_p = list(ar)
        et = {p["title"].lower().strip() for p in all_p}
        for p in sr:
            if p["title"].lower().strip() not in et:
                all_p.append(p)
                et.add(p["title"].lower().strip())
        self._audit("search_complete", {"arxiv": len(ar), "ss": len(sr), "deduped": len(all_p)})
        citations = []
        if all_p:
            try:
                vs = get_vector_store()
                vs.add_documents(all_p)
                self._audit("index_complete", {"count": len(all_p)})
            except Exception as e:
                logger.warning("Vector store indexing degraded: %s", e)
        for p in all_p[:10]:
            citations.append({"index": len(citations)+1, "title": p.get("title",""), "url": p.get("url",""), "authors": p.get("authors",[]), "year": p.get("year",0), "source": p.get("source","")})
        return AgentResult(success=True, data={"papers": all_p, "count": len(all_p)}, citations=citations, confidence=min(1.0, len(all_p)/20.0) if all_p else 0.3)