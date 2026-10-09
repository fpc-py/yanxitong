# Workflow definitions v3.0 — supervisor graph with full 5-link chain: find→read→compute→write→review.

import asyncio
import logging
from typing import Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

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


def route_intent(state: ResearchState) -> Literal["retrieve", "data_analyst", "experiment_designer", "writing", "academic_reviewer", "supervisor"]:
    query = state.get("user_query", "").lower()
    phase = state.get("current_phase", "")

    if phase == "experiment":
        return "data_analyst"
    if phase == "data_analysis":
        return "data_analyst"
    if phase == "design":
        return "experiment_designer"
    # /review 直达审稿节点：绝不重跑写作，避免新生成草稿覆盖用户提交的待审稿
    if phase == "review":
        return "academic_reviewer"
    if phase == "writing":
        return "writing"

    if any(w in query for w in ["写论文", "生成论文", "撰写", "draft", "写作", "写摘要", "写引言"]):
        return "writing"
    if any(w in query for w in ["审稿", "审阅", "修改论文", "论文评审", "review", "评审"]):
        return "academic_reviewer"
    if any(w in query for w in ["分析数据", "数据分析", "统计", "csv", "图表", "可视化", "analyze"]):
        return "data_analyst"
    if any(w in query for w in ["实验设计", "实验方案", "假设", "验证方案", "experiment design"]):
        return "experiment_designer"
    if not state.get("literature_results"):
        return "retrieve"
    return "supervisor"


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