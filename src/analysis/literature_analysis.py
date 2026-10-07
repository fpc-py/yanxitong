"""Literature-level analysis: comparison matrix, conflict detection, research gaps.

* :func:`build_matrix` / :func:`matrix_to_csv` — render the enriched corpus as
  an N-papers x M-dimensions comparison table (CSV carries a UTF-8 BOM so
  Excel opens Chinese text correctly).
* :class:`LiteratureAnalyzer` — LLM-backed contradiction detection between
  papers (same five-field shape the experiment designer already emits:
  ``claim_a/source_a/claim_b/source_b/suggested_resolution``) and research-gap
  identification driven by sparse knowledge-graph entities plus reported
  limitations.

Source indexes returned by the model are mapped to real titles server-side and
evidence quotes are re-verified against each paper's source text, so the model
cannot invent citations.
"""

import csv
import io
import logging

from src.agents.base import AgentResult, BaseAgent, parse_llm_json
from src.analysis.evidence import source_text_of, verify_quote

logger = logging.getLogger(__name__)

TOP_N = 10
MAX_CONFLICTS = 5
MAX_GAPS = 5

CONFLICT_SYSTEM_PROMPT = (
    "你是学术文献对比分析专家。只输出 JSON，不添加解释或 Markdown 代码块。"
    "只能基于给定材料判断矛盾，禁止编造。"
)

CONFLICT_PROMPT = """研究主题：{topic}

以下是同一主题下多篇论文的要点，编号 [n] 对应论文：

{context}

请找出论文之间真实存在的结论矛盾/分歧（例如同一指标上结论相反、对同一方法效果判断冲突、对同一问题的结论不一致）。严格输出 JSON：
{{
  "conflicts": [
    {{
      "claim_a": "论文A的结论（中文概括，一句话）",
      "source_a": 论文A的编号（整数）,
      "claim_b": "论文B的结论（中文概括，一句话）",
      "source_b": 论文B的编号（整数）,
      "suggested_resolution": "为什么会产生分歧、如何解释（中文，一句话）",
      "evidence_a": "支撑结论A的原文逐字片段（英文）",
      "evidence_b": "支撑结论B的原文逐字片段（英文）"
    }}
  ]
}}

要求：
- 没有真实矛盾时返回 {{"conflicts": []}}，禁止为凑数编造分歧。
- 最多输出 5 组；两个编号必须不同。
- evidence 必须逐字摘自对应论文的标题/摘要。
"""

GAP_SYSTEM_PROMPT = (
    "你是科研选题顾问。只输出 JSON，不添加解释或 Markdown 代码块。"
    "结论必须基于给定材料，禁止泛泛而谈。"
)

GAP_PROMPT = """研究主题：{topic}

知识图谱中的稀疏/孤立实体（关系极少，可能是研究空白所在）：
{entities}

近期论文报告的局限性：
{limitations}

请归纳该主题下值得研究的方向（研究空白）。严格输出 JSON：
{{
  "gaps": [
    {{
      "gap": "研究空白（中文，一句话）",
      "reason": "判断依据（中文，一到两句）",
      "related_entities": ["相关的稀疏实体名（可为空数组）"],
      "from_limitations": ["直接支撑该空白的局限性描述（可为空数组）"]
    }}
  ]
}}

要求：
- 最多输出 5 条；结合稀疏实体与局限性，指出「还没有被充分研究」的具体方向。
- 材料不足时返回 {{"gaps": []}}，禁止编造。
"""

_CSV_HEADER = ["序号", "标题", "链接", "年份", "来源", "相关度", "相关理由",
               "研究问题", "核心方法(基线)", "数据集(样本规模)", "核心结果", "局限性"]


def build_matrix(papers: list[dict]) -> list[dict]:
    """Render enriched papers as matrix rows (order preserved)."""
    rows = []
    for index, paper in enumerate(papers, start=1):
        enrichment = paper.get("enrichment") or {}
        relevance = enrichment.get("relevance") or {}
        sources = paper.get("sources") or ([paper.get("source", "")] if paper.get("source") else [])
        rows.append({
            "index": index,
            "title": paper.get("title", ""),
            "url": paper.get("url", ""),
            "year": paper.get("year", 0),
            "sources": [s for s in sources if s],
            "relevance": {"score": int(relevance.get("score") or 0), "reason": relevance.get("reason", "")},
            "research_problem": enrichment.get("research_problem", ""),
            "methods": enrichment.get("methods", []),
            "datasets": enrichment.get("datasets", []),
            "results": enrichment.get("results", []),
            "limitations": enrichment.get("limitations", []),
            "evidence": enrichment.get("evidence", {}),
        })
    return rows


def _join_methods(methods: list[dict]) -> str:
    parts = []
    for m in methods or []:
        name = m.get("name", "")
        if not name:
            continue
        parts.append(f"{name} (基线: {m['baseline']})" if m.get("baseline") else name)
    return "; ".join(parts)


def _join_datasets(datasets: list[dict]) -> str:
    parts = []
    for d in datasets or []:
        name = d.get("name", "")
        if not name:
            continue
        parts.append(f"{name} (n={d['sample_size']})" if d.get("sample_size") else name)
    return "; ".join(parts)


def _join_results(results: list[dict]) -> str:
    parts = []
    for r in results or []:
        metric = r.get("metric", "")
        value = f"{r.get('value', '')}{r.get('unit', '')}"
        if metric or value:
            parts.append(f"{metric}: {value}".strip(": "))
    return "; ".join(parts)


def matrix_to_csv(rows: list[dict]) -> str:
    """Serialize matrix rows to CSV with a UTF-8 BOM (Excel-friendly Chinese)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_CSV_HEADER)
    for row in rows:
        relevance = row.get("relevance") or {}
        writer.writerow([
            row.get("index", ""),
            row.get("title", ""),
            row.get("url", ""),
            row.get("year", ""),
            "/".join(row.get("sources") or []),
            relevance.get("score", 0) or "",
            relevance.get("reason", ""),
            row.get("research_problem", ""),
            _join_methods(row.get("methods")),
            _join_datasets(row.get("datasets")),
            _join_results(row.get("results")),
            "; ".join(row.get("limitations") or []),
        ])
    return "\ufeff" + buffer.getvalue()


def _parse_json(response: str) -> dict:
    return parse_llm_json(response)


def _as_positive_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class LiteratureAnalyzer(BaseAgent):
    """Contradiction detection and research-gap identification across papers."""

    name = "literature_analyzer"
    description = "文献矛盾检测与研究空白识别"
    model_role = "lightweight"

    async def _execute_impl(self, state: dict) -> AgentResult:
        papers = state.get("literature_results", []) or []
        topic = state.get("research_topic") or state.get("user_query", "")
        conflicts = await self.detect_conflicts(papers, topic)
        gaps = await self.identify_gaps(papers, state.get("sparse_entities") or [], topic)
        return AgentResult(success=True, data={"conflicts": conflicts, "gaps": gaps}, confidence=1.0)

    async def detect_conflicts(self, papers: list[dict], topic: str) -> list[dict]:
        """Return verified conflicts between papers (empty list when none/failure)."""
        selected = [p for p in papers if (p.get("enrichment") or {}).get("results")][:TOP_N]
        if len(selected) < 2:
            return []
        context = "\n\n".join(self._paper_brief(i, p) for i, p in enumerate(selected, start=1))
        prompt = CONFLICT_PROMPT.format(topic=topic or "(未提供)", context=context)
        try:
            response = await self._call_llm(prompt, system_prompt=CONFLICT_SYSTEM_PROMPT, json_mode=True)
        except Exception as error:
            logger.warning("Conflict detection degraded: %s", error)
            return []

        conflicts = []
        for item in _parse_json(response).get("conflicts") or []:
            if len(conflicts) >= MAX_CONFLICTS:
                break
            item = item if isinstance(item, dict) else {}
            index_a, index_b = _as_positive_int(item.get("source_a")), _as_positive_int(item.get("source_b"))
            if not (1 <= index_a <= len(selected)) or not (1 <= index_b <= len(selected)) or index_a == index_b:
                continue
            claim_a, claim_b = str(item.get("claim_a") or "").strip(), str(item.get("claim_b") or "").strip()
            if not (claim_a and claim_b):
                continue
            paper_a, paper_b = selected[index_a - 1], selected[index_b - 1]
            evidence_a, evidence_b = str(item.get("evidence_a") or "").strip(), str(item.get("evidence_b") or "").strip()
            conflicts.append({
                "claim_a": claim_a,
                "source_a": f"[{index_a}] {paper_a.get('title', '')}",
                "source_a_url": paper_a.get("url", ""),
                "claim_b": claim_b,
                "source_b": f"[{index_b}] {paper_b.get('title', '')}",
                "source_b_url": paper_b.get("url", ""),
                "suggested_resolution": str(item.get("suggested_resolution") or "").strip(),
                "evidence_a": evidence_a,
                "evidence_b": evidence_b,
                "evidence_a_verified": verify_quote(evidence_a, source_text_of(paper_a)),
                "evidence_b_verified": verify_quote(evidence_b, source_text_of(paper_b)),
            })
        self._audit("conflicts_complete", {"papers": len(selected), "conflicts": len(conflicts)})
        return conflicts

    async def identify_gaps(self, papers: list[dict], sparse_entities: list[dict], topic: str) -> list[dict]:
        """Identify research gaps from sparse KG entities + reported limitations."""
        limitations = []
        for paper in papers[:TOP_N]:
            for limitation in (paper.get("enrichment") or {}).get("limitations") or []:
                limitations.append(f"- {paper.get('title', '')[:70]}: {limitation}")
                if len(limitations) >= 20:
                    break
            if len(limitations) >= 20:
                break

        entity_lines = [
            f"- {e.get('name', '')} ({e.get('type', '')}, 关系数 {e.get('degree', 0)})"
            for e in (sparse_entities or [])[:30] if e.get("name")
        ]
        if not limitations and not entity_lines:
            return []

        prompt = GAP_PROMPT.format(
            topic=topic or "(未提供)",
            entities="\n".join(entity_lines) or "（知识图谱暂不可用）",
            limitations="\n".join(limitations) or "（暂无抽取到的局限性）",
        )
        try:
            response = await self._call_llm(prompt, system_prompt=GAP_SYSTEM_PROMPT, json_mode=True)
        except Exception as error:
            logger.warning("Gap identification degraded: %s", error)
            return []

        gaps = []
        for item in _parse_json(response).get("gaps") or []:
            if len(gaps) >= MAX_GAPS:
                break
            item = item if isinstance(item, dict) else {}
            gap = str(item.get("gap") or "").strip()
            if not gap:
                continue
            gaps.append({
                "gap": gap,
                "reason": str(item.get("reason") or "").strip(),
                "related_entities": [str(x) for x in (item.get("related_entities") or []) if str(x).strip()][:10],
                "from_limitations": [str(x) for x in (item.get("from_limitations") or []) if str(x).strip()][:5],
            })
        self._audit("gaps_complete", {"sparse_entities": len(entity_lines), "gaps": len(gaps)})
        return gaps

    @staticmethod
    def _paper_brief(index: int, paper: dict) -> str:
        enrichment = paper.get("enrichment") or {}
        parts = [f"[{index}] {paper.get('title', '')} ({paper.get('year', 0)})"]
        results = _join_results(enrichment.get("results"))
        if results:
            parts.append(f"结果: {results}")
        methods = _join_methods(enrichment.get("methods"))
        if methods:
            parts.append(f"方法: {methods}")
        if not results:
            parts.append(f"摘要: {(paper.get('abstract') or '')[:300]}")
        return "\n".join(parts)
