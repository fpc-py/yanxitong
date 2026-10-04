"""Writing Assistant Agent — 论文写作辅助智能体."""

import logging
from typing import Any

from src.agents.base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)

SECTION_PROMPTS = {
    "abstract": """基于以下研究内容，撰写学术论文摘要（200-300字，中文）。
研究主题: {topic}
主要发现: {findings}
方法: {methods}
摘要:""",

    "introduction": """撰写学术论文引言（500-800字，中文）。
研究主题: {topic}
背景文献: {literature}
研究空白: {gap}
本文贡献: {contribution}
引言:""",

    "methods": """基于实验设计，撰写方法部分（中文）。
实验设计: {design}
数据集: {datasets}
评估指标: {metrics}
方法:""",

    "results": """基于数据分析结果，撰写结果部分（中文）。
分析结果: {results}
图表说明: {figures}
结果:""",

    "discussion": """撰写讨论部分（中文）。
主要发现: {findings}
与文献对比: {comparison}
局限性: {limitations}
未来工作: {future_work}
讨论:""",

    "full_paper": """基于以下所有信息，撰写一篇完整的中文学术论文初稿。

研究主题: {topic}
文献综述: {literature}
实验设计: {design}
数据分析结果: {results}

论文结构要求:
1. 标题（中英文）
2. 摘要 + 关键词
3. 引言（含文献综述）
4. 方法
5. 实验与结果
6. 讨论
7. 结论
8. 参考文献（使用[1][2]格式标注）

完整论文:""",
}


class WritingAssistantAgent(BaseAgent):
    name = "writing_assistant"
    description = "论文写作辅助智能体 — 生成摘要/引言/方法/结果/讨论/全文"
    model_role = "deep_reasoning"

    @staticmethod
    def _as_dict(value: Any) -> dict:
        """Coerce a possibly-missing/wrongly-typed state value to a dict."""
        return value if isinstance(value, dict) else {}

    async def _execute_impl(self, state: dict) -> AgentResult:
        section = state.get("writing_section", "full_paper")
        topic = state.get("research_topic", state.get("user_query", "未指定研究主题"))

        # Literature context: top-10 paper titles/years/abstract snippets
        papers = state.get("literature_results", []) or []
        lit_text = "\n".join(
            f"[{i+1}] {p.get('title','')} ({p.get('year','')}) — {p.get('abstract','')[:200]}"
            for i, p in enumerate(papers[:10])
        ) if papers else "暂无文献"

        # Experiment context: design + findings (tolerate malformed shapes)
        exp = self._as_dict(state.get("experiment_results", {}))
        design = self._as_dict(exp.get("design", {}))
        variables = self._as_dict(design.get("variables", {}))
        findings = exp.get("findings", exp.get("stdout", "暂无分析结果"))
        if isinstance(findings, list):
            findings = "\n".join(str(item) for item in findings[:10])

        # Build format args
        fmt = {
            "topic": topic,
            "literature": lit_text[:3000],
            "findings": str(findings)[:2000],
            "methods": str(variables)[:1000],
            "design": str(design)[:2000],
            "datasets": str(variables.get("independent", []))[:500],
            "metrics": str(design.get("statistical_methods", []))[:500],
            "results": str(findings)[:2000],
            "figures": "",
            "gap": "现有研究在以下方面存在不足：1) ...",
            "contribution": f"本文针对{topic}提出以下贡献：1) ...",
            "comparison": str(design.get("conflicts", []))[:1000],
            "limitations": "本研究存在以下局限：1) 数据规模有限...",
            "future_work": "未来可从以下方向拓展：1) ...",
        }

        prompt_template = SECTION_PROMPTS.get(section, SECTION_PROMPTS["full_paper"])
        self._audit("writing_start", {"section": section, "topic": str(topic)[:100]})

        prompt = prompt_template.format(**fmt)
        result_text = await self._call_llm(prompt)

        self._audit("writing_complete", {"section": section, "length": len(result_text)})

        return AgentResult(
            success=True,
            data={
                "section": section,
                "content": result_text,
                "topic": topic,
            },
            confidence=0.85,
        )
