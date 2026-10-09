"""Supervisor Agent v3.0 — GraphRAG answer generation + six-layer quality gate."""

import logging
from src.agents.base import BaseAgent, AgentResult
from src.knowledge.graphrag import get_graphrag
from src.safety.hallucination import get_hallucination_defense
from src.safety.citation import get_citation_tracker
from src.safety.guard import get_input_guard

logger = logging.getLogger(__name__)

QA_PROMPT = """You are a research assistant. Answer based ONLY on the provided literature (papers and uploaded knowledge-base excerpts labeled [KB:...]). Cite every claim with [1],[2] etc. If unsure, clearly state so. Be academic and concise.

Literature:
{context}

Question: {query}

Answer:"""


class SupervisorAgent(BaseAgent):
    name = "supervisor"
    description = "总协调智能体 v3.0 — GraphRAG 答案生成 + 六道幻觉防线质量门禁"
    model_role = "supervisor"

    async def _execute_impl(self, state: dict) -> AgentResult:
        query = state.get("user_query", "")
        if not query:
            return AgentResult(success=False, error="No query", confidence=0.0)

        self._audit("supervisor_start", {"query": query[:200]})

        # 意图分类已在入口 route_intent 完成（LLM 或关键词回退），这里只读不重复调用。
        intent = {
            "intent": state.get("intent") or "literature_search",
            "confidence": state.get("intent_confidence", 0.0),
        }
        self._audit("intent", intent)

        # ---- 输入安全门禁：危险/越界请求直接拒答，不进 RAG ----
        # 有索引后即使是危险问题也可能召回"相关"论文，不能靠 papers 是否为空判拒答。
        guard = get_input_guard().check(query)
        if not guard.passed and guard.risk_level in ("critical", "high"):
            self._audit("blocked_by_guard", {"reason": guard.blocked_reason, "cats": guard.categories_triggered})
            return AgentResult(
                success=True,
                data={
                    "intent": "blocked",
                    "action": "refuse",
                    "query": query,
                    "answer": (
                        "这个问题超出了本系统的服务范围。本系统是学术文献助手，"
                        "不提供危险操作、非法行为或有害内容的指导。"
                        "如果你有文献调研、论文解读、科研方法相关的问题，欢迎继续提问。"
                    ),
                },
                confidence=0.95,
            )

        papers = state.get("literature_results", [])

        # GraphRAG context —— 无论是否有论文都要查：知识库双区可能命中上传文档，
        # 只有「论文与 KB 均为空」才走未检索到的降级提示。
        rag: dict = {}
        ctx, citations, evidence_docs = "", [], []
        try:
            graphrag = await get_graphrag()
            # scope=session_id：检索限定在本研究问题（会话）自己的图谱子图上；
            # kb_id：领域包分区（FAISS/图谱按包过滤，跨域需显式声明）；
            # user_id 启用知识库双区合并（kb:team 共享 + kb:{user_id} 私有）
            query_kwargs: dict = {
                "scope": state.get("session_id"),
                "user_id": state.get("user_id", ""),
            }
            if state.get("kb_id"):
                query_kwargs["kb_id"] = state["kb_id"]
            if state.get("cross_kb_ids"):
                query_kwargs["cross_kb_ids"] = state["cross_kb_ids"]
            rag = await graphrag.query(query, **query_kwargs)
            ctx = rag.get("fused_context", "")
            citations = rag.get("citations", [])
            evidence_docs = papers + rag.get("kb_docs", [])
        except Exception as e:
            logger.warning("GraphRAG degraded: %s", e)
            ctx = "\n\n".join(f"[{i+1}] {p.get('title','')}: {p.get('abstract','')[:300]}" for i, p in enumerate(papers[:5]))
            citations = [{"index": i+1, "title": p.get("title",""), "url": p.get("url",""), "year": p.get("year",0)} for i, p in enumerate(papers[:5])]
            evidence_docs = papers

        if not papers and not rag.get("kb_docs"):
            # 检索与知识库均为空时不能返回静默空串：supervisor 是图的终点节点，
            # 必须给用户一个明确的降级提示，避免前端出现“空响应”。
            return AgentResult(
                success=True,
                data={
                    "intent": intent,
                    "action": "retrieve",
                    "query": query,
                    "answer": "本次未检索到相关文献，本地知识库也没有可用片段。\n\n建议：改用更具体的英文关键词（arXiv / Semantic Scholar / OpenAlex 以英文文献为主），或上传相关 PDF 到知识库后重试。",
                },
                confidence=0.8,
            )

        # Generate answer
        answer = await self._call_llm(QA_PROMPT.format(context=ctx[:8000], query=query))
        self._audit("answer_generated", {"length": len(answer)})

        # ---- 三元组反查（规格③核心）: (方法,数据集,指标) → 知识图谱 --------
        triple_report = None
        try:
            from src.safety.triple_check import get_triple_verifier

            verify_kwargs = {"kb_id": state["kb_id"]} if state.get("kb_id") else {}
            triple_report = await get_triple_verifier().verify_answer(answer, **verify_kwargs)
        except Exception as e:
            logger.warning("Triple check degraded: %s", e)

        # ---- Phase 4: Six-layer hallucination defense quality gate ----
        defense_report = None
        try:
            defense = get_hallucination_defense()
            kg_entities = rag.get("kg_entities", [])
            defense_report = await defense.evaluate(answer, evidence_docs, kg_entities, triple_report=triple_report)
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

            # 落库：指标 + 审计存储（推理轨迹/幻觉标记面板的数据源）
            from src.observability.metrics import track_hallucination_flag
            from src.observability.audit_store import record_hallucination_flag

            sid = state.get("session_id", "")
            for flag in (triple_report or {}).get("flags", [])[:5]:
                track_hallucination_flag("triple_check", flag.get("state", "unknown"))
                record_hallucination_flag(sid, "triple_check", flag.get("state", "unknown"), flag)
            if risk in ("high", "critical"):
                track_hallucination_flag("hallucination_defense", risk)
                record_hallucination_flag(sid, "hallucination_defense", risk, {
                    "summary": defense_report.summary,
                    "flagged_claims": defense_report.flagged_claims[:5],
                })

        except Exception as e:
            logger.warning("Hallucination defense degraded: %s", e)
            confidence = 0.7
            hr = False
            risk = "unknown"

        # Citation chain
        tracker = get_citation_tracker()
        chain = tracker.build_chain(answer, evidence_docs)

        # Build final data payload
        result_data = {
            "answer": answer,
            "intent": intent,
            "confidence": confidence,
            "human_review_required": hr,
            "risk_level": risk,
            "citation_chain": chain.to_dict(),
            "triple_report": triple_report,
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