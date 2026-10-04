"""Pydantic schemas v3.0 — full 5-link chain support."""

from typing import Optional
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