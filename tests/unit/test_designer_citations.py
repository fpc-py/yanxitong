"""Unit tests for the P5 citation gates: designer enforcement + reviewer tracing."""

import json
from unittest.mock import AsyncMock

from src.agents.academic_reviewer.agent import AcademicReviewerAgent
from src.agents.experiment_designer.agent import ExperimentDesignerAgent

PAPERS = [
    {"title": "FedProx: Federated Optimization", "arxiv_id": "1812.06127", "year": 2020},
    {"title": "SCAFFOLD: Stochastic Controlled Averaging", "arxiv_id": "1910.06378", "year": 2020},
]

DESIGN_JSON = json.dumps({
    "hypothesis": "FedProx 的近端项可提升异构联邦学习稳定性",
    "rationale": "基于文献[1]",
    "variables": {"independent": ["近端项系数"], "dependent": ["准确率"], "controlled": ["轮数"]},
    "experimental_groups": ["FedProx 组 [1]", "对照组 [2]", "未引用文献的实验组"],
    "statistical_methods": ["配对 t 检验 [1]"],
    "expected_outcomes": ["准确率提升 [9]"],  # 9 号文献不存在 → 应被剔除
    "conflicts": [],
    "recommended_validation": "在 FEMNIST 上复现 [1]",
    "risk_assessment": "低",
})


def test_enforce_citations_keeps_only_resolvable_items():
    agent = ExperimentDesignerAgent()
    plan = json.loads(DESIGN_JSON)

    gate = agent._enforce_citations(plan, PAPERS)

    assert gate["checked"] == 5 and gate["kept"] == 3 and gate["dropped"] == 2
    assert plan["experimental_groups"] == ["FedProx 组 [1]", "对照组 [2]"]
    assert plan["statistical_methods"] == ["配对 t 检验 [1]"]
    assert plan["expected_outcomes"] == []
    assert set(gate["cited_papers"]) == {"ax:1812.06127", "ax:1910.06378"}
    assert gate["cited_papers"]["ax:1812.06127"]["arxiv_id"] == "1812.06127"
    fields = {d["field"] for d in gate["dropped_items"]}
    assert fields == {"experimental_groups", "expected_outcomes"}


def test_enforce_citations_skips_without_papers():
    agent = ExperimentDesignerAgent()
    plan = {"experimental_groups": ["A", "B"], "statistical_methods": [], "expected_outcomes": []}

    gate = agent._enforce_citations(plan, [])

    assert gate["skipped"] is True and gate["checked"] == 0
    assert plan["experimental_groups"] == ["A", "B"]  # 无文献可引用时不误删


async def test_designer_execute_impl_gates_and_penalises():
    agent = ExperimentDesignerAgent()
    agent._call_llm = AsyncMock(return_value=DESIGN_JSON)
    state = {"literature_results": PAPERS, "user_query": "q", "research_topic": "federated learning"}

    result = await agent._execute_impl(state)

    design = result.data["experiment_design"]
    assert design["experimental_groups"] == ["FedProx 组 [1]", "对照组 [2]"]
    assert design["expected_outcomes"] == []
    assert result.data["citation_gate"]["dropped"] == 2
    assert result.confidence == round(0.85 * 0.9, 3)  # 有建议被剔除 → 置信度打折


async def test_designer_confidence_collapses_when_all_dropped():
    agent = ExperimentDesignerAgent()
    payload = json.dumps({
        "hypothesis": "H",
        "variables": {},
        "experimental_groups": ["无引用 A", "无引用 B"],
        "statistical_methods": [],
        "expected_outcomes": [],
    })
    agent._call_llm = AsyncMock(return_value=payload)
    state = {"literature_results": PAPERS, "user_query": "q"}

    result = await agent._execute_impl(state)

    assert result.data["experiment_design"]["experimental_groups"] == []
    assert result.confidence == 0.4


async def test_reviewer_traces_citations_to_papers():
    agent = AcademicReviewerAgent()
    agent._call_llm = AsyncMock(return_value=json.dumps({"overall_score": 80, "recommendation": "accept", "summary": "ok"}))
    state = {"writing_draft": "近端项方法[1]在异构场景有效，另见[2]。存疑引用[5]。", "literature_results": PAPERS}

    result = await agent._execute_impl(state)

    trace = result.data["citation_trace"]
    assert trace["markers"] == 3
    assert [r["marker"] for r in trace["resolved"]] == ["[1]", "[2]"]
    assert trace["resolved"][0]["paper_id"] == "ax:1812.06127"
    assert trace["unresolved"] == [5]
    assert "[5] 无对应文献" in result.data["review"]["citations"]["missing_citations"]
