"""规格② 任务规划：LLM 结构化计划解析 + 失败回退启发式计划。"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.agents.data_analyst import prompts
from src.agents.data_analyst.agent import DataAnalystAgent


def _stub(agent, responder):
    calls = []

    async def fake(prompt, system_prompt="", json_mode=False, enable_thinking=True, role=""):
        calls.append({"prompt": prompt, "json_mode": json_mode, "role": role})
        return responder(prompt) if callable(responder) else responder

    agent._call_llm = fake
    return calls


# ---- 启发式回退 --------------------------------------------------------------


def test_heuristic_plan_group_comparison():
    plan = prompts.heuristic_plan("比较实验组与对照组的差异是否显著")
    assert plan["fallback"] is True
    assert len(plan["steps"]) == 1
    assert any("检验" in m["name"] for m in plan["methods"])
    assert plan["intent_summary"] == "比较实验组与对照组的差异是否显著"


def test_heuristic_plan_correlation():
    plan = prompts.heuristic_plan("分析变量之间的相关关系并做回归")
    assert any("相关" in m["name"] for m in plan["methods"])


def test_heuristic_plan_generic_intent_has_no_methods():
    plan = prompts.heuristic_plan("帮我看看这份数据")
    assert plan["methods"] == []
    assert plan["journal_rules"]


# ---- LLM 规划路径 ------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_parses_llm_json():
    agent = DataAnalystAgent()
    plan_json = json.dumps({
        "intent_summary": "比较两组差异",
        "steps": [
            {"id": 1, "type": "clean", "description": "清洗数据", "depends_on": [], "output": "clean df"},
            {"id": 2, "type": "stats", "description": "做 t 检验", "depends_on": [1], "output": "p 值"},
        ],
        "methods": [{"name": "Welch t 检验", "why": "方差不齐", "assumptions": ["独立"], "source": "统计方法库/独立样本 t 检验"}],
        "templates": [{"name": "误差线柱状图", "when": "组间比较", "params": "±SE", "source": "绘图模板库/分组柱状图（误差线）"}],
        "journal_rules": ["字号不小于 7pt"],
    })
    calls = _stub(agent, "```json\n" + plan_json + "\n```")

    plan = await agent._plan("比较两组销量差异", {"format": "csv/utf-8", "rows": 100}, {"libraries": {}, "sources": 0})

    assert plan.get("fallback") is None
    assert [s["type"] for s in plan["steps"]] == ["clean", "stats"]
    assert plan["methods"][0]["source"].startswith("统计方法库")
    assert plan["templates"] and plan["journal_rules"]
    assert calls and calls[0]["json_mode"] is True
    assert "比较两组销量差异" in calls[0]["prompt"]


@pytest.mark.asyncio
async def test_plan_falls_back_when_llm_raises():
    agent = DataAnalystAgent()

    async def boom(*args, **kwargs):
        raise RuntimeError("api down")

    agent._call_llm = boom
    plan = await agent._plan("比较两组差异", None, {"libraries": {}, "sources": 0})

    assert plan["fallback"] is True
    assert plan["steps"]
    assert any(entry.action == "plan_failed" for entry in agent.audit_log)


@pytest.mark.asyncio
async def test_plan_falls_back_when_json_unparsable():
    agent = DataAnalystAgent()
    _stub(agent, "抱歉，我无法给出任务计划。")
    plan = await agent._plan("比较两组差异", None, {"libraries": {}, "sources": 0})
    assert plan["fallback"] is True


@pytest.mark.asyncio
async def test_plan_defaults_empty_arrays():
    agent = DataAnalystAgent()
    _stub(agent, json.dumps({"steps": [{"id": 1, "type": "stats", "description": "统计"}]}))
    plan = await agent._plan("描述性统计", None, {"libraries": {}, "sources": 0})
    assert plan["methods"] == [] and plan["templates"] == [] and plan["journal_rules"] == []
    assert plan.get("fallback") is None


# ---- 摘要注入 -----------------------------------------------------------------


def test_profile_digest_handles_missing_and_degraded():
    assert "无数据画像" in prompts.profile_digest(None)
    assert "画像降级" in prompts.profile_digest({"degraded": True, "error": "沙箱不可用"})
    digest = prompts.profile_digest({
        "format": "csv/utf-8", "rows": 90, "cols": 2,
        "columns": [{"name": "身高", "dtype": "float64", "missing_pct": 0, "unique": 80,
                     "min": 150, "max": 200, "sample": "175.2"}],
        "quality": {"issues": [{"kind": "outliers", "column": "身高", "detail": "3 个可疑异常值"}]},
    })
    assert "csv/utf-8" in digest and "身高" in digest
    assert "范围[150, 200]" in digest
    assert "可疑异常值" in digest


def test_recall_digest_lists_sources():
    digest = prompts.recall_digest({
        "libraries": {"methods": {"label": "统计方法库", "chunks": [
            {"source": "统计方法库/methods.md", "section": "独立样本 t 检验", "text": "前提与流程"},
        ]}},
        "degraded": True,
    })
    assert "统计方法库" in digest
    assert "独立样本 t 检验" in digest
    assert "关键词召回" in digest


def test_plan_steps_digest():
    text = prompts.plan_steps_digest({"steps": [
        {"id": 1, "type": "clean", "description": "清洗", "output": "干净数据"},
    ]})
    assert "1. [clean] 清洗" in text
    assert "干净数据" in text


def test_build_preamble_injects_paths_and_helpers():
    code = prompts.build_preamble("/workspace/data.csv", "实验数据.csv")
    assert '"/workspace/data.csv"' in code
    assert "save_figure" in code and "load_data" in code and "emit_findings" in code
    assert "PALETTE" in code and "np.random.seed(42)" in code
    assert prompts.FINDINGS_SENTINEL in code


def test_degraded_script_contract():
    assert "load_data()" in prompts.DEGRADED_SCRIPT
    assert "emit_findings" in prompts.DEGRADED_SCRIPT
    assert "降级" in prompts.DEGRADED_SCRIPT
