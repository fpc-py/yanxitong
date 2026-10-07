"""Pydantic schemas v3.0 — full 5-link chain support."""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str = Field(..., description="User query")
    session_id: Optional[str] = Field(None)
    topic: str = Field("general")


class AnalyzeRequest(BaseModel):
    session_id: str
    query: str


class ReviewRequest(BaseModel):
    session_id: str
    draft: Optional[str] = Field(None, description="Paper draft. Prefix with [APA]/[MLA]/[GBT] for style.")


class UploadResponse(BaseModel):
    session_id: str
    filename: str
    size_bytes: int
    file_path: str
    message: str


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