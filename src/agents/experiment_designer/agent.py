"""Experiment Designer Agent — 实验设计智能体.

Generates a structured experiment plan from literature findings and data-analysis
results.  It uses a deep-reasoning LLM to:

    - formulate a research hypothesis with its rationale,
    - design the experiment (independent / dependent / controlled variables,
      experimental groups, statistical methods, expected outcomes),
    - detect contradictions between sources (文献A 说 X，文献B 说 Y) and propose
      a resolution,
    - recommend a validation approach and assess experiment risk.

The agent reads ``literature_results`` and ``experiment_results`` from the shared
LangGraph state and returns an :class:`AgentResult` carrying the structured plan
under ``data["experiment_design"]``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.agents.base import AgentResult, BaseAgent
from src.tools.paper_schema import make_paper_id

logger = logging.getLogger(__name__)

_CITE_RE = re.compile(r"\[(\d+)\]")

DESIGN_PROMPT = """你是一个科研实验设计专家。请基于文献调研结果和数据分析发现，设计实验方案。

文献发现:
{literature}

数据分析发现:
{data_findings}

请设计实验方案，返回JSON (no markdown fences):
{{
    "hypothesis": "研究假设",
    "rationale": "假设依据",
    "variables": {{
        "independent": ["自变量1"],
        "dependent": ["因变量1"],
        "controlled": ["控制变量1"]
    }},
    "experimental_groups": ["实验组1 [1]", "对照组 [2]"],
    "statistical_methods": ["统计方法1 [1]"],
    "expected_outcomes": ["预期结果1 [1]"],
    "conflicts": [
        {{"claim_a": "文献A的观点", "source_a": "文献A标题", "claim_b": "文献B的观点", "source_b": "文献B标题", "suggested_resolution": "建议的验证方法"}}
    ],
    "recommended_validation": "推荐的验证方案",
    "risk_assessment": "实验风险评估"
}}

引用要求：experimental_groups、statistical_methods、expected_outcomes 的每一项都必须以 [编号]
标注其文献依据，编号与「文献发现」列表一致（如 [1]、[2]）；没有文献依据的条目一律不要输出。
如果没有发现冲突，conflicts为空数组。所有文本字段使用中文。"""


# Default plan skeleton used whenever the model output cannot be parsed as JSON.
_EMPTY_PLAN: dict[str, Any] = {
    "hypothesis": "",
    "rationale": "",
    "variables": {"independent": [], "dependent": [], "controlled": []},
    "experimental_groups": [],
    "statistical_methods": [],
    "expected_outcomes": [],
    "conflicts": [],
    "recommended_validation": "",
    "risk_assessment": "",
}


def _strip_code_fences(text: str) -> str:
    """Remove leading/trailing markdown code fences from an LLM completion."""
    text = text.strip()
    if text.startswith("```"):
        # Drop the opening fence (```json / ```python / ```).
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _format_literature(papers: list[dict[str, Any]], limit: int = 10) -> str:
    """Render retrieved papers into a compact, prompt-friendly digest."""
    if not papers:
        return "暂无文献数据"

    blocks: list[str] = []
    for i, paper in enumerate(papers[:limit]):
        title = paper.get("title") or "(无标题)"
        year = paper.get("year") or ""
        findings = paper.get("key_findings") or []
        if not findings:
            abstract = (paper.get("abstract") or "").strip()
            findings = [abstract[:200]] if abstract else []
        findings_text = "; ".join(findings) if findings else "无明确发现"
        methods = paper.get("methods") or []
        methods_text = (
            f"\n方法: {', '.join(methods[:5])}" if methods else ""
        )
        blocks.append(
            f"[{i + 1}] {title} ({year})\n"
            f"发现: {findings_text}{methods_text}"
        )
    return "\n\n".join(blocks)


def _format_data_findings(experiment_results: dict[str, Any] | None) -> str:
    """Render the data-analyst report into prompt-friendly text."""
    if not experiment_results:
        return "暂无数据分析结果"

    text = experiment_results.get("stdout") or ""
    if not text:
        findings = experiment_results.get("findings") or []
        text = "\n".join(findings) if findings else "暂无数据分析结果"

    text = str(text)
    if len(text) > 2000:
        text = text[:2000] + "..."
    return text


class ExperimentDesignerAgent(BaseAgent):
    """实验设计智能体 — 假设生成、变量设计、冲突检测、验证方案推荐."""

    name = "experiment_designer"
    description = "实验设计智能体 — 假设生成、变量设计、冲突检测、验证方案推荐"
    model_role = "deep_reasoning"

    async def _execute_impl(self, state: dict[str, Any]) -> AgentResult:
        papers = state.get("literature_results") or []
        experiment_results = state.get("experiment_results") or {}
        query = state.get("user_query") or state.get("research_topic") or ""

        lit_text = _format_literature(papers)
        data_text = _format_data_findings(experiment_results)

        self._audit(
            "design_start",
            {"papers": len(papers), "has_data": bool(experiment_results),
             "query": query[:100]},
        )

        prompt = DESIGN_PROMPT.format(
            literature=lit_text[:5000], data_findings=data_text
        )
        resp = await self._call_llm(prompt)

        design = self._parse(resp, query)

        # 规格④引用门控：建议条目必须引用带图谱身份的文献，无引用的剔除
        gate = self._enforce_citations(design, papers)
        self._audit("citation_gate", {k: gate[k] for k in ("checked", "kept", "dropped", "skipped")})

        conflict_count = len(design.get("conflicts", []) or [])
        has_conflicts = conflict_count > 0

        self._audit(
            "design_complete",
            {
                "hypothesis": design.get("hypothesis", "")[:100],
                "conflicts": conflict_count,
                "methods": len(design.get("statistical_methods", []) or []),
            },
        )

        # Higher confidence when conflicts were surfaced (more rigorous output);
        # baseline confidence when a concrete hypothesis was produced.
        confidence = 0.9 if has_conflicts else 0.85
        if not design.get("hypothesis"):
            confidence = 0.5
        if gate["checked"] and not gate["kept"]:
            confidence = min(confidence, 0.4)  # 全部建议无引用依据
        elif gate["dropped"]:
            confidence = round(confidence * 0.9, 3)

        return AgentResult(
            success=True,
            data={
                "experiment_design": design,
                "has_conflicts": has_conflicts,
                "conflict_count": conflict_count,
                "citation_gate": gate,
            },
            confidence=confidence,
        )

    def _enforce_citations(self, plan: dict[str, Any], papers: list[dict[str, Any]]) -> dict[str, Any]:
        """规格④：建议条目必须引用检索到的文献（KG Paper 节点身份），无引用剔除。

        文献编号与 :func:`_format_literature` 的 [n] 一致（前 10 篇）；条目中的
        每个 [n] 都必须解析到一篇真实论文。没有文献可引用时跳过门控（降级）。
        """
        resolvable: dict[int, dict[str, Any]] = {}
        for i, paper in enumerate(papers[:10]):
            pid = make_paper_id(paper)
            if pid:
                resolvable[i + 1] = {
                    "paper_id": pid,
                    "title": paper.get("title", ""),
                    "arxiv_id": paper.get("arxiv_id", ""),
                }

        stats: dict[str, Any] = {
            "checked": 0, "kept": 0, "dropped": 0,
            "dropped_items": [], "cited_papers": {}, "skipped": False,
        }
        if not resolvable:
            stats["skipped"] = True
            stats["reason"] = "无可引用文献"
            return stats

        for field in ("experimental_groups", "statistical_methods", "expected_outcomes"):
            kept: list[str] = []
            for item in plan.get(field) or []:
                stats["checked"] += 1
                refs = [resolvable[int(n)] for n in _CITE_RE.findall(str(item)) if int(n) in resolvable]
                if refs:
                    kept.append(item)
                    stats["kept"] += 1
                    for ref in refs:
                        stats["cited_papers"][ref["paper_id"]] = ref
                else:
                    stats["dropped"] += 1
                    stats["dropped_items"].append({"field": field, "item": str(item)[:120]})
            plan[field] = kept
        return stats

    def _parse(self, resp: str, query: str) -> dict[str, Any]:
        """Parse the model's JSON plan, falling back to a degraded skeleton."""
        try:
            design = json.loads(_strip_code_fences(resp))
            if not isinstance(design, dict):
                raise json.JSONDecodeError("not an object", resp, 0)
        except (json.JSONDecodeError, ValueError):
            logger.warning("ExperimentDesigner: model output was not valid JSON")
            design = {
                **_EMPTY_PLAN,
                "hypothesis": query or "无法解析的实验设计",
                "rationale": (resp or "")[:500],
            }

        # Normalise the schema so downstream consumers always find every key.
        plan = {**_EMPTY_PLAN, **design}
        variables = dict(plan["variables"])
        variables.setdefault("independent", [])
        variables.setdefault("dependent", [])
        variables.setdefault("controlled", [])
        plan["variables"] = variables
        for key in (
            "experimental_groups",
            "statistical_methods",
            "expected_outcomes",
            "conflicts",
        ):
            if not isinstance(plan.get(key), list):
                plan[key] = []
        if not isinstance(plan.get("hypothesis"), str):
            plan["hypothesis"] = str(plan.get("hypothesis", ""))
        return plan