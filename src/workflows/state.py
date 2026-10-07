"""LangGraph ResearchState definition and supporting data classes.

The dataclasses in this module describe the payload objects that flow through
the four-phase research pipeline (literature -> knowledge graph -> experiment
-> writing).  They are converted to plain dicts before being written into
ResearchState, because LangGraph checkpoints must stay JSON/msgpack
serializable.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Optional, TypedDict

__all__ = [
    "Citation",
    "PaperSummary",
    "ExperimentReport",
    "SafetyAlert",
    "Task",
    "ResearchState",
    "create_initial_state",
]


@dataclass
class Citation:
    """A citation linking a claim to its source."""

    claim: str
    source_title: str
    source_url: str = ""
    source_authors: list[str] = field(default_factory=list)
    source_year: int = 0
    page_number: str = ""
    excerpt: str = ""           # Verbatim text lifted from the source.
    confidence: float = 1.0     # 0..1: how directly the excerpt backs the claim.


@dataclass
class PaperSummary:
    """Structured summary of one retrieved paper."""

    title: str
    authors: list[str] = field(default_factory=list)
    year: int = 0
    abstract: str = ""
    url: str = ""
    source: str = "arxiv"           # arxiv | semantic_scholar | openalex | knowledge
    doi: str = ""
    citations_count: int = 0
    key_findings: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    datasets: list[str] = field(default_factory=list)
    metrics: dict[str, str] = field(default_factory=dict)
    full_text_snippet: str = ""
    relevance_score: int = 0        # 1-6 from enrichment; 0 = not assessed.
    relevance_reason: str = ""
    enrichment: dict[str, Any] = field(default_factory=dict)  # six fields + evidence.


@dataclass
class ExperimentReport:
    """Results from a data-analysis experiment run by the Data Analyst agent."""

    hypothesis: str
    method: str
    data_summary: dict[str, Any] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    figures: list[str] = field(default_factory=list)  # base64 payloads or file paths.
    statistical_results: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    code: str = ""


@dataclass
class SafetyAlert:
    """A safety-related flag raised by the reviewer or guardrail layer."""

    level: Literal["info", "warning", "critical"]
    message: str
    rule_id: str = ""
    suggested_action: str = ""


@dataclass
class Task:
    """A unit of work in the supervisor's task queue."""

    task_id: str
    phase: Literal["literature", "knowledge_graph", "experiment", "writing"]
    description: str
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    assigned_agent: str = ""
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class ResearchState(TypedDict):
    """Global state shared across all LangGraph nodes.

    List fields written by potentially parallel nodes use
    Annotated[..., operator.add] reducers so concurrent updates append
    instead of overwriting each other.
    """

    # --- Session metadata ---------------------------------------------------
    session_id: str
    user_id: str
    research_topic: str

    # --- Task management ------------------------------------------------------
    current_phase: Literal["literature", "knowledge_graph", "experiment", "writing"]
    task_queue: Annotated[list[dict[str, Any]], operator.add]
    completed_tasks: Annotated[list[dict[str, Any]], operator.add]

    # --- Phase outputs ----------------------------------------------------------
    literature_results: list[dict[str, Any]]       # PaperSummary as dicts.
    literature_conflicts: list[dict[str, Any]]      # Contradiction pairs (claim_a vs claim_b).
    research_gaps: list[dict[str, Any]]             # Identified research gaps.
    kg_snapshot: Optional[str]                      # JSON string of graph subgraph.
    kg_stats: Optional[dict[str, Any]]              # Graph build stats for this session.
    research_roadmap: list[dict[str, Any]]          # Timeline / evolution entries from the KG.
    triple_report: Optional[dict[str, Any]]         # (method, dataset, metric) verification report.
    experiment_results: Optional[dict[str, Any]]    # ExperimentReport as a dict.
    writing_draft: Optional[str]

    # --- Quality & safety --------------------------------------------------------
    confidence_scores: dict[str, float]
    citation_chain: list[dict[str, Any]]            # Citation as dicts.
    safety_flags: list[dict[str, Any]]              # SafetyAlert as dicts.

    # --- Human-in-the-loop ---------------------------------------------------------
    human_review_required: bool
    human_feedback: Optional[str]

    # --- Messages --------------------------------------------------------------------
    user_query: str
    final_response: Optional[str]
    error_message: Optional[str]

    # --- Inputs written by the API layer before invoking the graph --------------------
    # 必须在此声明：LangGraph 只保留 schema 中声明的通道，未声明的键会被丢弃
    # （历史 bug：data_file_path 被丢弃，导致数据分析误判为「无数据」而自己造样本）。
    data_file_path: str
    writing_section: str
    citation_style: str


def create_initial_state(
    session_id: str,
    user_id: str,
    topic: str,
    query: str,
) -> ResearchState:
    """Factory that returns a clean initial ResearchState.

    Args:
        session_id: Identifier of the current chat session.
        user_id: Identifier of the authenticated user.
        topic: High-level research topic for the whole session.
        query: The concrete question the pipeline should answer now.

    Returns:
        A fully populated state with empty accumulators, starting in the
        literature phase.
    """
    return ResearchState(
        session_id=session_id,
        user_id=user_id,
        research_topic=topic,
        current_phase="literature",
        task_queue=[],
        completed_tasks=[],
        literature_results=[],
        literature_conflicts=[],
        research_gaps=[],
        kg_snapshot=None,
        kg_stats=None,
        research_roadmap=[],
        triple_report=None,
        experiment_results=None,
        writing_draft=None,
        confidence_scores={},
        citation_chain=[],
        safety_flags=[],
        human_review_required=False,
        human_feedback=None,
        user_query=query,
        final_response=None,
        error_message=None,
        data_file_path="",
        writing_section="",
        citation_style="",
    )
