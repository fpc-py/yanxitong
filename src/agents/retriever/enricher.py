"""Structured extraction (enrichment) of retrieved papers.

For each paper (up to ``settings.extraction.max_papers``, ``Semaphore``-bounded
concurrency) a flash-tier LLM extracts six fields for the literature matrix:

* research_problem  — one-sentence research question
* methods           — core method + the baseline it is compared against
* datasets          — dataset name + sample size
* results           — metric / value / unit
* limitations       — reported limitations
* relevance         — 1-6 score + reason relative to the session topic

Anti-hallucination: every factual field must carry an ``evidence.quote`` that
is verified server-side against the paper's source text (title + abstract) via
:mod:`src.analysis.evidence`; items whose quote cannot be verified are dropped
before anything reaches the matrix. Relevance is a judgement, not a factual
claim, so it only needs a valid 1-6 score.

Each enriched paper also gets legacy flat fields (``key_findings``, ``methods``,
``datasets``, ``metrics``) projected from the enrichment, so existing
consumers (experiment designer, writing assistant, KG builder) keep working
unchanged. Papers are returned sorted by relevance (descending), which makes
every downstream ``papers[:N]`` slice pick the most on-topic work.
"""

import asyncio
import logging

from src.agents.base import AgentResult, BaseAgent, parse_llm_json
from src.analysis.evidence import source_text_of, verify_quote
from src.core.config import get_settings

logger = logging.getLogger(__name__)

ABSTRACT_LIMIT = 2000
MIN_SOURCE_CHARS = 80

EXTRACTION_SYSTEM_PROMPT = (
    "你是严谨的学术文献信息抽取器。只输出 JSON，不添加任何解释或 Markdown 代码块。"
    "所有 quote 字段必须是论文原文的逐字片段（英文），禁止改写、翻译或补充；"
    "原文没有依据的字段留空，宁缺毋滥。"
)

EXTRACTION_PROMPT = """研究主题：{topic}

论文标题：{title}

论文摘要：
{abstract}

请从上述材料中抽取以下字段，严格输出 JSON：
{{
  "research_problem": {{"text": "一句话研究问题（中文概括）", "quote": "支撑该问题的原文逐字片段"}},
  "methods": [{{"name": "核心方法名", "baseline": "对比的基线方法（无则空串）", "quote": "论证该方法的原文逐字片段"}}],
  "datasets": [{{"name": "数据集名称", "sample_size": "样本规模（无则空串）", "quote": "提到该数据集的原文逐字片段"}}],
  "results": [{{"metric": "指标名", "value": "指标数值", "unit": "单位（无则空串）", "quote": "报告该结果的原文逐字片段"}}],
  "limitations": [{{"text": "局限性（中文概括）", "quote": "陈述该局限的原文逐字片段"}}],
  "relevance": {{"score": 与主题相关度（1-6 的整数）, "reason": "相关性理由（中文，一句话）"}}
}}

规则：
- quote 必须逐字摘自上面的标题/摘要原文，保留英文原样；summary 类文字（text/name/baseline/reason）用中文概括。
- 材料未提及的字段：对象填空字符串、数组填空数组，绝不编造。
- relevance.score：6=研究同一问题且方法可直接比较，4-5=高度相关，2-3=同领域相关，1=仅主题相邻。
"""


def empty_enrichment() -> dict:
    """Neutral enrichment used for papers that were not (or could not be) extracted."""
    return {
        "research_problem": "",
        "methods": [],
        "datasets": [],
        "results": [],
        "limitations": [],
        "relevance": {"score": 0, "reason": ""},
        "evidence": {},
        "dropped_fields": [],
    }


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def build_enrichment(raw: dict, source_text: str) -> dict:
    """Validate raw LLM output against the source text and build storage form.

    Scalars (research_problem) are dropped wholesale when their quote does not
    verify; list fields drop individual items, so one bad quote does not lose
    the whole field.
    """
    raw = _as_dict(raw)
    evidence: dict[str, list[dict]] = {}
    dropped: list[str] = []

    def verified(field: str, quote: str) -> bool:
        if verify_quote(quote, source_text):
            evidence.setdefault(field, []).append({"quote": quote.strip(), "page": None, "source": "abstract"})
            return True
        dropped.append(field)
        return False

    research_problem = ""
    rp = _as_dict(raw.get("research_problem"))
    rp_text, rp_quote = str(rp.get("text") or "").strip(), str(rp.get("quote") or "").strip()
    if rp_text and verified("research_problem", rp_quote):
        research_problem = rp_text

    methods = []
    for item in _as_list(raw.get("methods")):
        item = _as_dict(item)
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        entry = {"name": name, "baseline": str(item.get("baseline") or "").strip()}
        if verified("methods", str(item.get("quote") or "")):
            methods.append(entry)

    datasets = []
    for item in _as_list(raw.get("datasets")):
        item = _as_dict(item)
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        entry = {"name": name, "sample_size": str(item.get("sample_size") or "").strip()}
        if verified("datasets", str(item.get("quote") or "")):
            datasets.append(entry)

    results = []
    for item in _as_list(raw.get("results")):
        item = _as_dict(item)
        metric = str(item.get("metric") or "").strip()
        value = str(item.get("value") or "").strip()
        if not (metric or value):
            continue
        entry = {"metric": metric, "value": value, "unit": str(item.get("unit") or "").strip()}
        if verified("results", str(item.get("quote") or "")):
            results.append(entry)

    limitations = []
    for item in _as_list(raw.get("limitations")):
        item = _as_dict(item)
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if verified("limitations", str(item.get("quote") or "")):
            limitations.append(text)

    relevance_raw = _as_dict(raw.get("relevance"))
    try:
        score = int(relevance_raw.get("score"))
    except (TypeError, ValueError):
        score = 0
    if not 1 <= score <= 6:
        score = 0
        dropped.append("relevance")
    relevance = {"score": score, "reason": str(relevance_raw.get("reason") or "").strip()}

    return {
        "research_problem": research_problem,
        "methods": methods,
        "datasets": datasets,
        "results": results,
        "limitations": limitations,
        "relevance": relevance,
        "evidence": evidence,
        "dropped_fields": dropped,
    }


def flatten_enrichment(enrichment: dict) -> dict:
    """Project enrichment onto the legacy flat fields other agents consume."""
    findings = []
    for r in enrichment.get("results") or []:
        label = f"{r.get('metric', '')}: {r.get('value', '')}{r.get('unit', '')}".strip(": ")
        if label:
            findings.append(label)
    if not findings and enrichment.get("research_problem"):
        findings = [enrichment["research_problem"]]

    methods = []
    for m in enrichment.get("methods") or []:
        name = m.get("name", "")
        if not name:
            continue
        methods.append(f"{name} (baseline: {m['baseline']})" if m.get("baseline") else name)

    datasets = []
    for d in enrichment.get("datasets") or []:
        name = d.get("name", "")
        if not name:
            continue
        datasets.append(f"{name} (n={d['sample_size']})" if d.get("sample_size") else name)

    metrics = {}
    for r in enrichment.get("results") or []:
        if r.get("metric"):
            metrics[r["metric"]] = f"{r.get('value', '')}{r.get('unit', '')}"

    flat: dict = {}
    if findings:
        flat["key_findings"] = findings[:5]
    if methods:
        flat["methods"] = methods
    if datasets:
        flat["datasets"] = datasets
    if metrics:
        flat["metrics"] = metrics
    return flat


def _rank_key(paper: dict) -> tuple[int, int]:
    relevance = (paper.get("enrichment") or {}).get("relevance") or {}
    return (-int(relevance.get("score") or 0), -int(paper.get("citations_count") or 0))


class PaperEnricher(BaseAgent):
    """Six-field structured extraction with server-side evidence validation."""

    name = "paper_enricher"
    description = "论文六字段结构化抽取"
    model_role = "lightweight"

    async def _execute_impl(self, state: dict) -> AgentResult:
        papers = state.get("literature_results", []) or []
        topic = state.get("research_topic") or state.get("user_query", "")
        enriched = await self.enrich_papers(papers, topic)
        return AgentResult(success=True, data={"papers": enriched}, confidence=1.0)

    async def enrich_papers(self, papers: list[dict], topic: str) -> list[dict]:
        """Enrich up to ``extraction.max_papers`` papers, then rank by relevance."""
        settings = get_settings()
        limit = max(1, settings.extraction.max_papers)
        concurrency = max(1, settings.extraction.concurrency)
        targets, rest = papers[:limit], papers[limit:]
        for paper in rest:
            paper["enrichment"] = empty_enrichment()

        sem = asyncio.Semaphore(concurrency)
        outcomes = await asyncio.gather(
            *(self._enrich_one(paper, topic, sem) for paper in targets),
            return_exceptions=True,
        )

        enriched = 0
        for paper, outcome in zip(targets, outcomes):
            if isinstance(outcome, BaseException):
                logger.warning("Enrichment failed for %r: %s", paper.get("title", "")[:60], outcome)
                outcome = None
            if not outcome:
                paper["enrichment"] = empty_enrichment()
                continue
            paper["enrichment"] = outcome
            paper.update(flatten_enrichment(outcome))
            enriched += 1

        self._audit("enrich_complete", {"targets": len(targets), "enriched": enriched, "skipped": len(rest)})
        return sorted(papers, key=_rank_key)

    async def _enrich_one(self, paper: dict, topic: str, sem: asyncio.Semaphore) -> dict | None:
        source_text = source_text_of(paper)
        if len(source_text) < MIN_SOURCE_CHARS:
            self._audit("enrich_skip", {"reason": "source_too_short", "title": (paper.get("title") or "")[:60]})
            return None
        prompt = EXTRACTION_PROMPT.format(
            topic=topic or "(未提供)",
            title=paper.get("title", ""),
            abstract=source_text[:ABSTRACT_LIMIT],
        )
        async with sem:
            response = await self._call_llm(prompt, system_prompt=EXTRACTION_SYSTEM_PROMPT, json_mode=True)
        return build_enrichment(parse_llm_json(response), source_text)
