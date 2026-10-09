"""Retriever Agent - 文献检索与解析智能体."""

import asyncio, logging
from src.agents.base import BaseAgent, AgentResult, parse_llm_json
from src.agents.retriever.enricher import PaperEnricher
from src.analysis.literature_analysis import LiteratureAnalyzer
from src.core.config import get_settings
from src.knowledge.graph_store import get_graph_store
from src.mcp_servers.registry import DEFAULT_SOURCES, SEARCH_TOOL
from src.tools.mcp_runtime import call_tool
from src.tools.paper_schema import merge_dedup, paper_identity
from src.knowledge.vector_store import get_vector_store

logger = logging.getLogger(__name__)

# 送入向量索引的单篇正文长度上限（字符）；与 kg_builder 的 ABSTRACT_LIMIT 同一取舍
EMBED_TEXT_LIMIT = 1200

SEARCH_QUERY_PROMPT = """你是学术检索策略助手。请将「研究主题」与「用户问题」转化为一条用于 arXiv / OpenAlex / Semantic Scholar 全文检索的查询语句。

要求：
1. 只保留学术关键词（研究对象、方法、指标、场景），去掉「请检索 / 总结 / 近五年 / 研究进展 / 争议」等指令性词语；
2. 中文主题必须同时给出对应英文关键词：英文关键词在前，中文关键词在后，用空格分隔（arXiv 仅收录英文文献）；
3. 8-20 个词，不加标点、引号或解释。

输出 JSON：{{"query": "检索语句"}}

研究主题：{topic}
用户问题：{query}"""


class RetrieverAgent(BaseAgent):
    name = "retriever"
    description = "文献检索与解析智能体"
    model_role = "lightweight"

    async def _build_search_candidates(self, topic: str, query: str) -> list[str]:
        """Distil (topic, question) into keyword-style search queries, best first.

        Academic full-text search matches keyword lists far better than long
        instruction-style questions; the distilled query also carries English
        terms so arXiv (English-only) returns results for Chinese topics.
        Falls back to the raw topic / question when distillation fails.
        """
        candidates: list[str] = []
        try:
            response = await self._call_llm(
                SEARCH_QUERY_PROMPT.format(topic=topic or "（未填写）", query=query),
                json_mode=True,
            )
            distilled = str(parse_llm_json(response).get("query", "") or "").strip().strip('"“”')
            if 2 <= len(distilled) <= 200 and "\n" not in distilled:
                candidates.append(distilled)
        except Exception as e:
            logger.warning("Search-query distillation degraded to raw query: %s", e)
        if topic and topic.lower() != "general" and topic != query:
            candidates.append(topic)
        candidates.append(query)
        return list(dict.fromkeys(c for c in candidates if c.strip()))

    async def _search_all_sources(self, query: str) -> tuple[list[dict], dict[str, int]]:
        """Fan out the query across every registered MCP source.

        Each source is an MCP tool call (see ``src.tools.mcp_runtime``); a
        failing source degrades to an empty list without affecting the others.
        Per-source result caps come from ``retriever.<source>_max_results``.
        """
        retriever_settings = get_settings().retriever
        results = await asyncio.gather(
            *(
                call_tool(source, SEARCH_TOOL, {
                    "query": query,
                    "max_results": getattr(retriever_settings, f"{source}_max_results", 20),
                })
                for source in DEFAULT_SOURCES
            ),
            return_exceptions=True,
        )
        papers: list[dict] = []
        counts: dict[str, int] = {}
        for source, result in zip(DEFAULT_SOURCES, results):
            if isinstance(result, BaseException):
                logger.warning("MCP source %s failed: %s", source, result)
                result = []
            counts[source] = len(result)
            papers.extend(result)
        return papers, counts

    async def _analyze_literature(self, papers: list[dict], query: str, scope: str, kb_id: str = "") -> tuple[list[dict], list[dict]]:
        """Contradiction detection + research-gap identification (both degrade to []).

        ``kb_id`` 限定空白检测的图谱范围：研究空白只在所属领域包内统计，
        其他领域的稀疏实体不会稀释本领域结论。
        """
        analyzer = LiteratureAnalyzer()
        conflicts = await analyzer.detect_conflicts(papers, query)
        sparse_entities: list[dict] = []
        try:
            gs = await get_graph_store()
            kwargs = {"kb_id": kb_id} if kb_id else {}
            sparse_entities = await gs.find_sparse_entities(scope=scope or None, limit=30, **kwargs)
        except Exception as e:
            logger.warning("Sparse entity query degraded (gaps use limitations only): %s", e)
        gaps = await analyzer.identify_gaps(papers, sparse_entities, query)
        return conflicts, gaps

    async def _link_graph(self, papers: list[dict], kb_id: str = "") -> dict:
        """把检索结果与全局图谱关联（规格④）：只读，绝不写图。

        图写入只发生在 kg_build 节点（RetrieverAgent 保持检索职责边界）。
        返回每篇论文的确定性 paper_id（KG Paper 节点身份，可供前端深链）
        以及已存在于图谱中的篇数——跨会话积累（规格⑦）的复用信号。
        paper_id 与图谱写入端使用同一 kb 前缀；真实领域包内再按包判定积累篇数。
        """
        links = []
        for paper in papers[:10]:
            links.append({"title": paper.get("title", ""), **paper_identity(paper, kb_id)})
        known = 0
        try:
            gs = await get_graph_store()
            kwargs = {"kb_id": kb_id} if kb_id and kb_id != "default" else {}
            found = await asyncio.wait_for(
                gs.papers_existing([l["paper_id"] for l in links if l["paper_id"]], **kwargs),
                timeout=3,
            )
            known = len(found)
        except Exception as e:
            logger.warning("Graph association degraded: %s", e)
        return {"papers": links, "known_in_graph": known}

    async def _execute_impl(self, state: dict) -> AgentResult:
        query = state.get("user_query", state.get("research_topic", ""))
        if not query:
            return AgentResult(success=False, error="No query provided", confidence=0.0)
        topic = (state.get("research_topic") or "").strip()
        kb_id = str(state.get("kb_id") or "default")
        candidates = await self._build_search_candidates(topic, query)
        self._audit("search_start", {"query": query, "candidates": candidates, "sources": list(DEFAULT_SOURCES)})
        raw_papers, counts = [], {}
        used_query = candidates[0]
        # 蒸馏语句优先；全空时依次回退（最多两次检索，避免恶意拖延）
        for candidate in candidates[:2]:
            raw_papers, counts = await self._search_all_sources(candidate)
            used_query = candidate
            if raw_papers:
                break
        all_p = merge_dedup(raw_papers)
        self._audit("search_complete", {**counts, "deduped": len(all_p), "used_query": used_query})

        conflicts, gaps = [], []
        if all_p:
            all_p = await PaperEnricher().enrich_papers(all_p, state.get("research_topic") or query)
            conflicts, gaps = await self._analyze_literature(all_p, query, state.get("session_id", ""), kb_id)

        citations = []
        if all_p:
            try:
                vs = get_vector_store()
                # scope=kb_id：论文按「领域包」隔离索引并按包跨会话沉淀（单领域
                # 深耕），其他领域包的论文不会混入本包的 RAG 上下文。
                # 只编码相关度最高的前 N 篇并截断正文：BGE-M3 在 CPU 上是整条问答
                # 链最慢的一段（39 篇全量编码可达 4 分钟），RAG 只读 top-k，
                # 索引前 N 篇即可保证质量并把首问延迟压到可接受区间。
                index_docs = [
                    {**p, "text": (p.get("text") or "")[:EMBED_TEXT_LIMIT], "scope": kb_id, "kb_id": kb_id}
                    for p in all_p[: get_settings().retriever.index_max_papers]
                ]
                vs.add_documents(index_docs)
                vs.save(get_settings().retriever.index_path)  # 索引落盘，重启后仍可检索
                self._audit("index_complete", {"count": len(index_docs), "total": len(all_p), "scope": kb_id})
            except Exception as e:
                logger.warning("Vector store indexing degraded: %s", e)
        for p in all_p[:10]:
            citations.append({"index": len(citations)+1, "title": p.get("title",""), "url": p.get("url",""), "authors": p.get("authors",[]), "year": p.get("year",0), "source": p.get("source","")})
        kg_links = await self._link_graph(all_p, kb_id) if all_p else {"papers": [], "known_in_graph": 0}
        self._audit("graph_link", {"linked": len(kg_links["papers"]), "known_in_graph": kg_links["known_in_graph"]})
        return AgentResult(success=True, data={"papers": all_p, "count": len(all_p), "conflicts": conflicts, "gaps": gaps, "kg_links": kg_links}, citations=citations, confidence=min(1.0, len(all_p)/20.0) if all_p else 0.3)