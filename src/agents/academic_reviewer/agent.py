"""Academic Reviewer Agent v2.0 — 增强学术审稿智能体 with IMRaD + format compliance."""

import json
import logging
import re

from src.agents.base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

REVIEW_PROMPT_V2 = """你是一位资深学术审稿人。请对以下论文草稿进行严格审阅，返回JSON。

论文草稿:
{draft}

评审维度:
1. IMRaD结构完整性: Introduction-Methods-Results-and-Discussion 是否齐全
2. 逻辑一致性: 论点是否前后一致，推理是否严密
3. 引用完整性: 关键声明是否有文献支撑（检查[{num_citations}]引用标注）
4. 数据与结论一致性: 数据是否能支撑结论
5. 格式合规: 是否符合{style}格式规范
6. 语言质量: 学术语言是否规范
7. 创新性评估: 与现有文献对比的创新程度

返回JSON（不要markdown代码块）:
{{
    "overall_score": 0-100,
    "recommendation": "accept|minor_revision|major_revision|reject",
    "structure": {{"score": 0-100, "imrad_complete": true, "issues": [], "suggestions": []}},
    "logic": {{"score": 0-100, "issues": [], "suggestions": []}},
    "citations": {{"score": 0-100, "total_citations_found": 0, "missing_citations": [], "suggestions": []}},
    "data_consistency": {{"score": 0-100, "issues": [], "suggestions": []}},
    "format": {{"score": 0-100, "style": "{style}", "issues": [], "suggestions": []}},
    "language": {{"score": 0-100, "issues": [], "suggestions": []}},
    "novelty": {{"score": 0-100, "assessment": ""}},
    "strengths": ["优点1"],
    "weaknesses": ["缺点1"],
    "summary": "总体评审意见（中文，300字以内）",
    "detailed_comments": "逐段详细意见",
    "revision_checklist": ["修改项1", "修改项2"]
}}"""


class AcademicReviewerAgent(BaseAgent):
    name = "academic_reviewer"
    description = "学术审稿智能体 v2.0 — IMRaD结构检查 + 逻辑一致性 + 引用完整性 + 格式合规(APA/MLA/GB/T 7714)"
    model_role = "deep_reasoning"

    async def _execute_impl(self, state: dict) -> AgentResult:
        draft = state.get("writing_draft", "")
        style = state.get("citation_style", "GB/T 7714")

        if not draft:
            papers = state.get("literature_results", []) or []
            query = state.get("user_query", state.get("research_topic", ""))
            if query and papers:
                draft = await self._generate_lit_review(query, papers)
                self._audit("auto_draft_generated", {"length": len(draft)})
            else:
                return AgentResult(
                    success=False, error="No draft and insufficient context", confidence=0.0
                )

        self._audit("review_start", {"draft_length": len(draft), "style": style})

        # Count citation markers for context
        num_citations = len(re.findall(r"\[\d+\]", draft))

        prompt = REVIEW_PROMPT_V2.format(
            draft=draft[:12000], style=style, num_citations=num_citations
        )
        resp = await self._call_llm(prompt)

        review = self._parse_review(resp)

        self._audit("review_complete", {"overall_score": review.get("overall_score", 0)})

        return AgentResult(
            success=True,
            data={
                "review": review,
                "draft": draft[:5000],
                "style": style,
            },
            confidence=review.get("overall_score", 60) / 100.0,
        )

    def _parse_review(self, resp: str) -> dict:
        try:
            resp = resp.strip()
            if resp.startswith("```"):
                resp = resp.split("\n", 1)[-1]
                if resp.endswith("```"):
                    resp = resp[:-3]
                resp = resp.strip()
            return json.loads(resp)
        except json.JSONDecodeError:
            return {
                "overall_score": 60,
                "recommendation": "minor_revision",
                "summary": resp[:500],
                "structure": {
                    "score": 60, "imrad_complete": False, "issues": [], "suggestions": []
                },
                "logic": {"score": 60, "issues": [], "suggestions": []},
                "citations": {
                    "score": 60, "total_citations_found": 0,
                    "missing_citations": [], "suggestions": []
                },
                "data_consistency": {"score": 60, "issues": [], "suggestions": []},
                "format": {
                    "score": 60, "style": "GB/T 7714", "issues": [], "suggestions": []
                },
                "language": {"score": 60, "issues": [], "suggestions": []},
                "novelty": {"score": 60, "assessment": ""},
                "strengths": [],
                "weaknesses": [],
                "detailed_comments": resp[:500],
                "revision_checklist": [],
            }

    async def _generate_lit_review(self, topic: str, papers: list[dict]) -> str:
        papers_text = "\n\n".join(
            f"[{i+1}] {p.get('title','')} ({p.get('year','')})\n{p.get('abstract','')[:500]}"
            for i, p in enumerate(papers[:15])
        )
        prompt = f"""撰写关于"{topic}"的文献综述（中文，800-1200字）。
按主题组织（不要逐篇罗列），指出研究趋势、主要方法和存在的空白。
每处引用使用[1][2]标注。

文献:
{papers_text}

文献综述:"""
        return await self._call_llm(prompt)
