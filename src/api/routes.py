"""FastAPI routes v3.0 — full 5-link chain: find→read→compute→write→review."""

import uuid, os, logging, tempfile
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pathlib import Path

from src.api.schemas import (
    QueryRequest, AnalyzeRequest, ReviewRequest,
    QueryResponse, SessionStatus, CitationChainResponse, HealthResponse,
    UploadResponse,
)
from src.workflows.state import create_initial_state
from src.workflows.supervisor_graph import get_research_app

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")
_sessions: dict[str, dict] = {}
UPLOAD_DIR = Path(tempfile.gettempdir()) / "yanxitong_uploads"


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok", version="3.0.0", timestamp=datetime.now(timezone.utc).isoformat())


@router.post("/session", response_model=QueryResponse)
async def create_session_and_query(req: QueryRequest):
    session_id = req.session_id or str(uuid.uuid4())[:12]
    state = create_initial_state(session_id=session_id, user_id="default", topic=req.topic, query=req.query)
    _sessions[session_id] = state
    app = get_research_app()
    config = {"configurable": {"thread_id": session_id}}
    try:
        result = await app.ainvoke(state, config)
    except Exception as e:
        logger.exception("Workflow failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(e))
    _sessions[session_id] = result
    return _build_response(session_id, result)


@router.post("/session/{session_id}/query", response_model=QueryResponse)
async def continue_query(session_id: str, req: QueryRequest):
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    state["user_query"] = req.query
    state["final_response"] = None
    state["error_message"] = None
    app = get_research_app()
    config = {"configurable": {"thread_id": session_id}}
    try:
        result = await app.ainvoke(state, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    _sessions[session_id] = result
    return _build_response(session_id, result)


# ---- Phase 2 endpoints ----

@router.post("/session/{session_id}/upload", response_model=UploadResponse)
async def upload_data_file(session_id: str, file: UploadFile = File(...)):
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename).suffix if file.filename else ".csv"
    file_path = UPLOAD_DIR / f"{session_id}_{uuid.uuid4().hex[:8]}{suffix}"
    content = await file.read()
    file_path.write_bytes(content)
    state["data_file_path"] = str(file_path)
    # 独立阶段，配合 route_intent/route_after_analysis：上传后只做数据分析
    state["current_phase"] = "data_analysis"
    _sessions[session_id] = state
    return UploadResponse(session_id=session_id, filename=file.filename, size_bytes=len(content), file_path=str(file_path), message="File uploaded. Use /analyze to run analysis.")


@router.post("/session/{session_id}/analyze", response_model=QueryResponse)
async def analyze_data(session_id: str, req: AnalyzeRequest):
    state = _sessions.get(session_id)
    if not state: raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    state["user_query"] = req.query
    state["current_phase"] = "data_analysis"
    state["final_response"] = None
    state["error_message"] = None
    app = get_research_app()
    config = {"configurable": {"thread_id": session_id}}
    try:
        result = await app.ainvoke(state, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    _sessions[session_id] = result
    exp = result.get("experiment_results", {}) or {}
    figures = exp.get("figures", []) or []
    stdout = (exp.get("stdout") or "").strip()
    if stdout:
        answer = stdout
    elif figures:
        answer = "沙箱已执行分析并生成图表，但生成的代码没有输出文字结论。图表见下方。"
    else:
        answer = (exp.get("stderr") or "").strip() or "分析已执行，但没有可展示的输出。"
    return QueryResponse(session_id=session_id, answer=answer, confidence=result.get("confidence_scores", {}).get("data_analyst", 0.5), citations=[], phase="data_analysis", figures=figures)


@router.post("/session/{session_id}/design", response_model=QueryResponse)
async def design_experiment(session_id: str, req: QueryRequest):
    state = _sessions.get(session_id)
    if not state: raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    state["user_query"] = req.query or state.get("user_query", "Design experiment")
    # 用独立的 "design" 阶段，避免被 route_intent 误判为数据分析（experiment）而先跑沙箱
    state["current_phase"] = "design"
    state["final_response"] = None
    state["error_message"] = None
    app = get_research_app()
    config = {"configurable": {"thread_id": session_id}}
    try:
        result = await app.ainvoke(state, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    _sessions[session_id] = result
    exp = result.get("experiment_results", {})
    design = exp.get("design", {}) if isinstance(exp, dict) else {}
    answer = _format_design_response(design)
    return QueryResponse(session_id=session_id, answer=answer, confidence=result.get("confidence_scores", {}).get("experiment_designer", 0.5), citations=[], phase="experiment")


# ---- Phase 3 endpoints ----

@router.post("/session/{session_id}/write", response_model=QueryResponse)
async def write_paper(session_id: str, req: QueryRequest):
    """Generate a paper section or full draft. Query specifies section: abstract/introduction/methods/results/discussion/full_paper."""
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    section_map = {
        "摘要": "abstract", "abstract": "abstract",
        "引言": "introduction", "introduction": "introduction",
        "方法": "methods", "methods": "methods",
        "结果": "results", "results": "results",
        "讨论": "discussion", "discussion": "discussion",
        "全文": "full_paper", "full": "full_paper", "full_paper": "full_paper",
    }
    section = "full_paper"
    for kw, sec in section_map.items():
        if kw in req.query.lower():
            section = sec
            break

    state["writing_section"] = section
    state["user_query"] = req.query
    state["current_phase"] = "writing"
    state["final_response"] = None
    state["error_message"] = None

    app = get_research_app()
    config = {"configurable": {"thread_id": session_id}}
    try:
        result = await app.ainvoke(state, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    _sessions[session_id] = result

    content = result.get("writing_draft", result.get("final_response", ""))
    return QueryResponse(session_id=session_id, answer=content[:5000], confidence=result.get("confidence_scores", {}).get("writing_assistant", 0.5), citations=[], phase="writing")


@router.post("/session/{session_id}/review", response_model=QueryResponse)
async def review_draft(session_id: str, req: ReviewRequest):
    """Enhanced review with style specification. Include style in draft prefix: [APA]/[MLA]/[GBT]."""
    state = _sessions.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    draft = req.draft or state.get("writing_draft", "")
    style = "GB/T 7714"
    for s in ["APA", "MLA", "GBT", "GB/T"]:
        if draft.startswith(f"[{s}]"):
            style = "GB/T 7714" if s in ("GBT", "GB/T") else s
            draft = draft[len(f"[{s}]"):].strip()
            break

    state["writing_draft"] = draft
    state["citation_style"] = style
    state["current_phase"] = "writing"

    app = get_research_app()
    config = {"configurable": {"thread_id": session_id}}
    try:
        result = await app.ainvoke(state, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    _sessions[session_id] = result

    # Get review from experiment_results
    exp = result.get("experiment_results", {})
    review = exp.get("review", {}) if isinstance(exp, dict) else {}
    answer = _format_review_response(review, style)
    return QueryResponse(session_id=session_id, answer=answer, confidence=result.get("confidence_scores", {}).get("academic_reviewer", 0.5), citations=[], phase="writing")


@router.post("/session/{session_id}/bibliography")
async def format_bibliography(session_id: str, style: str = "gbt7714"):
    """Generate formatted bibliography from session papers."""
    state = _sessions.get(session_id)
    if not state: raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    from src.tools.citation_formatter import CitationFormatter
    papers = state.get("literature_results", [])
    bib = CitationFormatter.format_bibliography(papers, style)
    return {"session_id": session_id, "style": style, "count": len(papers), "bibliography": bib}


# ---- Session info endpoints ----

@router.get("/session/{session_id}", response_model=SessionStatus)
async def get_session_status(session_id: str):
    state = _sessions.get(session_id)
    if not state: raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return SessionStatus(session_id=session_id, topic=state.get("research_topic",""), current_phase=state.get("current_phase","literature"), papers_count=len(state.get("literature_results",[])), kg_entities_count=0, confidence_scores=state.get("confidence_scores",{}), human_review_required=state.get("human_review_required",False), error=state.get("error_message"), has_data_file=bool(state.get("data_file_path","")))


@router.get("/session/{session_id}/citation-chain", response_model=CitationChainResponse)
async def get_citation_chain(session_id: str):
    state = _sessions.get(session_id)
    if not state: raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    chain = state.get("citation_chain", [])
    if isinstance(chain, dict): chain = chain.get("claims", [])
    avg_conf = sum(c.get("confidence",0) for c in chain) / len(chain) if chain else 0.0
    return CitationChainResponse(session_id=session_id, claims=chain, average_confidence=avg_conf)


# ---- Helpers ----

def _build_response(session_id: str, result: dict) -> QueryResponse:
    return QueryResponse(session_id=session_id, answer=result.get("final_response","No response generated"), confidence=result.get("confidence_scores",{}).get("supervisor",0.5), citations=result.get("citation_chain",[]), human_review_required=result.get("human_review_required",False), phase=result.get("current_phase","literature"))


def _format_design_response(design: dict) -> str:
    if not design: return "No design generated."
    lines = [f"## 实验设计方案", f"**假设**: {design.get('hypothesis','N/A')}", f"**依据**: {design.get('rationale','N/A')}"]
    v = design.get("variables",{})
    lines.append(f"**自变量**: {', '.join(v.get('independent',[]))}")
    lines.append(f"**因变量**: {', '.join(v.get('dependent',[]))}")
    lines.append(f"**控制变量**: {', '.join(v.get('controlled',[]))}")
    lines.append(f"**统计方法**: {', '.join(design.get('statistical_methods',[]))}")
    conflicts = design.get("conflicts",[])
    if conflicts:
        lines.append("## 文献冲突检测")
        for c in conflicts:
            lines.append(f"- {c.get('source_a','?')}: {c.get('claim_a','?')} vs {c.get('source_b','?')}: {c.get('claim_b','?')} → {c.get('suggested_resolution','?')}")
    lines.append(f"**推荐验证方案**: {design.get('recommended_validation','N/A')}")
    return "\n\n".join(lines)


def _format_review_response(review: dict, style: str) -> str:
    if not review: return "No review generated."
    lines = [f"## 审稿报告 ({style})", f"**总分**: {review.get('overall_score',0)}/100", f"**建议**: {review.get('recommendation','N/A')}", f"**优点**: {', '.join(review.get('strengths',[]))}", f"**缺点**: {', '.join(review.get('weaknesses',[]))}", f"**摘要**: {review.get('summary','N/A')}", f"**详细意见**: {review.get('detailed_comments','N/A')[:1000]}", f"**修改清单**: {', '.join(review.get('revision_checklist',[]))}"]
    return "\n\n".join(lines)