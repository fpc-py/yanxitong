"""Supervisor Agent v2.0 — enhanced with Phase 4 quality gate integration."""

import json, logging
from src.agents.base import BaseAgent, AgentResult
from src.knowledge.graphrag import get_graphrag
from src.safety.hallucination import get_hallucination_defense, HallucinationReport
from src.safety.citation import get_citation_tracker

logger = logging.getLogger(__name__)

# 字面量 JSON 花括号需转义为 {{ }}，否则 str.format() 会把它当占位符而抛 KeyError，
# 导致意图识别每次都静默退化为默认值。
INTENT_PROMPT = """Analyze the user query and determine intent type. Return JSON: {{"intent":"literature_search|knowledge_graph_query|data_analysis|writing_review|comprehensive","confidence":0.9}}
Query: {query}"""

QA_PROMPT = """You are a research assistant. Answer based ONLY on the provided literature. Cite every claim with [1],[2] etc. If unsure, clearly state so. Be academic and concise.

Literature:
{context}

Question: {query}

Answer:"""


class SupervisorAgent(BaseAgent):
    name = "supervisor"
    description = "总协调智能体 v2.0 — 意图识别 + Phase 4 六道幻觉防线质量门禁"
    model_role = "supervisor"

    async def _execute_impl(self, state: dict) -> AgentResult:
        query = state.get("user_query", "")
        if not query:
            return AgentResult(success=False, error="No query", confidence=0.0)

        self._audit("supervisor_start", {"query": query[:200]})

        # Intent analysis
        try:
            resp = await self._call_llm(INTENT_PROMPT.format(query=query))
            resp = resp.strip()
            if resp.startswith("```"):
                resp = resp.split("\n", 1)[-1]
                if resp.endswith("```"):
                    resp = resp[:-3]
                resp = resp.strip()
            intent = json.loads(resp)
        except Exception:
            intent = {"intent": "comprehensive", "confidence": 0.5}
        self._audit("intent", intent)

        papers = state.get("literature_results", [])
        rag = {}
        if not papers:
            # 检索为空时不能返回静默空串：supervisor 是图的终点节点，
            # 必须给用户一个明确的降级提示，避免前端出现“空响应”。
            return AgentResult(
                success=True,
                data={
                    "intent": intent,
                    "action": "retrieve",
                    "query": query,
                    "answer": "本次未检索到相关文献（arXiv / Semantic Scholar 均无匹配结果）。\n\n建议：改用更具体的英文关键词（这两个库以英文文献为主），或调整研究主题后重试。",
                },
                confidence=0.8,
            )

        # GraphRAG context
        try:
            graphrag = await get_graphrag()
            # scope=session_id：检索限定在本研究问题（会话）自己的论文索引与图谱子图上
            rag = await graphrag.query(query, scope=state.get("session_id"))
            ctx = rag.get("fused_context", "")
            citations = rag.get("citations", [])
        except Exception as e:
            logger.warning("GraphRAG degraded: %s", e)
            ctx = "\n\n".join(f"[{i+1}] {p.get('title','')}: {p.get('abstract','')[:300]}" for i, p in enumerate(papers[:5]))
            citations = [{"index": i+1, "title": p.get("title",""), "url": p.get("url",""), "year": p.get("year",0)} for i, p in enumerate(papers[:5])]

        # Generate answer
        answer = await self._call_llm(QA_PROMPT.format(context=ctx[:8000], query=query))
        self._audit("answer_generated", {"length": len(answer)})

        # ---- Phase 4: Six-layer hallucination defense quality gate ----
        defense_report = None
        try:
            defense = get_hallucination_defense()
            kg_entities = rag.get("kg_entities", [])
            defense_report = await defense.evaluate(answer, papers, kg_entities)
            confidence = defense_report.overall_confidence
            hr = defense_report.requires_human_review
            risk = defense_report.risk_level

            self._audit("quality_gate_v4", {
                "confidence": confidence,
                "risk_level": risk,
                "human_review": hr,
                "layers_passed": sum(1 for l in defense_report.layer_results.values() if l.passed),
                "total_layers": len(defense_report.layer_results),
            })

            # If high risk, prepend warning to answer
            if risk in ("high", "critical"):
                answer = f"⚠️ [风险等级: {risk}] 以下回答需要人工审核:\n\n{answer}"
                hr = True

        except Exception as e:
            logger.warning("Hallucination defense degraded: %s", e)
            confidence = 0.7
            hr = False
            risk = "unknown"

        # Citation chain
        tracker = get_citation_tracker()
        chain = tracker.build_chain(answer, papers)

        # Build final data payload
        result_data = {
            "answer": answer,
            "intent": intent,
            "confidence": confidence,
            "human_review_required": hr,
            "risk_level": risk,
            "citation_chain": chain.to_dict(),
            "quality_report": {
                "overall_confidence": confidence,
                "risk_level": risk,
                "layer_results": {
                    name: {"score": lr.score, "passed": lr.passed, "details": lr.details}
                    for name, lr in defense_report.layer_results.items()
                } if defense_report else {},
                "summary": defense_report.summary if defense_report else "Defense unavailable",
            },
        }

        return AgentResult(
            success=True,
            data=result_data,
            citations=citations,
            confidence=confidence,
        )