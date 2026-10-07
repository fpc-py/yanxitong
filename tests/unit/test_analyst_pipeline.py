"""数据分析 9 段流水线端到端（假沙箱 + 桩 LLM，覆盖 happy / debug / 降级三径）。

不加载真实 Docker / BGE / 外部 LLM：
- FakeSandbox 脚本化返回执行结果（含 findings sentinel）；
- _call_llm 桩按 prompt 特征路由（规划/代码生成/Debug/校验/报告）；
- 知识召回桩固定返回，审计写入内存 SQLite（不碰仓库 data/audit.db）。
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.agents.data_analyst import agent as agent_mod
from src.agents.data_analyst import prompts
from src.agents.data_analyst.agent import DataAnalystAgent
from src.observability import audit_store

PROFILE = {
    "ok": True, "format": "csv/utf-8", "rows": 90, "cols": 2,
    "columns": [
        {"name": "组别", "dtype": "object", "missing_pct": 0, "unique": 2},
        {"name": "身高", "dtype": "float64", "min": 150.0, "max": 200.0, "missing_pct": 0},
    ],
    "quality": {"duplicates": 0, "issues": []},
}

CLEAN_FINDINGS = {
    "summary": "两组身高差异显著（Welch t=3.21, p=0.003）",
    "tests": [{"name": "Welch t 检验", "statistic": 3.21, "p_value": 0.003, "groups": ["A", "B"]}],
    "statistics": {"身高": {"mean": 172.0, "std": 8.0, "min": 150.0, "max": 200.0, "median": 171.0, "n": 90}},
    "percentages": {},
    "charts": ["身高分布.png"],
    "notes": [],
}

DEGRADED_FINDINGS = {
    "summary": "降级模式：完成基础统计（90 行 × 2 列）",
    "tests": [],
    "statistics": {"身高": {"mean": 172.0, "std": 8.0, "min": 150.0, "max": 200.0, "median": 171.0, "n": 90}},
    "charts": ["降级_身高_分布.png"],
    "notes": ["自动修复 3 次仍失败，已降级"],
}

GENERATED_CODE = "df = load_data()\nprint('数据形状:', df.shape)\nemit_findings({...})"
FIXED_CODE = "df = load_data()\nprint('fixed')\nemit_findings({...})"
REPORT_MD = "## 主要发现\n\n身高均值 172.0（n=90），组间差异显著（p=0.003）。"

DEFAULT_PLAN = {
    "intent_summary": "比较两组身高差异",
    "steps": [
        {"id": 1, "type": "stats", "description": "做 Welch t 检验", "depends_on": [], "output": "p 值"},
        {"id": 2, "type": "viz", "description": "画误差线柱状图", "depends_on": [1], "output": "图表"},
    ],
    "methods": [{"name": "Welch t 检验", "why": "方差不齐", "assumptions": ["独立"], "source": "统计方法库/独立样本 t 检验"}],
    "templates": [],
    "journal_rules": ["字号不小于 7pt"],
}


class FakeSandbox:
    """脚本化沙箱：按顺序返回预设结果，记录每次调用参数。"""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def execute_with_file(self, code, input_file, timeout=None, *, mount_name="data.csv", collect_to=None):
        self.calls.append({"code": code, "input_file": input_file, "mount_name": mount_name,
                           "collect_to": collect_to})
        return self._next()

    async def execute(self, code, timeout=None, *, collect_to=None):
        self.calls.append({"code": code, "collect_to": collect_to})
        return self._next()

    async def env_lock(self):
        return "# test env\npandas==2.2.0\n"

    def _next(self):
        if self.outcomes:
            return self.outcomes.pop(0)
        return {"stdout": "", "stderr": "no outcome left", "exit_code": -1, "timed_out": False,
                "figures": [], "artifacts": []}


def fail_result(stderr="NameError: name 'df' is not defined"):
    return {"stdout": "", "stderr": stderr, "exit_code": 1, "timed_out": False,
            "figures": [], "artifacts": []}


def ok_result(findings=None, stdout_extra="数据形状: (90, 2)"):
    payload = findings if findings is not None else CLEAN_FINDINGS
    sentinel = prompts.FINDINGS_SENTINEL + json.dumps(payload, ensure_ascii=True)
    return {"stdout": stdout_extra + "\n" + sentinel + "\n", "stderr": "", "exit_code": 0,
            "timed_out": False, "figures": [{"name": "身高分布.png", "data_url": "data:image/png;base64,AA=="}],
            "artifacts": [{"name": "figures/身高分布.png", "size": 10}]}


def stub_llm(agent, *, code=GENERATED_CODE, debug_code=FIXED_CODE, plan=DEFAULT_PLAN,
             validate=None, interpret=REPORT_MD):
    validation = validate if validate is not None else {"overall": "pass", "assumptions": [],
                                                        "narrative": [], "comments": []}
    calls: list[dict] = []

    async def fake(prompt, system_prompt="", json_mode=False, enable_thinking=True, role=""):
        calls.append({"role": role, "json_mode": json_mode, "prompt": prompt})
        if "任务规划模块" in prompt:
            return json.dumps(plan, ensure_ascii=False)
        if "修复下面代码" in prompt:
            return debug_code
        if "代码生成助手" in prompt:
            return code
        if "审核员" in prompt:
            return json.dumps(validation, ensure_ascii=False)
        if "撰写中文解读报告" in prompt:
            return interpret
        return ""

    agent._call_llm = fake
    return calls


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(audit_store._SCHEMA)
    monkeypatch.setattr(audit_store, "_conn", conn)

    recall_result = {
        "query": "比较两组身高差异", "degraded": False, "sources": 1,
        "libraries": {"methods": {"label": "统计方法库", "chunks": [
            {"section": "独立样本 t 检验", "text": "前提与流程", "source": "统计方法库/statistical_methods.md",
             "similarity": 0.9},
        ]}},
    }
    monkeypatch.setattr(agent_mod.knowledge_libs, "recall",
                        lambda intent, profile=None, top_k=None: recall_result)
    return tmp_path


def make_state(tmp_path, *, with_profile=True, with_data=True):
    state = {"session_id": "sess-e2e", "user_query": "比较两组身高差异"}
    if with_data:
        data = tmp_path / "demo_experiment.csv"
        data.write_text("组别,身高\nA,170\nB,180\n", encoding="utf-8")
        state["data_file_path"] = str(data)
    if with_profile:
        state["data_profile"] = {**PROFILE, "file_path": state.get("data_file_path", "")}
    return state


async def _no_profiler(*args, **kwargs):
    raise AssertionError("state 已带 data_profile，不应再调用画像")


# ---- ① happy path ------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_full_pipeline(env, monkeypatch):
    sandbox = FakeSandbox(ok_result())
    monkeypatch.setattr(agent_mod, "get_sandbox", lambda: sandbox)
    monkeypatch.setattr(agent_mod, "profile_data_file", _no_profiler)
    agent = DataAnalystAgent()
    calls = stub_llm(agent)

    state = make_state(env)
    result = await agent._execute_impl(state)

    assert result.success is True
    assert result.confidence == pytest.approx(0.9)
    report = result.data

    # 旧契约键保持
    assert report["exit_code"] == 0 and report["has_data_file"] is True
    assert report["code"] == GENERATED_CODE
    assert report["findings_data"] == CLEAN_FINDINGS
    assert report["figures"][0]["name"] == "身高分布.png"
    assert report["stdout"] and prompts.FINDINGS_SENTINEL not in report["stdout"]

    # 新契约键（画像与当前数据文件指纹一致 → 直接复用，未再调沙箱画像）
    assert report["profile"]["format"] == PROFILE["format"]
    assert report["profile"]["file_path"] == state["data_file_path"]
    assert report["task_plan"]["steps"]
    assert report["knowledge_recall"]["sources"] == 1
    assert report["validation"]["overall"] == "pass"
    assert report["report"] == REPORT_MD

    run = report["analysis_run"]
    assert run["degraded"] is False and run["attempts"] == 1
    assert run["validation_overall"] == "pass"
    assert run["run_id"]

    # 沙箱拿到了 preamble（契约 helper 与调色板）+ 正确挂载名 + 产物收集目录
    call = sandbox.calls[0]
    assert "def save_figure" in call["code"] and "emit_findings" in call["code"]
    assert "np.random.seed(42)" in call["code"]
    assert GENERATED_CODE in call["code"]
    assert call["mount_name"] == "demo_experiment.csv"
    assert Path(call["collect_to"]).is_dir()

    # 代码生成走了 coder 角色
    assert any(c["role"] == "coder" for c in calls)

    # 产出包落盘（run 目录）
    run_dir = env / "data" / "analysis" / "sess-e2e" / run["run_id"]
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "analysis.ipynb").is_file()
    assert (run_dir / "report.md").read_text(encoding="utf-8") == REPORT_MD
    assert json.loads((run_dir / "findings.json").read_text(encoding="utf-8")) == CLEAN_FINDINGS
    assert (run_dir / "environment.lock").read_text(encoding="utf-8").startswith("# test env")

    # 分阶段审计
    stage_names = [s["stage"] for s in run["stages"]]
    assert stage_names == ["profile", "recall", "plan", "codegen", "execute",
                           "validate", "interpret", "package"]
    actions = [e.action for e in agent.audit_log]
    assert "analysis_start" in actions and "execution_success" in actions


# ---- ⑤⑥ debug path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_debug_loop_recovers(env, monkeypatch):
    sandbox = FakeSandbox(fail_result(), ok_result())
    monkeypatch.setattr(agent_mod, "get_sandbox", lambda: sandbox)
    monkeypatch.setattr(agent_mod, "profile_data_file", _no_profiler)
    agent = DataAnalystAgent()
    stub_llm(agent)

    result = await agent._execute_impl(make_state(env))

    assert result.success is True
    assert result.data["analysis_run"]["degraded"] is False
    assert result.data["analysis_run"]["attempts"] == 2
    assert result.data["code"] == FIXED_CODE  # 第 2 轮执行修复后的代码
    assert FIXED_CODE in sandbox.calls[1]["code"]
    actions = [e.action for e in agent.audit_log]
    assert "execution_failed" in actions and "degraded_start" not in actions


# ---- ⑥ 降级路径 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_degraded_fallback_after_three_failures(env, monkeypatch):
    sandbox = FakeSandbox(fail_result(), fail_result("TypeError: bad groupby"),
                          fail_result("ValueError: empty"), ok_result(DEGRADED_FINDINGS))
    monkeypatch.setattr(agent_mod, "get_sandbox", lambda: sandbox)
    monkeypatch.setattr(agent_mod, "profile_data_file", _no_profiler)
    agent = DataAnalystAgent()
    calls = stub_llm(agent, validate={"overall": "fail"})  # 即便桩校验 fail，降级时也不应被调用

    result = await agent._execute_impl(make_state(env))

    assert result.success is True  # 兜底脚本跑通
    run = result.data["analysis_run"]
    assert run["degraded"] is True
    assert run["degrade_reason"] == "execution_failed_after_3_attempts"
    assert run["attempts"] == 3
    assert result.confidence == pytest.approx(0.55)
    assert result.data["findings_data"] == DEGRADED_FINDINGS

    # 第 4 次执行是静态降级脚本（无 LLM 参与）
    assert "降级" in sandbox.calls[3]["code"]
    assert len(sandbox.calls) == 4
    # 降级时跳过 LLM 校验层，但解释报告仍生成
    assert not any("审核员" in c["prompt"] for c in calls)
    assert any("撰写中文解读报告" in c["prompt"] for c in calls)
    assert result.data["report"] == REPORT_MD
    assert result.data["validation"]["llm"] is None

    actions = [e.action for e in agent.audit_log]
    assert "degraded_start" in actions and "degraded_fallback_success" in actions


# ---- ⑦ 校验失败 → 幻觉标记 -----------------------------------------------------


@pytest.mark.asyncio
async def test_validation_failure_flags_hallucination(env, monkeypatch):
    bad_findings = json.loads(json.dumps(CLEAN_FINDINGS))
    bad_findings["tests"][0]["p_value"] = 1.5
    sandbox = FakeSandbox(ok_result(bad_findings))
    monkeypatch.setattr(agent_mod, "get_sandbox", lambda: sandbox)
    monkeypatch.setattr(agent_mod, "profile_data_file", _no_profiler)
    agent = DataAnalystAgent()
    stub_llm(agent)

    result = await agent._execute_impl(make_state(env))

    assert result.data["validation"]["overall"] == "fail"
    assert result.confidence == pytest.approx(0.4)
    flags = audit_store.recent_flags("sess-e2e")
    assert any(f["layer"] == "result_validation" and f["risk_level"] == "high" for f in flags)
    assert any(e.action == "validation_failed" for e in agent.audit_log)


# ---- ① 画像指纹：换文件后旧画像必须作废 ----------------------------------------


@pytest.mark.asyncio
async def test_stale_profile_discarded_and_reprofiled(env, monkeypatch):
    profiled = {"calls": 0}

    async def fake_profile(path, sandbox=None):
        profiled["calls"] += 1
        return {**PROFILE, "file_path": path}

    sandbox = FakeSandbox(ok_result())
    monkeypatch.setattr(agent_mod, "get_sandbox", lambda: sandbox)
    monkeypatch.setattr(agent_mod, "profile_data_file", fake_profile)
    agent = DataAnalystAgent()
    stub_llm(agent)

    state = make_state(env)
    # 状态里是「上一个文件」的画像（file_path 不匹配，列名都可能不同）
    state["data_profile"] = {**PROFILE, "file_path": str(env / "old_upload.csv")}

    result = await agent._execute_impl(state)

    assert result.success is True
    assert profiled["calls"] == 1  # 指纹不符 → 重新画像
    assert result.data["profile"]["file_path"] == state["data_file_path"]
    assert any(e.action == "profile_stale_discarded" for e in agent.audit_log)


# ---- ① 画像降级不阻塞分析 ------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_degraded_does_not_block(env, monkeypatch):
    degraded_profile = {"ok": False, "degraded": True, "error": "沙箱不可用",
                        "filename": "demo_experiment.csv"}
    profiled = {"called": 0}

    async def fake_profile(path, sandbox=None):
        profiled["called"] += 1
        return degraded_profile

    sandbox = FakeSandbox(ok_result())
    monkeypatch.setattr(agent_mod, "get_sandbox", lambda: sandbox)
    monkeypatch.setattr(agent_mod, "profile_data_file", fake_profile)
    agent = DataAnalystAgent()
    stub_llm(agent)

    result = await agent._execute_impl(make_state(env, with_profile=False))

    assert profiled["called"] == 1
    assert result.success is True
    assert result.data["profile"]["degraded"] is True
    profile_stage = next(s for s in result.data["analysis_run"]["stages"] if s["stage"] == "profile")
    assert profile_stage["ok"] is False
    actions = [e.action for e in agent.audit_log]
    assert "profile_degraded" in actions


# ---- ④ 代码生成失败（无数据文件）----------------------------------------------


@pytest.mark.asyncio
async def test_codegen_empty_without_data_fails_fast(env, monkeypatch):
    monkeypatch.setattr(agent_mod, "get_sandbox", lambda: FakeSandbox())
    agent = DataAnalystAgent()
    calls = stub_llm(agent, code="")

    result = await agent._execute_impl(make_state(env, with_profile=False, with_data=False))

    assert result.success is False
    assert "未返回可执行代码" in result.error
    assert result.confidence == 0.0
    # coder 失败后回退默认模型再试一次（两次尝试均空 → 放弃）
    assert len([c for c in calls if "代码生成助手" in c["prompt"]]) == 2
    assert any(e.action == "coder_fallback" for e in agent.audit_log)


# ---- ④ coder 角色异常回退默认模型 ---------------------------------------------


@pytest.mark.asyncio
async def test_coder_role_failure_falls_back(env, monkeypatch):
    sandbox = FakeSandbox(ok_result())
    monkeypatch.setattr(agent_mod, "get_sandbox", lambda: sandbox)
    monkeypatch.setattr(agent_mod, "profile_data_file", _no_profiler)
    agent = DataAnalystAgent()

    async def fake(prompt, system_prompt="", json_mode=False, enable_thinking=True, role=""):
        if "任务规划模块" in prompt:
            return json.dumps(DEFAULT_PLAN, ensure_ascii=False)
        if role == "coder":
            raise RuntimeError("coder model unavailable")
        if "代码生成助手" in prompt:
            return GENERATED_CODE
        if "审核员" in prompt:
            return json.dumps({"overall": "pass", "assumptions": [], "narrative": [], "comments": []})
        if "撰写中文解读报告" in prompt:
            return REPORT_MD
        return ""

    agent._call_llm = fake
    result = await agent._execute_impl(make_state(env))

    assert result.success is True
    assert result.data["code"] == GENERATED_CODE
    assert any(e.action == "coder_fallback" for e in agent.audit_log)


# ---- 图节点：final_response 契约（live E2E 发现的 500：answer=None） ------------


@pytest.mark.asyncio
async def test_data_analyst_node_sets_final_response(monkeypatch):
    from src.agents.base import AgentResult
    from src.workflows import supervisor_graph

    class OkAgent:
        async def execute(self, state):
            return AgentResult(success=True, data={"report": "分析报告正文", "stdout": "raw"}, confidence=0.8)

    monkeypatch.setattr(supervisor_graph, "DataAnalystAgent", OkAgent)
    state = {"session_id": "s1", "confidence_scores": {}}
    out = await supervisor_graph.data_analyst_node(state)
    assert out["final_response"] == "分析报告正文"
    assert out["confidence_scores"]["data_analyst"] == 0.8

    class FailAgent:
        async def execute(self, state):
            return AgentResult(success=False, error="boom")

    monkeypatch.setattr(supervisor_graph, "DataAnalystAgent", FailAgent)
    out = await supervisor_graph.data_analyst_node({"session_id": "s1", "confidence_scores": {}})
    assert out["final_response"] == "Error: boom"
    assert out["error_message"] == "boom"


def test_analysis_answer_fallback_chain():
    from src.workflows.supervisor_graph import analysis_answer

    assert analysis_answer({"report": "报告", "stdout": "x"}) == "报告"
    assert analysis_answer({"stdout": "raw out"}) == "raw out"
    assert "图表" in analysis_answer({"figures": [{"name": "a.png"}]})
    assert analysis_answer({"stderr": "traceback..."}) == "traceback..."
    assert analysis_answer(None) == "分析已执行，但没有可展示的输出。"
