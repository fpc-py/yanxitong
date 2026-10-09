# Workflow definitions v3.0 — supervisor graph with full 5-link chain: find→read→compute→write→review.

import asyncio, json, logging
from typing import Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, SystemMessage

from src.workflows.state import ResearchState
from src.workflows import tracing
from src.agents.retriever.agent import RetrieverAgent
from src.agents.kg_builder.agent import KGBuilderAgent
from src.agents.supervisor.agent import SupervisorAgent
from src.agents.data_analyst.agent import DataAnalystAgent
from src.agents.academic_reviewer.agent import AcademicReviewerAgent
from src.agents.experiment_designer.agent import ExperimentDesignerAgent
from src.agents.writing_assistant.agent import WritingAssistantAgent

logger = logging.getLogger(__name__)

# 显式端点阶段 → 入口节点映射（这些是 /analyze、/design、/write、/review 等
# 独立端点直接设定的 phase，意图明确，不经过 LLM 分类）。
_EXPLICIT_PHASE_ENTRY = {
    "data_analysis": "data_analyst",
    "design": "experiment_designer",
    "review": "academic_reviewer",
    "writing": "writing",
}

# LLM 意图分类标签 → 入口节点
_INTENT_TO_NODE = {
    "literature_search": "retrieve",
    "data_analysis": "data_analyst",
    "experiment_design": "experiment_designer",
    "writing": "writing",
    "review": "academic_reviewer",
}

_INTENT_PROMPT = """You are an intent classifier for an academic research assistant.
Classify the user query into exactly ONE category:
- literature_search: asking to search / summarize / compare papers, literature landscape
- data_analysis: asking to analyze a dataset, produce charts / statistics from data
- experiment_design: asking to design / optimize an experiment or hyperparameters
- writing: asking to draft / write a paper section
- review: asking to review / critique an existing draft
Reply ONLY JSON: {"intent": "<one of the above>", "confidence": 0.0~1.0}
Query: """


async def _llm_classify_intent(query: str) -> tuple[str | None, float]:
    """Lightweight LLM intent classification; (None, 0.0) on any failure.

    Uses the lightweight model role with hidden-thinking disabled (the model
    is a reasoning model that would otherwise burn the whole token budget on
    internal CoT).
    """
    if not query or len(query.strip()) < 2:
        return None, 0.0
    try:
        from src.core.llm_factory import get_llm

        llm = get_llm("lightweight").bind(
            response_format={"type": "json_object"},
            extra_body={"enable_thinking": False},
        )
        resp = await llm.ainvoke([
            SystemMessage(content="You are a fast intent classifier. Reply JSON only."),
            HumanMessage(content=_INTENT_PROMPT + query[:400]),
        ])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        data = json.loads(text.strip().strip("`").strip())
        intent = str(data.get("intent", "")).strip()
        conf = float(data.get("confidence", 0.0))
        if intent in _INTENT_TO_NODE:
            return intent, conf
    except Exception as e:
        logger.warning("LLM intent classification degraded to keywords: %s", e)
    return None, 0.0


def _keyword_intent(query: str) -> str:
    """Legacy keyword fallback (kept as the low-confidence / error path)."""
    if any(w in query for w in ["写论文", "生成论文", "撰写", "draft", "写作", "写摘要", "写引言"]):
        return "writing"
    if any(w in query for w in ["审稿", "审阅", "修改论文", "论文评审", "review", "评审"]):
        return "review"
    if any(w in query for w in ["分析数据", "数据分析", "统计", "csv", "图表", "可视化", "analyze"]):
        return "data_analysis"
    if any(w in query for w in ["实验设计", "实验方案", "假设", "验证方案", "experiment design"]):
        return "experiment_design"
    return "literature_search"


async def retrieve_node(state: ResearchState) -> ResearchState:
    # 记录最近一次运行，供 /api/system/capabilities 展示全链路追踪标识
    tracing.LAST_TRACE_ID = state["session_id"]
    agent = RetrieverAgent()
    result = await agent.execute(state)
    if result.success and result.data:
        state["literature_results"] = result.data.get("papers", [])
        state["literature_conflicts"] = result.data.get("conflicts", [])
        state["research_gaps"] = result.data.get("gaps", [])
        if result.citations:
            state["citation_chain"].extend(result.citations)
        state["confidence_scores"]["retriever"] = result.confidence
    else:
        state["error_message"] = result.error
    return state


async def kg_build_node(state: ResearchState) -> ResearchState:
    if not state.get("literature_results"):
        return state
    agent = KGBuilderAgent()
    result = await agent.execute(state)
    if result.success:
        state["confidence_scores"]["kg_builder"] = result.confidence
        stats = dict(result.data or {})
        # 建图后重算会话视图内的稀疏实体（研究空白候选），供洞察展示
        try:
            from src.knowledge.graph_store import get_graph_store
            gs = await get_graph_store()
            kb_kwargs = {"kb_id": state["kb_id"]} if state.get("kb_id") else {}
            stats["sparse_entities"] = (await gs.find_sparse_entities(
                scope=state.get("session_id") or None, limit=10, **kb_kwargs
            ))[:5]
        except Exception as e:
            logger.warning("Sparse entity query degraded: %s", e)
        state["kg_stats"] = stats
        # 审计日志（推理轨迹/审计面板数据源）：建图统计一次性落库
        try:
            from src.observability.audit_store import record_audit

            record_audit(
                state.get("session_id", ""),
                "kg_builder",
                "build_complete",
                {k: stats.get(k) for k in ("papers", "entities", "relations", "dropped", "degraded")},
            )
        except Exception as e:
            logger.debug("Audit write degraded: %s", e)
        # 规格⑤：建图后产出路线图洞察（时间线/演进链/矛盾/空白），写回 state
        try:
            from src.analysis.roadmap import RoadmapBuilder

            state["research_roadmap"] = await asyncio.wait_for(
                RoadmapBuilder().build(
                    scope=state.get("session_id") or None,
                    kb_id=state.get("kb_id") or None,
                ),
                timeout=6,
            )
        except Exception as e:
            logger.warning("Roadmap degraded: %s", e)
    else:
        logger.warning("KG Builder degraded: %s", result.error)
    return state


async def supervisor_node(state: ResearchState) -> ResearchState:
    agent = SupervisorAgent()
    result = await agent.execute(state)
    if result.success and result.data:
        state["final_response"] = result.data.get("answer", "")
        state["confidence_scores"]["supervisor"] = result.confidence
        state["human_review_required"] = result.data.get("human_review_required", False)
        if "citation_chain" in result.data:
            state["citation_chain"] = result.data["citation_chain"].get("claims", [])
        if "triple_report" in result.data:
            state["triple_report"] = result.data["triple_report"]
    else:
        state["error_message"] = result.error
        state["final_response"] = f"Error: {result.error}"
    return state


def analysis_answer(exp: dict | None) -> str:
    """解释报告优先；缺失时逐级回退（stdout → 图表提示 → stderr）。"""
    exp = exp or {}
    report = (exp.get("report") or "").strip()
    stdout = (exp.get("stdout") or "").strip()
    if report:
        return report
    if stdout:
        return stdout
    if exp.get("figures"):
        return "沙箱已执行分析并生成图表，但生成的代码没有输出文字结论。图表见下方。"
    return (exp.get("stderr") or "").strip() or "分析已执行，但没有可展示的输出。"


async def data_analyst_node(state: ResearchState) -> ResearchState:
    agent = DataAnalystAgent()
    result = await agent.execute(state)
    if result.success:
        state["experiment_results"] = result.data
        state["confidence_scores"]["data_analyst"] = result.confidence
        # 与其他节点一致：/session、/query 的 _build_response 依赖 final_response
        state["final_response"] = analysis_answer(result.data)
    else:
        state["error_message"] = result.error
        state["final_response"] = f"Error: {result.error}"
    return state


async def experiment_designer_node(state: ResearchState) -> ResearchState:
    agent = ExperimentDesignerAgent()
    result = await agent.execute(state)
    if result.success:
        engine = result.data.get("design_engine") or {}
        state["experiment_results"] = {
            **(state.get("experiment_results") or {}),
            "design": result.data.get("experiment_design", {}),
            "design_engine": engine,
            "design_run": result.data.get("design_run", {}),
        }
        state["confidence_scores"]["experiment_designer"] = result.confidence
        # /query 路径经 _build_response 依赖 final_response：解释报告优先，回退经典渲染
        state["final_response"] = (engine.get("report") or "").strip() or _fallback_design_text(
            result.data.get("experiment_design", {})
        )
    else:
        state["error_message"] = result.error
        state["final_response"] = f"Error: {result.error}"
    return state


def _fallback_design_text(design: dict) -> str:
    """无报告时的设计答复回退（含假设/变量/统计方法字样，满足演示契约）。"""
    design = design or {}
    variables = design.get("variables") or {}
    return "\n\n".join([
        "## 实验设计方案",
        f"**假设**: {design.get('hypothesis') or 'N/A'}",
        f"**自变量**: {', '.join(variables.get('independent') or []) or 'N/A'}",
        f"**因变量**: {', '.join(variables.get('dependent') or []) or 'N/A'}",
        f"**控制变量**: {', '.join(variables.get('controlled') or []) or 'N/A'}",
        f"**统计方法**: {', '.join(design.get('statistical_methods') or []) or 'N/A'}",
        f"**推荐验证方案**: {design.get('recommended_validation') or 'N/A'}",
    ])


async def writing_node(state: ResearchState) -> ResearchState:
    agent = WritingAssistantAgent()
    result = await agent.execute(state)
    if result.success:
        state["writing_draft"] = result.data.get("content", "")
        state["confidence_scores"]["writing_assistant"] = result.confidence
        state["final_response"] = result.data.get("content", "")[:3000]
    else:
        state["error_message"] = result.error
    return state


async def academic_reviewer_node(state: ResearchState) -> ResearchState:
    agent = AcademicReviewerAgent()
    result = await agent.execute(state)
    if result.success:
        state["confidence_scores"]["academic_reviewer"] = result.confidence
        review_data = result.data.get("review", {})
        state["experiment_results"] = {
            **(state.get("experiment_results") or {}),
            "review": review_data,
        }
        state["final_response"] = review_data.get("summary", str(result.data.get("review", "")))
    else:
        state["error_message"] = result.error
    return state


# --- Routing ---

def route_after_retrieve(state: ResearchState) -> Literal["kg_build", "supervisor", "data_analyst", END]:
    if state.get("error_message"):
        return END
    if state.get("current_phase") in ("experiment",):
        return "data_analyst"
    if state.get("literature_results"):
        return "kg_build"
    return "supervisor"


def route_after_kg(state: ResearchState) -> Literal["supervisor", "data_analyst", "writing", END]:
    phase = state.get("current_phase", "")
    if phase == "experiment":
        return "data_analyst"
    if phase == "writing":
        return "writing"
    return "supervisor"


def route_after_analysis(state: ResearchState) -> Literal["experiment_designer", "writing", "supervisor", END]:
    if state.get("error_message"):
        return END
    phase = state.get("current_phase", "")
    # /analyze 只做数据分析：分析完直接结束，不再串到实验设计
    if phase == "data_analysis":
        return END
    if phase == "experiment":
        return "experiment_designer"
    if phase == "writing":
        return "writing"
    return "supervisor"


def route_after_writing(state: ResearchState) -> Literal["academic_reviewer", "supervisor", END]:
    if state.get("current_phase") == "writing":
        return "academic_reviewer"
    return "supervisor"


async def route_intent(state: ResearchState) -> Literal[
    "retrieve", "data_analyst", "experiment_designer",
    "writing", "academic_reviewer", "supervisor",
]:
    """Entry router: explicit endpoint phase wins; otherwise LLM intent with keyword fallback.

    - ``/analyze``/``/design``/``/write``/``/review`` 等独立端点直接设定 phase，
      意图明确，直达对应节点，不消耗 LLM 分类。
    - 默认 literature 阶段：先跑一次 lightweight LLM 分类；置信度 ≥0.6 直接采用，
      否则（或调用失败）回退关键词表。分类结果写回 state 供 supervisor 节点审计，
      避免重复调一次 LLM。
    """
    phase = state.get("current_phase", "")
    if phase in _EXPLICIT_PHASE_ENTRY:
        return _EXPLICIT_PHASE_ENTRY[phase]

    query = state.get("user_query", "") or ""
    intent, conf = await _llm_classify_intent(query)
    if conf >= 0.6 and intent:
        state["intent"] = intent
        state["intent_confidence"] = conf
        node = _INTENT_TO_NODE.get(intent, "retrieve")
        logger.info("Intent route (LLM): %s conf=%.2f -> %s", intent, conf, node)
        return node

    # 低置信 / 调用失败：关键词回退
    kw = _keyword_intent(query.lower())
    state["intent"] = kw
    state["intent_confidence"] = conf
    logger.info("Intent route (keyword fallback): %s", kw)
    node = _INTENT_TO_NODE.get(kw, "retrieve")
    # 已有文献结果时，非数据/设计/写作/审稿意图直接进 supervisor 做 RAG 问答
    if node == "retrieve" and state.get("literature_results"):
        return "supervisor"
    return node


def build_supervisor_graph() -> StateGraph:
    workflow = StateGraph(ResearchState)

    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("kg_build", kg_build_node)
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("data_analyst", data_analyst_node)
    workflow.add_node("experiment_designer", experiment_designer_node)
    workflow.add_node("writing", writing_node)
    workflow.add_node("academic_reviewer", academic_reviewer_node)

    workflow.set_conditional_entry_point(route_intent)

    workflow.add_conditional_edges("retrieve", route_after_retrieve)
    workflow.add_conditional_edges("kg_build", route_after_kg)
    workflow.add_conditional_edges("data_analyst", route_after_analysis)
    workflow.add_conditional_edges("experiment_designer", route_after_writing)
    workflow.add_edge("writing", "academic_reviewer")
    workflow.add_edge("academic_reviewer", END)
    workflow.add_edge("supervisor", END)

    return workflow


def create_research_app():
    graph = build_supervisor_graph()
    memory = MemorySaver()
    return graph.compile(checkpointer=memory)


_research_app = None

def get_research_app():
    global _research_app
    if _research_app is None:
        _research_app = create_research_app()
    return _research_app