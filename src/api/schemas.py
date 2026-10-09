"""Pydantic schemas v3.0 — full 5-link chain support."""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(..., description="User query")
    session_id: Optional[str] = Field(None)
    topic: str = Field("general")
    kb_id: Optional[str] = Field(
        None,
        description="领域包 id（仅建会话时生效；缺省=未分类默认包）。换领域=新建会话",
    )
    cross_kb_ids: Optional[list[str]] = Field(
        None,
        description="显式声明的跨域参考包 id 列表（未声明=零跨包召回；可在会话中通过 PATCH 调整）",
    )


class PackCreateRequest(BaseModel):
    """领域包（KnowledgeBase 分区）创建请求。"""

    name: str = Field(..., min_length=1, max_length=64, description="包名，如「图神经网络」")
    description: str = Field("", max_length=500)


class PackPatchRequest(BaseModel):
    """领域包改名/描述（仅 owner 可改）。"""

    name: str = Field("", max_length=64)
    description: str = Field("", max_length=500)


class SessionKbRequest(BaseModel):
    """会话跨域声明更新（kb_id 本身不可改，换包=新会话）。"""

    cross_kb_ids: list[str] = Field(default_factory=list, description="跨域参考包 id 列表")


class AnalyzeRequest(BaseModel):
    session_id: str
    query: str


class DesignRequest(BaseModel):
    """实验设计请求：可带当前实验配置（模型/超参/数据/资源），用于瓶颈诊断与优化。"""

    session_id: Optional[str] = Field(None, description="会话 id（路径已含，缺省可不填）")
    query: str
    experiment_config: Optional[dict] = Field(
        None,
        description="当前实验配置，形如 {model, hyperparams:{lr,batch_size,...}, dataset:{name,size}, resources:{gpu_hours,memory_gb}}",
    )


class DesignFeedbackRequest(BaseModel):
    """实验结果回流（闭环反馈⑩）：写入先验存储与知识图谱。"""

    run_id: str
    candidate_id: str = Field("", description="候选方案 id（可选，缺省取推荐方案）")
    metrics: dict = Field(default_factory=dict, description="实测指标 {指标名: 数值}")
    cost: Optional[float] = Field(None, description="实测成本（相对单位，可选）")
    duration_hours: Optional[float] = Field(None, description="实测训练时长（小时，可选）")
    notes: str = Field("", description="备注/配置偏差说明")


class ReviewRequest(BaseModel):
    session_id: str
    draft: Optional[str] = Field(None, description="Paper draft. Prefix with [APA]/[MLA]/[GBT] for style.")
    style: Optional[str] = Field(None, description="引用格式：APA / MLA / GBT（缺省 GB/T 7714）")


class UploadResponse(BaseModel):
    session_id: str
    filename: str
    size_bytes: int
    file_path: str
    message: str


class ReviewDecisionRequest(BaseModel):
    """图谱边人工复核判定（规格③）。"""

    decision: str = Field(..., description="approved | rejected")
    note: str = Field("", description="复核备注")


class QueryResponse(BaseModel):
    session_id: str
    answer: str
    confidence: float
    citations: list[dict] = Field(default_factory=list)
    human_review_required: bool = False
    phase: str = "literature"
    figures: list[dict] = Field(
        default_factory=list,
        description="沙箱生成的图表，元素形如 {name, data_url}",
    )
    quota_remaining: Optional[int] = Field(
        None,
        description="未登录用户的剩余免费问答次数；已登录或配额服务不可用时为 null",
    )
    triple_report: Optional[dict] = Field(
        None,
        description="三元组幻觉校验报告：{checked, score, states, flags, triples}",
    )
    profile: Optional[dict] = Field(
        None,
        description="数据分析①：数据画像（格式/行列/列Schema/质量报告）；降级时含 degraded",
    )
    task_plan: Optional[dict] = Field(
        None,
        description="数据分析②：任务规划（步骤/依赖/预期产出 + 推荐方法与绘图模板）",
    )
    knowledge_recall: Optional[dict] = Field(
        None,
        description="数据分析③：RAG 知识召回（绘图模板/统计方法/期刊规范/教材 证据块）",
    )
    validation: Optional[dict] = Field(
        None,
        description="数据分析⑦：结果验证报告（确定性检查 + 假设适用性 + 叙述一致性）",
    )
    analysis_run: Optional[dict] = Field(
        None,
        description="数据分析⑨：产出包元数据（run_id/降级状态/阶段轨迹/产物清单）",
    )
    # ---- Experiment designer（实验方案优化引擎） ----
    experiment_config: Optional[dict] = Field(
        None,
        description="实验设计①：解析后的当前实验配置（模型/超参/数据/资源）",
    )
    design_diagnosis: Optional[dict] = Field(
        None,
        description="实验设计①：瓶颈诊断（模型落后/超参偏离/数据不足/策略缺失/资源不匹配）",
    )
    design_evidence: Optional[dict] = Field(
        None,
        description="实验设计②：证据集（KG 路径/文献引用/知识库块，逐条来源锚点）",
    )
    design_candidates: Optional[dict] = Field(
        None,
        description="实验设计④：多路候选方案（结构/超参/数据/策略，含估计值与证据引用）",
    )
    design_optimization: Optional[dict] = Field(
        None,
        description="实验设计⑤⑥：多目标优化（Pareto 前沿 + Top-K 加权推荐）",
    )
    design_validation: Optional[dict] = Field(
        None,
        description="实验设计⑦⑧⑪：可行性校验 + 验证计划 + 幻觉校验报告",
    )
    design_run: Optional[dict] = Field(
        None,
        description="实验设计⑨⑫：产出包元数据（run_id/阶段轨迹/产物清单/降级状态）",
    )
    # ---- Writing assistant / Academic reviewer（成果产出页） ----
    writing: Optional[dict] = Field(
        None,
        description="论文写作：{section, content}；/write 返回，写作页据此渲染带标注的草稿",
    )
    review: Optional[dict] = Field(
        None,
        description=(
            "学术审阅：{overall_score, recommendation, summary, strengths, weaknesses, "
            "revision_checklist, issues:[{id,type,severity,status,quote,description,suggestion,"
            "section,para,fixable,located}], stats, similarity, citation_trace 等}；"
            "/write、/review 返回"
        ),
    )


# ---- Auth -------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class UserInfo(BaseModel):
    id: int
    username: str
    created_at: Optional[str] = None


class QuotaInfo(BaseModel):
    limit: int
    used: int
    remaining: int


class AuthResponse(BaseModel):
    token: str
    user: UserInfo


class MeResponse(BaseModel):
    user: Optional[UserInfo] = None
    quota: Optional[QuotaInfo] = None


class BibliographyResponse(BaseModel):
    session_id: str
    style: str
    count: int
    bibliography: str


class SessionStatus(BaseModel):
    session_id: str
    topic: str
    current_phase: str
    papers_count: int = 0
    kg_entities_count: int = 0
    confidence_scores: dict = Field(default_factory=dict)
    human_review_required: bool = False
    error: Optional[str] = None
    has_data_file: bool = False
    has_draft: bool = False
    writing_section: str = Field("", description="最近一次写作的章节 key")
    writing_draft: str = Field("", description="会话中最近一次生成的草稿全文（用于页面恢复）")
    review: Optional[dict] = Field(None, description="会话中最近一次的审阅结果（用于页面恢复）")
    kb_id: str = Field("default", description="会话绑定的领域包 id")
    kb_name: str = Field("", description="领域包显示名")
    cross_kb_ids: list[str] = Field(
        default_factory=list, description="显式声明的跨域参考包 id 列表"
    )


class SessionListItem(BaseModel):
    session_id: str
    topic: str
    papers_count: int


class DefenseItem(BaseModel):
    key: str
    label: str
    active: bool
    note: str = ""


class SystemCapabilities(BaseModel):
    sandbox_mode: Literal["docker", "mock"]
    defenses: list[DefenseItem] = Field(default_factory=list)
    last_trace_id: Optional[str] = None
    eval_layers: int = 4


class MetricsSummary(BaseModel):
    cost_cents: float = 0.0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    request_count: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    hallucination_flags: dict[str, int] = Field(default_factory=dict)
    guard_blocks: int = 0
    errors: int = 0
    avg_latency_s: float = 0.0


class KnowledgeFileItem(BaseModel):
    filename: str
    library: str = "personal"
    uploader: str = ""
    content_hash: str = ""
    chunks: int
    chars: int
    updated_at: str = ""
    preview: str = ""


class KnowledgeUploadResult(BaseModel):
    filename: str
    added: int
    total_docs: int
    library: str = "personal"
    parser_used: str = "text"
    pages: int = 0
    chunks: int = 0
    ocr_used: bool = False
    deduped: bool = False
    uploader: str = ""


class CitationChainResponse(BaseModel):
    session_id: str
    claims: list[dict] = Field(default_factory=list)
    average_confidence: float = 0.0


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "3.0.0"
    timestamp: str = ""
    features: list[str] = Field(default_factory=lambda: [
        "literature_search", "knowledge_graph", "data_analysis",
        "experiment_design", "paper_writing", "academic_review",
        "citation_tracking", "hallucination_defense",
        "rate_limiting", "sandbox_execution", "bibliography_formatting"
    ])