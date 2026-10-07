"""FastAPI routes v3.0 — full 5-link chain: find→read→compute→write→review."""

import uuid, os, logging, hashlib
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request, Response
from pathlib import Path

from src.api.schemas import (
    QueryRequest, AnalyzeRequest, ReviewRequest,
    QueryResponse, SessionStatus, CitationChainResponse, HealthResponse,
    UploadResponse, SessionListItem,
    SystemCapabilities, DefenseItem, MetricsSummary,
    KnowledgeFileItem, KnowledgeUploadResult,
)
from src.api.deps import Identity, consume_question_quota, get_identity
from src.api.auth_routes import router as auth_router
from src.api import session_store
from src.core.config import get_settings
from src.workflows.state import create_initial_state
from src.workflows.supervisor_graph import get_research_app
from src.workflows import tracing
from src.knowledge.kb import (
    add_chunks as kb_add_chunks,
    find_by_hash as kb_find_by_hash,
    find_chunk as kb_find_chunk,
    get_kb_store,
    list_files as kb_list_files,
    remove_file as kb_remove_file,
)
from src.knowledge.kb_ner import index_chunks as kb_index_chunks
from src.analysis.literature_analysis import build_matrix, matrix_to_csv
from src.tools.pdf_ingest import ParsedBlock, ParsedDoc, chunk_parsed_doc, parse_pdf
from src.tools.sandbox import get_sandbox, DockerSandbox
from src.observability import metrics as metrics_mod

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")
router.include_router(auth_router)
# 会话状态：启动时从 SQLite 恢复，之后的每次变更都会写回（后端重启不丢历史会话）
_sessions: dict[str, dict] = session_store.load_sessions()
UPLOAD_DIR = Path("data") / "uploads"


def _persist_session(session_id: str, state: dict) -> None:
    """Update in-memory session state and write it through to SQLite."""
    _sessions[session_id] = state
    session_store.save_session(session_id, state)


def _get_owned_state(session_id: str, identity: Identity) -> dict:
    """Return the session state, or 404 when missing or owned by someone else.

    会话按身份（user:<id> / anon:<anon_id>）隔离：不同用户、不同浏览器
    的匿名体验者互相看不到对方的研究会话。
    """
    state = _sessions.get(session_id)
    if not state or state.get("user_id") != identity.label:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    return state


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok", version="3.0.0", timestamp=datetime.now(timezone.utc).isoformat())


@router.post("/session", response_model=QueryResponse)
async def create_session_and_query(req: QueryRequest, request: Request):
    identity = await get_identity(request)
    quota_remaining = await consume_question_quota(identity)
    session_id = req.session_id or str(uuid.uuid4())[:12]
    state = create_initial_state(session_id=session_id, user_id=identity.label, topic=req.topic, query=req.query)
    _persist_session(session_id, state)
    app = get_research_app()
    config = {"configurable": {"thread_id": session_id}}
    try:
        result = await app.ainvoke(state, config)
    except Exception as e:
        logger.exception("Workflow failed for session %s", session_id)
        raise HTTPException(status_code=500, detail=str(e))
    _persist_session(session_id, result)
    return _build_response(session_id, result, quota_remaining=quota_remaining)


@router.post("/session/{session_id}/query", response_model=QueryResponse)
async def continue_query(session_id: str, req: QueryRequest, request: Request):
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)
    quota_remaining = await consume_question_quota(identity)
    state["user_query"] = req.query
    state["final_response"] = None
    state["error_message"] = None
    app = get_research_app()
    config = {"configurable": {"thread_id": session_id}}
    try:
        result = await app.ainvoke(state, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    _persist_session(session_id, result)
    return _build_response(session_id, result, quota_remaining=quota_remaining)


# ---- Phase 2 endpoints ----

@router.post("/session/{session_id}/upload", response_model=UploadResponse)
async def upload_data_file(session_id: str, request: Request, file: UploadFile = File(...)):
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename).suffix if file.filename else ".csv"
    file_path = UPLOAD_DIR / f"{session_id}_{uuid.uuid4().hex[:8]}{suffix}"
    content = await file.read()
    file_path.write_bytes(content)
    state["data_file_path"] = str(file_path)
    # 独立阶段，配合 route_intent/route_after_analysis：上传后只做数据分析
    state["current_phase"] = "data_analysis"
    _persist_session(session_id, state)
    return UploadResponse(session_id=session_id, filename=file.filename, size_bytes=len(content), file_path=str(file_path), message="File uploaded. Use /analyze to run analysis.")


@router.post("/session/{session_id}/analyze", response_model=QueryResponse)
async def analyze_data(session_id: str, req: AnalyzeRequest, request: Request):
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)
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
    _persist_session(session_id, result)
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
async def design_experiment(session_id: str, req: QueryRequest, request: Request):
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)
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
    _persist_session(session_id, result)
    exp = result.get("experiment_results", {})
    design = exp.get("design", {}) if isinstance(exp, dict) else {}
    answer = _format_design_response(design)
    return QueryResponse(session_id=session_id, answer=answer, confidence=result.get("confidence_scores", {}).get("experiment_designer", 0.5), citations=[], phase="experiment")


# ---- Phase 3 endpoints ----

@router.post("/session/{session_id}/write", response_model=QueryResponse)
async def write_paper(session_id: str, req: QueryRequest, request: Request):
    """Generate a paper section or full draft. Query specifies section: abstract/introduction/methods/results/discussion/full_paper."""
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)

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
    _persist_session(session_id, result)

    content = result.get("writing_draft", result.get("final_response", ""))
    return QueryResponse(session_id=session_id, answer=content[:5000], confidence=result.get("confidence_scores", {}).get("writing_assistant", 0.5), citations=[], phase="writing")


@router.post("/session/{session_id}/review", response_model=QueryResponse)
async def review_draft(session_id: str, req: ReviewRequest, request: Request):
    """Enhanced review with style specification. Include style in draft prefix: [APA]/[MLA]/[GBT]."""
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)

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
    _persist_session(session_id, result)

    # Get review from experiment_results
    exp = result.get("experiment_results", {})
    review = exp.get("review", {}) if isinstance(exp, dict) else {}
    answer = _format_review_response(review, style)
    return QueryResponse(session_id=session_id, answer=answer, confidence=result.get("confidence_scores", {}).get("academic_reviewer", 0.5), citations=[], phase="writing")


@router.post("/session/{session_id}/bibliography")
async def format_bibliography(session_id: str, request: Request, style: str = "gbt7714"):
    """Generate formatted bibliography from session papers."""
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)
    from src.tools.citation_formatter import CitationFormatter
    papers = state.get("literature_results", [])
    bib = CitationFormatter.format_bibliography(papers, style)
    return {"session_id": session_id, "style": style, "count": len(papers), "bibliography": bib}


# ---- Session info endpoints ----

@router.get("/session/{session_id}", response_model=SessionStatus)
async def get_session_status(session_id: str, request: Request):
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)
    return SessionStatus(session_id=session_id, topic=state.get("research_topic",""), current_phase=state.get("current_phase","literature"), papers_count=len(state.get("literature_results",[])), kg_entities_count=0, confidence_scores=state.get("confidence_scores",{}), human_review_required=state.get("human_review_required",False), error=state.get("error_message"), has_data_file=bool(state.get("data_file_path","")))


@router.get("/session/{session_id}/citation-chain", response_model=CitationChainResponse)
async def get_citation_chain(session_id: str, request: Request):
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)
    chain = state.get("citation_chain", [])
    if isinstance(chain, dict): chain = chain.get("claims", [])
    avg_conf = sum(c.get("confidence",0) for c in chain) / len(chain) if chain else 0.0
    return CitationChainResponse(session_id=session_id, claims=chain, average_confidence=avg_conf)


@router.get("/session/{session_id}/literature/matrix")
async def get_literature_matrix(session_id: str, request: Request, format: str = "json"):
    """文献矩阵（N×M 六字段对比表）；format=csv 时导出带 BOM 的 CSV（Excel 中文不乱码）。"""
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)
    rows = build_matrix(state.get("literature_results", []))
    if format == "csv":
        return Response(
            content=matrix_to_csv(rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="literature_matrix_{session_id[:8]}.csv"'},
        )
    return {"session_id": session_id, "count": len(rows), "rows": rows}


@router.get("/session/{session_id}/literature/conflicts")
async def get_literature_conflicts(session_id: str, request: Request):
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)
    return {"session_id": session_id, "conflicts": state.get("literature_conflicts", [])}


@router.get("/session/{session_id}/literature/research-gaps")
async def get_research_gaps(session_id: str, request: Request):
    identity = await get_identity(request)
    state = _get_owned_state(session_id, identity)
    return {"session_id": session_id, "gaps": state.get("research_gaps", [])}


@router.get("/sessions", response_model=list[SessionListItem])
async def list_sessions(request: Request):
    """按创建时间倒序返回「当前身份」的会话摘要（会话按用户隔离）。"""
    identity = await get_identity(request)
    return [
        _session_summary(sid, st)
        for sid, st in reversed(list(_sessions.items()))
        if st.get("user_id") == identity.label
    ]


# ---- 系统能力 / 实时指标 / 知识库 ----

_DEFENSE_LAYERS = [
    {"key": "rag", "label": "检索增强 (RAG)", "active": True},
    {"key": "citation", "label": "引用锚定", "active": True},
    {"key": "kg_factcheck", "label": "知识图谱事实校验", "active": True},
    {"key": "self_consistency", "label": "自一致性采样", "active": False, "note": "规划中"},
    {"key": "calibration", "label": "置信度校准", "active": True},
    {"key": "human_breaker", "label": "人工熔断", "active": True},
]


@router.get("/system/capabilities", response_model=SystemCapabilities)
async def system_capabilities():
    """平台可信架构能力：沙箱模式、六道防线激活状态、最近一次链路追踪 ID。"""
    sandbox = get_sandbox()
    mode = "docker" if isinstance(sandbox, DockerSandbox) else "mock"
    return SystemCapabilities(
        sandbox_mode=mode,
        defenses=[DefenseItem(**d) for d in _DEFENSE_LAYERS],
        last_trace_id=tracing.LAST_TRACE_ID,
        eval_layers=4,
    )


def _by_label(counter, index: int) -> dict[str, float]:
    """聚合 Counter 中第 index 个 label 维度的总量（保留 >0 项）。"""
    try:
        out: dict[str, float] = {}
        for k, v in (counter._value.get() or {}).items():
            key = k[index] if isinstance(k, tuple) and len(k) > index else str(k)
            out[key] = out.get(key, 0.0) + float(v)
        return {kk: vv for kk, vv in out.items() if vv > 0}
    except Exception:
        return {}


def _counter_total(counter) -> float:
    try:
        vals = counter._value.get() or {}
        if isinstance(vals, dict):
            return float(sum(vals.values()))
        return float(vals)
    except Exception:
        return 0.0


def _hist_sum(hist) -> float:
    """Histogram 的 _sum/_count 带 endpoint 标签，跨标签求和。"""
    try:
        v = hist._sum.get() if hasattr(hist, "_sum") else 0.0
        if isinstance(v, dict):
            return float(sum(v.values()))
        return float(v)
    except Exception:
        return 0.0


def _hist_count(hist) -> float:
    try:
        v = hist._count.get() if hasattr(hist, "_count") else 0.0
        if isinstance(v, dict):
            return float(sum(v.values()))
        return float(v)
    except Exception:
        return 0.0


@router.get("/metrics/summary", response_model=MetricsSummary)
async def metrics_summary():
    """聚合进程内 Prometheus 计数器为 JSON 摘要（不依赖 Prometheus server）。"""
    m = metrics_mod
    token_by_type = _by_label(m.LLM_TOKEN_COUNT, 1)
    lat_sum = _hist_sum(m.REQUEST_LATENCY)
    lat_count = _hist_count(m.REQUEST_LATENCY)
    return MetricsSummary(
        cost_cents=round(_counter_total(m.LLM_COST), 4),
        total_tokens=int(_counter_total(m.LLM_TOKEN_COUNT)),
        prompt_tokens=int(token_by_type.get("prompt", 0)),
        completion_tokens=int(token_by_type.get("completion", 0)),
        request_count=int(_counter_total(m.REQUEST_COUNT)),
        cache_hits=int(_counter_total(m.CACHE_HITS)),
        cache_misses=int(_counter_total(m.CACHE_MISSES)),
        hallucination_flags={k: int(v) for k, v in _by_label(m.HALLUCINATION_FLAGS, 0).items()},
        guard_blocks=int(_counter_total(m.GUARD_BLOCKS)),
        errors=int(_counter_total(m.ERROR_COUNT)),
        avg_latency_s=round((lat_sum / lat_count), 2) if lat_count else 0.0,
    )


@router.post("/knowledge/upload", response_model=KnowledgeUploadResult)
async def upload_knowledge(request: Request, file: UploadFile = File(...), library: str = Form("personal")):
    """上传文档入库：PDF 走两级解析链（PyMuPDF → OCR），txt/md 直接解析。

    library=team 需登录（课题组共享，全员可检索）；personal 仅上传者可见。
    相同内容（sha256）重复上传直接返回 deduped，不重复入库。
    """
    identity = await get_identity(request)
    if library not in ("team", "personal"):
        raise HTTPException(status_code=400, detail="library 仅支持 team / personal")
    if library == "team" and identity.is_anonymous:
        raise HTTPException(status_code=403, detail={"code": "login_required", "message": "共享库需要登录后上传"})
    settings = get_settings()
    filename = Path(file.filename).name if file.filename else "unnamed"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".txt", ".md", ".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 .txt / .md / .pdf")
    content = await file.read()
    if not content or not content.strip():
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(content) > settings.kb.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"文件超过 {settings.kb.max_upload_mb}MB 上限")

    content_hash = hashlib.sha256(content).hexdigest()
    if kb_find_by_hash(library, identity.label, content_hash):
        return KnowledgeUploadResult(
            filename=filename, added=0, total_docs=len(get_kb_store()),
            library=library, parser_used="dedup", deduped=True, uploader=identity.label,
        )

    pages, ocr_used = 0, False
    if suffix == ".pdf":
        doc = await parse_pdf(content, filename)
        if not doc.full_text.strip():
            raise HTTPException(status_code=400, detail="PDF 未提取到文本（加密或损坏文件？），可尝试重新导出")
        pages, ocr_used = doc.pages, doc.ocr_used
    else:
        text = content.decode("utf-8", errors="replace").strip()
        if not text:
            raise HTTPException(status_code=400, detail="未能从文件中提取文本")
        doc = ParsedDoc(
            full_text=text,
            blocks=[ParsedBlock(text=text, page=0, char_start=0, char_end=len(text))],
            pages=0,
            parser_used="text",
        )
    chunks = chunk_parsed_doc(doc)

    added = kb_add_chunks(filename, chunks, library=library, owner=identity.label, content_hash=content_hash)
    if added == 0:
        raise HTTPException(status_code=400, detail="文本过短或与库中已有内容完全重复，未形成可检索块")
    try:
        ner = await kb_index_chunks(chunks, library=library, owner=identity.label, filename=filename)
        logger.info("KB 图谱写入 %s: %s", filename, ner)
    except Exception as exc:  # NER 失败不影响入库
        logger.warning("KB NER 降级: %s", exc)
    return KnowledgeUploadResult(
        filename=filename, added=added, total_docs=len(get_kb_store()),
        library=library, parser_used=doc.parser_used, pages=pages, chunks=added,
        ocr_used=ocr_used, deduped=False, uploader=identity.label,
    )


@router.get("/knowledge/files", response_model=list[KnowledgeFileItem])
async def list_knowledge_files(request: Request):
    identity = await get_identity(request)
    return [KnowledgeFileItem(**f) for f in kb_list_files(owner=identity.label)]


@router.get("/knowledge/chunk")
async def get_knowledge_chunk(filename: str, chunk_hash: str, request: Request):
    """按块哈希取块正文及前后各一块（引用溯源预览），仅限有权访问的库。"""
    identity = await get_identity(request)
    chunk = kb_find_chunk(owner=identity.label, filename=filename, chunk_hash=chunk_hash)
    if chunk is None:
        raise HTTPException(status_code=404, detail="未找到该知识块")
    return chunk


@router.delete("/knowledge/file/{filename}")
async def delete_knowledge_file(filename: str, request: Request, library: str = "personal"):
    identity = await get_identity(request)
    if library not in ("team", "personal"):
        raise HTTPException(status_code=400, detail="library 仅支持 team / personal")
    removed = kb_remove_file(filename, library=library, owner=identity.label)
    if removed == 0:
        raise HTTPException(status_code=404, detail=f"知识库中没有 {filename}")
    return {"ok": True, "removed": removed}


@router.delete("/session/{session_id}")
async def delete_session(session_id: str, request: Request):
    """删除会话；不存在时静默返回 ok，属于他人时返回 404（不泄露存在性）。"""
    identity = await get_identity(request)
    state = _sessions.get(session_id)
    if state is not None and state.get("user_id") != identity.label:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    _sessions.pop(session_id, None)
    session_store.delete_session(session_id)
    return {"ok": True}


# ---- Helpers ----

def _session_summary(session_id: str, state: dict) -> SessionListItem:
    """与 GET /session/{id} 相同的字段提取逻辑：topic 缺省空串、papers_count 缺省 0。"""
    return SessionListItem(
        session_id=session_id,
        topic=state.get("research_topic", ""),
        papers_count=len(state.get("literature_results", [])),
    )


def _build_response(session_id: str, result: dict, quota_remaining: int | None = None) -> QueryResponse:
    return QueryResponse(session_id=session_id, answer=result.get("final_response","No response generated"), confidence=result.get("confidence_scores",{}).get("supervisor",0.5), citations=result.get("citation_chain",[]), human_review_required=result.get("human_review_required",False), phase=result.get("current_phase","literature"), quota_remaining=quota_remaining)


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