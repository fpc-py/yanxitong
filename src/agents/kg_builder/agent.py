"""KG Builder - 知识图谱构建智能体（本体驱动、证据锚定、增量融合）.

Two layers, both writing through :class:`~src.knowledge.graph_store.GraphStore`:

1. **Deterministic edges** — derived from the paper's already-quote-verified
   enrichment (six fields from :mod:`src.agents.retriever.enricher`), so the
   backbone of the graph costs zero extra LLM calls:
   ``Paper-PROPOSES->Method``, ``Method-COMPARES_WITH->Method`` (baseline),
   ``Paper-USES_DATASET->Dataset`` (sample size on the edge),
   ``Paper-USES_METRIC->Metric`` (value/unit on the edge),
   ``Paper-APPLIED_TO->ResearchProblem``.
2. **LLM relation pass** — one call per paper (budget ``kg.max_papers``,
   ``kg.concurrency``) that supplements inter-entity relations. The prompt is
   generated from the configured schema and every payload is gated against it
   in code, so unknown labels/relation types can never reach Neo4j. Relations
   whose evidence quote fails server-side verification are kept but forced
   into the human-review queue.

Entity ids / paper ids / edge keys are deterministic (see graph_store), which
is what makes MERGE idempotent and lets the graph accumulate across sessions
— the team-level paper fact base.
"""

import asyncio, logging
from src.agents.base import BaseAgent, AgentResult, parse_llm_json
from src.analysis.evidence import source_text_of, verify_quote
from src.core.config import get_settings
from src.knowledge.graph_store import (
    get_graph_store,
    make_edge_key,
    make_entity_id,
)
from src.tools.paper_schema import paper_identity

logger = logging.getLogger(__name__)

ABSTRACT_LIMIT = 1200

# 无对应关系类型可连边的实体类型在 LLM 通道中丢弃，避免产生孤立节点
# （孤立节点会污染稀疏实体/研究空白分析）。schema 中保留 Author/Venue，
# 待 AUTHORED/PUBLISHED_IN 等关系进入白名单后再启用。
UNLINKED_ENTITY_TYPES = {"Author", "Venue"}

# LLM 抽到的实体也要挂到论文上，按类型选择关系（缺失会导致孤立节点）
PAPER_LINK_RELATIONS = {
    "ResearchProblem": "APPLIED_TO",
    "Method": "PROPOSES",
    "Model": "PROPOSES",
    "Dataset": "USES_DATASET",
    "Metric": "USES_METRIC",
    "Finding": "PROPOSES",
}


def entity_prompt_template() -> str:
    """Extraction prompt generated from the configured schema (single source)."""
    kg = get_settings().kg
    entity_types = "|".join(kg.entity_types)
    relation_types = "|".join(kg.relation_types)
    return (
        "Extract entities and relations from the paper abstract.\n"
        'Return JSON: {{"entities":[{{"id":"uid","type":"' + entity_types + '","name":"name"}}],'
        '"relations":[{{"source_id":"uid","target_id":"uid","type":"' + relation_types + '","evidence":"逐字引文"}}]}}\n'
        "Rules: use only the listed types; every relation evidence must be a verbatim "
        "quote from the abstract (never paraphrase or translate).\n"
        "Paper: {title}\nAbstract: {abstract}"
    )


def schema_entity_type(raw) -> str:
    """Canonical schema entity type (case-insensitive), or '' when unknown."""
    value = str(raw or "").strip().lower()
    for allowed in get_settings().kg.entity_types:
        if allowed.lower() == value:
            return allowed
    return ""


def schema_relation_type(raw) -> str:
    """Canonical schema relation type (case-insensitive), or '' when unknown."""
    value = str(raw or "").strip().lower()
    for allowed in get_settings().kg.relation_types:
        if allowed.lower() == value:
            return allowed
    return ""


def _edge(source_id: str, target_id: str, rtype: str, evidence: str = "", evidence_source: str = "",
          force_review: bool = False, **props) -> dict:
    properties = {k: v for k, v in props.items() if v not in ("", None, [])}
    if evidence:
        properties["evidence"] = evidence
    if evidence_source:
        properties["evidence_source"] = evidence_source
    return {
        "source_id": source_id,
        "target_id": target_id,
        "type": rtype,
        "edge_key": make_edge_key(source_id, rtype, target_id),
        "properties": properties,
        "force_review": force_review,
    }


def _proofed_edge(source_id: str, target_id: str, rtype: str, evidence: str = "", **props) -> dict:
    """Edge whose evidence comes from the enricher's verified quotes."""
    return _edge(source_id, target_id, rtype, evidence=evidence, evidence_source="abstract", **props)


def build_deterministic_graph(papers: list[dict], kb_id: str = "") -> dict:
    """Derive Paper/Entity nodes and evidence-anchored edges from enrichment.

    Only quote-verified enrichment items are used (the enricher drops
    unverifiable ones before they reach this point), so every deterministic
    edge carries a verbatim evidence span from the paper's source text.
    ``kb_id``（领域包）通过确定性 id 实现实例隔离：实包 id 内嵌包前缀，
    空/默认包保持遗留形态（升级零迁移）。
    """
    paper_rows: list[dict] = []
    entities: dict[str, dict] = {}
    edges: list[dict] = []
    max_entities = get_settings().kg.max_entities_per_doc

    def add_entity(name: str, etype: str) -> str:
        eid = make_entity_id(name, kb_id)
        entities.setdefault(eid, {"entity_id": eid, "name": name.strip(), "type": etype})
        return eid

    def quote_of(evidence: dict, field: str, index: int) -> str:
        quotes = evidence.get(field) or []
        return quotes[index].get("quote", "") if index < len(quotes) else ""

    for paper in papers:
        ident = paper_identity(paper, kb_id)
        pid = ident["paper_id"]
        if not pid:
            continue
        row = {
            "paper_id": pid,
            "title": paper.get("title", ""),
            "year": paper.get("year", 0),
            "authors": paper.get("authors", []),
            "venue": paper.get("venue", ""),
            "arxiv_id": ident["arxiv_id"],
            "doi": paper.get("doi", ""),
            "url": paper.get("url", ""),
            "title_hash": ident["title_hash"],
            "abstract": paper.get("abstract", ""),
            "source": paper.get("source", ""),
            "citations_count": paper.get("citations_count", 0),
        }
        enrichment = paper.get("enrichment") or {}
        evidence = enrichment.get("evidence") or {}
        limitations = enrichment.get("limitations") or []
        if limitations:
            row["limitations"] = [str(x) for x in limitations][:5]
        paper_rows.append(row)

        created_here = 0
        for i, method in enumerate(enrichment.get("methods") or []):
            name = (method.get("name") or "").strip()
            if not name or created_here >= max_entities:
                continue
            quote = quote_of(evidence, "methods", i)
            method_id = add_entity(name, "Method")
            created_here += 1
            edges.append(_proofed_edge(pid, method_id, "PROPOSES", evidence=quote))
            baseline = (method.get("baseline") or "").strip()
            if baseline:
                baseline_id = add_entity(baseline, "Method")
                created_here += 1
                edges.append(_proofed_edge(method_id, baseline_id, "COMPARES_WITH", evidence=quote))
        for i, dataset in enumerate(enrichment.get("datasets") or []):
            name = (dataset.get("name") or "").strip()
            if not name or created_here >= max_entities:
                continue
            quote = quote_of(evidence, "datasets", i)
            dataset_id = add_entity(name, "Dataset")
            created_here += 1
            edges.append(_proofed_edge(pid, dataset_id, "USES_DATASET", evidence=quote, n=dataset.get("sample_size", "")))
        for i, result in enumerate(enrichment.get("results") or []):
            metric = (result.get("metric") or "").strip()
            if not metric or created_here >= max_entities:
                continue
            quote = quote_of(evidence, "results", i)
            metric_id = add_entity(metric, "Metric")
            created_here += 1
            edges.append(_proofed_edge(pid, metric_id, "USES_METRIC", evidence=quote,
                               value=result.get("value", ""), unit=result.get("unit", "")))
        problem = (enrichment.get("research_problem") or "").strip()
        if problem and created_here < max_entities:
            quote = quote_of(evidence, "research_problem", 0)
            problem_id = add_entity(problem, "ResearchProblem")
            created_here += 1
            edges.append(_proofed_edge(pid, problem_id, "APPLIED_TO", evidence=quote))

    return {"papers": paper_rows, "entities": list(entities.values()), "edges": edges}


class KGBuilderAgent(BaseAgent):
    name = "kg_builder"
    description = "知识图谱构建智能体"
    model_role = "lightweight"

    @staticmethod
    def _parse_json(resp: str) -> dict:
        """解析模型返回的 JSON；容忍代码围栏与前后夹杂的说明文字。"""
        return parse_llm_json(resp, raise_on_error=True)

    async def _extract_one(self, paper: dict, sem: asyncio.Semaphore) -> dict:
        """抽取单篇论文的实体/关系；无有效摘要时返回空结果。"""
        async with sem:
            source_text = source_text_of(paper)
            if len(source_text) < 80:
                return {}
            prompt = entity_prompt_template().format(
                title=paper.get("title", "Untitled"),
                abstract=source_text[:ABSTRACT_LIMIT],
            )
            # 开启 JSON 模式，避免模型在 JSON 前后夹带说明文字导致解析失败
            return self._parse_json(await self._call_llm(prompt, json_mode=True))

    def _gate_extraction(
        self, raw: dict, source_text: str, paper_id: str, kb_id: str = ""
    ) -> tuple[list[dict], list[dict], dict]:
        """把 LLM 输出收敛到封闭 schema，并生成证据锚定的边。

        返回 (entities, edges, dropped)。实体类型/关系类型不在白名单、
        关系端点悬空 ⇒ 丢弃；引文校验失败的实体间关系保留但强制进入人工复核。
        ``kb_id`` 透传到确定性 id（实包实体与默认包实例隔离）。
        """
        entities: list[dict] = []
        edges: list[dict] = []
        dropped = {"entity_types": 0, "relations": 0, "unlinked_types": 0}
        max_entities = get_settings().kg.max_entities_per_doc

        valid_uids: dict[str, str] = {}
        for ent in (raw.get("entities") or [])[:max_entities]:
            etype = schema_entity_type(ent.get("type"))
            name = str(ent.get("name") or "").strip()
            if not etype or not name:
                dropped["entity_types"] += 1
                continue
            if etype in UNLINKED_ENTITY_TYPES:
                dropped["unlinked_types"] += 1
                continue
            eid = make_entity_id(name, kb_id)
            valid_uids[str(ent.get("id"))] = eid
            entities.append({"entity_id": eid, "name": name, "type": etype})
            link_type = PAPER_LINK_RELATIONS.get(etype)
            if link_type and paper_id:
                edges.append(_edge(paper_id, eid, link_type))

        for rel in raw.get("relations") or []:
            rtype = schema_relation_type(rel.get("type"))
            source_id = valid_uids.get(str(rel.get("source_id")))
            target_id = valid_uids.get(str(rel.get("target_id")))
            if not rtype or not source_id or not target_id:
                dropped["relations"] += 1
                continue
            quote = str(rel.get("evidence") or "").strip()
            verified = bool(quote) and verify_quote(quote, source_text)
            edges.append(_edge(
                source_id, target_id, rtype,
                evidence=quote if verified else "",
                evidence_source="abstract" if verified else "unverified",
                force_review=not verified,
            ))
        return entities, edges, dropped

    async def _llm_relations(self, papers: list[dict], kb_id: str = "") -> tuple[list[dict], list[dict], dict]:
        """LLM 关系补充通道：配额与并发由配置控制，全程封闭 schema 约束。"""
        settings = get_settings().kg
        targets = papers[: max(1, settings.max_papers)]
        sem = asyncio.Semaphore(max(1, settings.concurrency))
        outcomes = await asyncio.gather(
            *(self._extract_one(p, sem) for p in targets), return_exceptions=True
        )
        entities: list[dict] = []
        edges: list[dict] = []
        dropped = {"entity_types": 0, "relations": 0, "unlinked_types": 0}
        processed = 0
        for paper, outcome in zip(targets, outcomes):
            if isinstance(outcome, BaseException) or not outcome:
                continue
            ident = paper_identity(paper, kb_id)
            paper_entities, paper_edges, paper_dropped = self._gate_extraction(
                outcome, source_text_of(paper), ident["paper_id"], kb_id
            )
            entities.extend(paper_entities)
            edges.extend(paper_edges)
            for key, value in paper_dropped.items():
                dropped[key] += value
            processed += 1
        dropped["processed"] = processed
        return entities, edges, dropped

    async def _execute_impl(self, state: dict) -> AgentResult:
        papers = state.get("literature_results", [])
        if not papers:
            return AgentResult(success=False, error="No papers in literature_results", confidence=0.0)
        settings = get_settings().kg
        self._audit("kg_start", {"paper_count": len(papers), "build_enabled": settings.build_enabled})
        if not settings.build_enabled:
            return AgentResult(success=True, data={"skipped": True, "reason": "kg.build_enabled=false"}, confidence=1.0)

        session_id = state.get("session_id", "")
        kb_id = str(state.get("kb_id") or "default")
        pack = kb_id != "default"
        deterministic = build_deterministic_graph(papers, kb_id=kb_id)
        entities = deterministic["entities"]
        edges = deterministic["edges"]
        dropped: dict = {}
        if settings.llm_relation_pass:
            llm_entities, llm_edges, dropped = await self._llm_relations(papers, kb_id=kb_id)
            entities.extend(llm_entities)
            edges.extend(llm_edges)

        # 同 id 实体去重（确定性通道与 LLM 通道可能同时产出）
        unique_entities = {e["entity_id"]: e for e in entities}
        entities = list(unique_entities.values())

        stats = {
            "papers": len(deterministic["papers"]),
            "entities": len(entities),
            "relations": len(edges),
            "dropped": dropped,
            "kb_id": kb_id,
            "degraded": False,
        }
        try:
            gs = await get_graph_store()
            if pack:  # 实包：写入 kb_id 属性（默认包走遗留形态，属性由 store 兜底）
                await gs.upsert_papers(deterministic["papers"], kb_id=kb_id)
                await gs.upsert_entities(entities, session_id=session_id, kb_id=kb_id)
                await gs.upsert_edges(edges, session_id=session_id, kb_id=kb_id)
            else:
                await gs.upsert_papers(deterministic["papers"])
                await gs.upsert_entities(entities, session_id=session_id)
                await gs.upsert_edges(edges, session_id=session_id)
            await gs.link_session(session_id, [p["paper_id"] for p in deterministic["papers"]])
        except Exception as e:
            logger.warning("Neo4j storage degraded: %s", e)
            stats["degraded"] = True
        self._audit("kg_done", stats)
        attempted = max(1, len(deterministic["papers"]))
        return AgentResult(success=True, data=stats, confidence=min(1.0, attempted / max(1, len(papers))))
