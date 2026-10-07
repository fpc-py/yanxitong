"""实验设计 10 段流水线端到端（桩 LLM + 假 KG/KB/沙箱，覆盖 happy / 降级 / 校验失败三径）。

不加载真实 Neo4j / 向量库 / 沙箱 / LLM：
- ``_StubStore`` 假图谱（可注入故障）返回实体/链/路径，锚点 id 契约同证据单测；
- ``knowledge_libs.recall`` 桩固定返回设计库块；``get_sandbox`` 桩提供 env_lock；
- ``_call_llm`` 桩按 prompt 特征路由（解析/诊断/候选/代码/校验/报告/经典设计）；
- 审计与先验写入内存/tmp SQLite（不碰仓库 data/）。
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.agents.experiment_designer import agent as agent_mod
from src.agents.experiment_designer import evidence as evidence_mod
from src.agents.experiment_designer import packaging, prompts
from src.knowledge import knowledge_libs, prior_store
from src.observability import audit_store
from src.tools.paper_schema import make_paper_id

import src.tools.sandbox as sandbox_mod

PAPER = {
    "title": "ConvNeXt: A ConvNet for the 2020s",
    "arxiv_id": "2201.03545",
    "year": 2022,
    "key_findings": ["modernised ConvNet matches transformers"],
    "methods": ["ConvNeXt"],
}
PAPER_ID = make_paper_id(PAPER)
KB_ID = "kb:design:0"

STAGES = ["parse", "retrieve", "diagnose", "build", "generate",
          "optimize", "rank", "plan", "validate", "package"]

PROVIDED_CONFIG = {
    "task": "图像分类",
    "model": {"name": "ResNet-50", "family": "cnn", "params_m": 25},
    "hyperparams": {"learning_rate": 0.01, "batch_size": 64, "epochs": 100},
    "data": {"name": "CIFAR-10", "n_samples": 50000},
    "resources": {"gpu": "A100", "gpu_hours": 24},
}

PARSE_JSON = {"metrics": ["accuracy"], "missing": ["gpu_hours"]}

DIAGNOSIS_JSON = {
    "bottlenecks": [
        {"type": "hyperparam_drift", "severity": "medium",
         "finding": "学习率偏离同任务先验区间",
         "evidence_refs": [KB_ID, "kg:ent:ghost"], "recommendation": "对齐先验"},
        {"type": "strategy_missing", "severity": "low",
         "finding": "缺少固定种子策略", "evidence_refs": ["kg:ent:not-real"],
         "recommendation": "补齐策略"},
    ],
    "summary": "两处瓶颈",
}

CANDIDATES_JSON = {"candidates": [
    {"route": "structure", "title": "升级骨干", "description": "换用证据中的同任务结构",
     "config_patch": {"model.name": "ConvNeXt-T"},
     "hyperparams": {"learning_rate": 0.002, "batch_size": 64, "epochs": 100},
     "evidence_refs": [KB_ID, f"kg:chain:{PAPER_ID}"],
     "expected": "结构收益（估计，需消融）", "risk": "high",
     "complexity": "high", "interpretability": "medium"},
    {"route": "hyperparam", "title": "对齐先验", "description": "学习率对齐证据区间",
     "config_patch": {"hyperparams.learning_rate": 0.003},
     "hyperparams": {"learning_rate": 0.003, "batch_size": 64, "epochs": 100},
     "evidence_refs": [KB_ID],
     "expected": "降低不收敛风险", "risk": "low",
     "complexity": "low", "interpretability": "high"},
    {"route": "strategy", "title": "补齐策略", "description": "调度/早停/多种子",
     "config_patch": {"strategy": ["cosine", "早停"]},
     "hyperparams": {"scheduler": "cosine"},
     "evidence_refs": [KB_ID],
     "expected": "提升可复现性", "risk": "low",
     "complexity": "low", "interpretability": "high"},
]}

VALIDATE_JSON = {"consistency": "pass", "issues": [], "narrative": "未见证据矛盾"}

CLASSIC_JSON = {
    "hypothesis": "升级骨干并对齐超参可提升图像分类准确率",
    "rationale": "证据中的同任务结构优于当前模型",
    "variables": {"independent": ["模型结构", "学习率"], "dependent": ["准确率"],
                  "controlled": ["数据划分"]},
    "experimental_groups": ["结构升级组 [1]", "对照组 [1]"],
    "statistical_methods": ["配对 t 检验 [1]"],
    "expected_outcomes": ["准确率提升 [1]"],
    "conflicts": [],
    "recommended_validation": "3 种子重复实验",
    "risk_assessment": "中等",
}

TRAIN_CODE = '''# 本脚本在具备相应 GPU 与依赖的训练环境执行（沙箱仅做静态校验）
import random

import numpy as np
import torch

SEED = 42
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)

CFG = {"lr": 0.003, "batch_size": 64, "optimizer": "adamw", "epochs": 100}


def main():
    print("train", CFG["lr"])


if __name__ == "__main__":
    main()
'''

REPORT_MD = "# 实验方案报告\n\n## 研究假设\n\n升级骨干与对齐超参（估计收益，需验证）。\n"

DEFAULT_RESPONSES = {
    "实验配置解析器": json.dumps(PARSE_JSON, ensure_ascii=False),
    "实验方案审计专家": json.dumps(DIAGNOSIS_JSON, ensure_ascii=False),
    "实验方案生成器": json.dumps(CANDIDATES_JSON, ensure_ascii=False),
    "机器学习工程专家": TRAIN_CODE,
    "实验方案校验者": json.dumps(VALIDATE_JSON, ensure_ascii=False),
    "科研实验方案报告撰写者": REPORT_MD,
    "科研实验设计专家": json.dumps(CLASSIC_JSON, ensure_ascii=False),
}

DESIGNER_CFG = SimpleNamespace(
    max_runs=5, recall_top_k=6, n_trials=24, top_k=3, deep_verify=True,
    max_gpu_hours=24.0, max_memory_gb=16.0, weights=None,
)

KB_RESULT = {
    "query": "升级 ResNet-50 的图像分类实验",
    "libraries": {"design": {"label": "实验设计库", "chunks": [
        {"section": "超参先验区间（学习率）", "text": "CNN 用 SGD 0.05–0.1",
         "source": "实验设计库/design_priors.md", "similarity": 0.82},
    ]}},
    "degraded": False,
    "sources": 1,
}


class _StubStore:
    """假图谱存储：返回固定实体/链/路径；``fail=True`` 模拟 Neo4j 不可用。"""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls: list[tuple] = []

    async def search_entities_multi(self, keywords, limit=200, scope=None):
        if self.fail:
            raise RuntimeError("neo4j unavailable")
        self.calls.append(("entities", list(keywords), scope))
        return [
            {"entity_id": "ent:resnet50", "name": "ResNet-50", "type": "Method"},
            {"entity_id": "ent:cifar10", "name": "CIFAR-10", "type": "Dataset"},
        ]

    async def chain_query(self, paper_ids, limit=40):
        if self.fail:
            raise RuntimeError("neo4j unavailable")
        self.calls.append(("chains", list(paper_ids)))
        return [{
            "paper_id": PAPER_ID, "paper_title": PAPER["title"], "year": 2022,
            "methods": ["ConvNeXt"], "datasets": [{"name": "ImageNet", "n": None}],
            "metrics": [{"name": "Top-1", "value": 87.8, "unit": ""}],
        }]

    async def multi_hop_paths(self, entry_ids, hops=2, limit=120):
        if self.fail:
            raise RuntimeError("neo4j unavailable")
        self.calls.append(("paths", list(entry_ids), hops))
        return {"nodes": [], "edges": [], "paths": [{"edges": [
            {"source": entry_ids[0], "target": "ent:cifar10", "type": "USES_DATASET",
             "evidence": "trained on CIFAR-10", "evidence_source": "p1"},
        ]}]}


async def _graph_ok():
    return _StubStore()


async def _graph_down():
    raise RuntimeError("neo4j unavailable")


def _kb_recall(intent, profile=None, top_k=None, libraries=None):
    return KB_RESULT


class _StubSandbox:
    async def env_lock(self):
        return "torch==2.1.0\nnumpy==1.26.0\n"


def stub_llm(agent, *, overrides=None, boom=False):
    """按 prompt 特征路由的桩 LLM；``boom=True`` 模拟模型服务整体不可用。"""
    calls: list[dict] = []
    routes = {**DEFAULT_RESPONSES, **(overrides or {})}

    async def fake(prompt, system_prompt="", json_mode=False, enable_thinking=True, role=""):
        calls.append({"role": role, "json_mode": json_mode, "prompt": prompt})
        if boom:
            raise RuntimeError("llm unavailable")
        for marker, payload in routes.items():
            if marker in prompt:
                return payload
        return ""

    agent._call_llm = fake
    return calls


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(packaging, "DESIGN_DIR", str(tmp_path / "design"))
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(audit_store._SCHEMA)
    monkeypatch.setattr(audit_store, "_conn", conn)
    monkeypatch.setattr(prior_store, "DB_PATH", str(tmp_path / "experiments.db"))
    monkeypatch.setattr(prior_store, "_conn", None)

    settings = SimpleNamespace(designer=DESIGNER_CFG)
    monkeypatch.setattr(agent_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(evidence_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(sandbox_mod, "get_sandbox", lambda: _StubSandbox())
    monkeypatch.setattr(knowledge_libs, "recall", _kb_recall)
    monkeypatch.setattr(evidence_mod, "get_graph_store", _graph_ok)
    yield tmp_path
    if prior_store._conn is not None:
        prior_store._conn.close()
        prior_store._conn = None


def make_state():
    return {
        "session_id": "sess-design",
        "user_query": "升级 ResNet-50 的图像分类实验",
        "experiment_config": PROVIDED_CONFIG,
        "literature_results": [PAPER],
    }


# ---- ①-⑫ happy path ----------------------------------------------------------


async def test_pipeline_happy_path_full_engine(env):
    agent = agent_mod.ExperimentDesignerAgent()
    calls = stub_llm(agent)

    result = await agent._execute_impl(make_state())

    assert result.success is True
    engine = result.data["design_engine"]
    run = result.data["design_run"]

    # 经典契约键保持
    assert result.data["has_conflicts"] is False and result.data["conflict_count"] == 0
    assert result.data["citation_gate"]["checked"] == 4
    assert result.data["citation_gate"]["kept"] == 4
    assert result.confidence == pytest.approx(0.85)

    # ① 配置解析（provided 优先）+ ② 证据锚点 + ① 诊断引用门控
    assert engine["config"]["model"]["name"] == "ResNet-50"
    assert engine["config_source"] == "provided"
    anchors = {a["id"] for a in engine["evidence"]["anchors"]}
    assert {KB_ID, f"kg:chain:{PAPER_ID}", f"paper:{PAPER_ID}"} <= anchors
    bottlenecks = engine["diagnosis"]["bottlenecks"]
    assert len(bottlenecks) == 1
    assert bottlenecks[0]["evidence_refs"] == [KB_ID]  # ghost 引用被过滤
    assert engine["diagnosis"]["dropped"] == 1          # 无证据瓶颈被剔除

    # ④ 候选 + ⑤⑥ 优化排序
    assert len(engine["candidates"]) == 3
    opt = engine["optimization"]
    assert opt["ok"] is True and opt["top_k"] and opt["pareto"]
    winner = opt["top_k"][0]
    assert engine["recommended"]["_meta"]["candidate_id"] == winner["candidate_id"]
    assert "估计值" in engine["recommended"]["_meta"]["estimates_note"]

    # ⑧ 验证计划 + ⑨ 静态校验（stub env_lock：torch/numpy 齐备 → 无缺失）
    assert engine["plan"]["significance"]["alpha"] == 0.05
    assert engine["plan"]["rollback"] and engine["plan"]["reproducibility"]
    assert engine["static_check"]["syntax_ok"] is True
    assert engine["static_check"]["missing"] == []

    # ⑦⑪ 校验层
    assert engine["validation"]["overall"] == "pass"
    assert engine["validation"]["llm"]["consistency"] == "pass"
    assert engine["validation"]["feasibility"]["dimensions"] == [
        "resource", "dependency", "data", "ethics", "reproducibility"]
    assert engine["report"] == REPORT_MD.strip()

    # ⑫ 编排与产出包
    assert run["degraded"] is False
    assert [s["stage"] for s in run["stages"]] == STAGES
    assert run["validation_overall"] == "pass" and run["files"]
    run_dir = env / "design" / "sess-design" / run["run_id"]
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == run["run_id"] and len(manifest["stages"]) == 10
    assert (run_dir / "code" / "train.py").read_text(encoding="utf-8") == TRAIN_CODE.strip()
    saved = json.loads((run_dir / "recommended.json").read_text(encoding="utf-8"))
    assert saved["_meta"]["candidate_id"] == winner["candidate_id"]

    # 代码生成走了 coder 角色
    assert any(c["role"] == "coder" and "机器学习工程专家" in c["prompt"] for c in calls)


# ---- ②⑤ 降级：KG + LLM 全不可用（启发式候选 + 结构化回退报告） -----------------


async def test_pipeline_survives_kg_and_llm_outage(env, monkeypatch):
    monkeypatch.setattr(evidence_mod, "get_graph_store", _graph_down)
    agent = agent_mod.ExperimentDesignerAgent()
    stub_llm(agent, boom=True)

    result = await agent._execute_impl(make_state())

    assert result.success is True
    engine = result.data["design_engine"]
    run = result.data["design_run"]

    # 证据降级但 KB/文献锚点仍在
    assert engine["evidence"]["degraded"] is True
    assert "neo4j" in engine["evidence"]["kg"]["error"]
    assert {c["route"] for c in engine["candidates"]} == set(prompts.VALID_ROUTES)
    assert all(c["source"] == "heuristic" for c in engine["candidates"])
    assert all(c["evidence_refs"] for c in engine["candidates"])

    # 优化仍产出胜者；代码生成失败 → 无静态校验；LLM 复核降级
    assert engine["optimization"]["ok"] is True
    assert engine["static_check"] is None
    assert engine["validation"]["llm"]["degraded"] is True

    # 报告回退为结构化版本；run 标记 degraded 且 10 段完整
    assert engine["report"].startswith("# 实验方案报告")
    assert run["degraded"] is True
    assert [s["stage"] for s in run["stages"]] == STAGES
    retrieve_stage = next(s for s in run["stages"] if s["stage"] == "retrieve")
    assert retrieve_stage["ok"] is False and retrieve_stage["degraded"] is True

    actions = [e.action for e in agent.audit_log]
    for action in ("parse_failed", "diagnose_failed", "classic_design_failed",
                   "coder_fallback", "report_failed"):
        assert action in actions, action


# ---- ⑪ 校验失败：越界数值 → overall=fail → 置信度封顶 + 幻觉标记 ----------------


async def test_pipeline_validation_fail_caps_confidence(env):
    bad = {"candidates": [
        {"route": "hyperparam", "title": "越界学习率",
         "hyperparams": {"learning_rate": 2.5, "batch_size": 64},
         "evidence_refs": [KB_ID], "risk": "high",
         "complexity": "low", "interpretability": "low"},
        *CANDIDATES_JSON["candidates"][1:],
    ]}
    agent = agent_mod.ExperimentDesignerAgent()
    stub_llm(agent, overrides={"实验方案生成器": json.dumps(bad, ensure_ascii=False)})

    result = await agent._execute_impl(make_state())

    assert result.success is True
    engine = result.data["design_engine"]
    assert engine["validation"]["overall"] == "fail"
    # LLM 复核 pass，但确定性层 fail 优先
    assert engine["validation"]["llm"]["consistency"] == "pass"
    domain = next(c for c in engine["validation"]["deterministic"]["checks"]
                  if c["id"] == "numeric_domain")
    assert domain["ok"] is False and "learning_rate=2.5" in domain["finding"]

    assert result.confidence == pytest.approx(0.4)
    run = result.data["design_run"]
    assert run["validation_overall"] == "fail"
    validate_stage = next(s for s in run["stages"] if s["stage"] == "validate")
    assert validate_stage["ok"] is False and validate_stage["overall"] == "fail"

    flags = audit_store.recent_flags("sess-design")
    assert any(f["layer"] == "design_validation" and f["risk_level"] == "high" for f in flags)
    assert any(e.action == "validation_failed" for e in agent.audit_log)


# ---- ② 证据锚点全空：无候选但管道不崩（无引用不建议的极端情形） ------------------


async def test_pipeline_without_anchors_yields_no_candidates(env, monkeypatch):
    monkeypatch.setattr(evidence_mod, "get_graph_store", _graph_down)
    monkeypatch.setattr(knowledge_libs, "recall",
                        lambda *a, **k: {"libraries": {}, "degraded": True, "sources": 0})
    state = make_state()
    state["literature_results"] = []  # 文献锚点也清空
    agent = agent_mod.ExperimentDesignerAgent()
    stub_llm(agent)

    result = await agent._execute_impl(state)

    assert result.success is True
    engine = result.data["design_engine"]
    assert engine["evidence"]["anchors"] == []
    assert engine["candidates"] == []
    assert engine["optimization"]["ok"] is False
    assert engine["recommended"] == {} and engine["static_check"] is None
    run = result.data["design_run"]
    assert [s["stage"] for s in run["stages"]] == STAGES
    rank_stage = next(s for s in run["stages"] if s["stage"] == "rank")
    assert rank_stage["ok"] is False and rank_stage["winner"] == ""
