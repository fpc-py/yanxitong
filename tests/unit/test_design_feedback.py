"""规格⑩闭环单元测试：实验结果回流 —— 先验存储必写、KG 尽力写入、失败全降级。

``record_feedback`` 契约：run 不存在 → ``{"ok": False, "error": "设计运行不存在"}``；
其余路径先验存储必须落一行（闭环的数据基础），KG 仅在 ``kg.build_enabled`` 且
Neo4j 可用时写入 Experiment 节点 + ACHIEVES/USES_DATASET 边，失败只降级 + 审计。
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from src.agents.experiment_designer import agent as agent_mod
from src.agents.experiment_designer import packaging
from src.knowledge import prior_store
from src.knowledge.graph_store import make_entity_id
from src.observability import audit_store

KG_SETTINGS = SimpleNamespace(
    build_enabled=True,
    entity_types=["Experiment", "Method", "Metric", "Dataset"],
    relation_types=["ACHIEVES", "USES_DATASET"],
)

RECOMMENDED = {
    "task": "图像分类",
    "model": {"name": "ResNet-50"},
    "data": {"name": "CIFAR-10"},
    "metrics": ["accuracy"],
    "hyperparams": {"learning_rate": 0.01},
    "_meta": {"candidate_id": "c1", "title": "升级", "route": "structure"},
}


class _KGStore:
    """假图谱存储：记录写入的实体/边；``fail=True`` 模拟 Neo4j 不可用。"""

    def __init__(self, fail=False):
        self.fail = fail
        self.entities: list[dict] = []
        self.edges: list[dict] = []

    async def upsert_entities(self, entities, session_id=""):
        if self.fail:
            raise RuntimeError("neo4j connection refused")
        self.entities.extend(entities)
        return len(entities)

    async def upsert_edges(self, edges, session_id=""):
        if self.fail:
            raise RuntimeError("neo4j connection refused")
        self.edges.extend(edges)
        return len(edges)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(packaging, "DESIGN_DIR", str(tmp_path / "design"))
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(audit_store._SCHEMA)
    monkeypatch.setattr(audit_store, "_conn", conn)
    monkeypatch.setattr(prior_store, "DB_PATH", str(tmp_path / "experiments.db"))
    monkeypatch.setattr(prior_store, "_conn", None)
    monkeypatch.setattr(agent_mod, "get_settings", lambda: SimpleNamespace(kg=KG_SETTINGS))
    yield tmp_path
    if prior_store._conn is not None:
        prior_store._conn.close()
        prior_store._conn = None


def _make_run(session="s1"):
    run_id, run_dir = packaging.new_run_dir(session)
    packaging.assemble_run(
        run_dir, session_id=session, run_id=run_id, intent="升级模型",
        stages=[{"stage": "package", "ok": True, "ms": 2}],
        candidates=[{"id": "c1", "route": "structure", "title": "升级"}],
        recommended=RECOMMENDED, code="print('x')",
        evidence={}, validation={"overall": "pass"}, report_md="# 报告",
    )
    return run_id


def _patch_graph(monkeypatch, store):
    async def _gs():
        return store

    monkeypatch.setattr(agent_mod, "get_graph_store", _gs)


async def test_feedback_writes_prior_and_kg(env, monkeypatch):
    store = _KGStore()
    _patch_graph(monkeypatch, store)
    run_id = _make_run()

    result = await agent_mod.record_feedback(
        "s1", run_id, metrics={"accuracy": 0.94}, cost=2.5, duration_hours=4.0, notes="复跑"
    )

    assert result["ok"] is True and result["record_id"] > 0
    assert result["candidate_id"] == "c1"
    assert (result["task"], result["method"], result["dataset"], result["metric"]) == \
        ("图像分类", "ResNet-50", "CIFAR-10", "accuracy")
    assert result["value"] == pytest.approx(0.94)
    assert result["kg"]["ok"] is True and result["kg"]["degraded"] is False
    assert result["kg"]["written"] == 7  # Experiment/Method/Metric/Dataset 4 实体 + 3 边

    exp_id = f"exp:s1:{run_id}:c1"
    entity_ids = {e["entity_id"] for e in store.entities}
    assert {exp_id, make_entity_id("ResNet-50"),
            make_entity_id("accuracy"), make_entity_id("CIFAR-10")} <= entity_ids
    edge_keys = {(e["type"], e["source_id"], e["target_id"]) for e in store.edges}
    assert ("ACHIEVES", exp_id, make_entity_id("ResNet-50")) in edge_keys
    assert ("ACHIEVES", exp_id, make_entity_id("accuracy")) in edge_keys
    assert ("USES_DATASET", exp_id, make_entity_id("CIFAR-10")) in edge_keys

    # 先验存储：闭环数据基座
    assert prior_store.count_experiments() == 1
    priors = prior_store.query_priors(task="图像分类")
    assert priors["rows"] == 1
    assert any(m["metric"] == "accuracy" for m in priors["metrics"])
    assert any(a["action"] == "design_feedback" for a in audit_store.recent_audit("s1"))


async def test_feedback_degrades_when_kg_down(env, monkeypatch):
    store = _KGStore(fail=True)
    _patch_graph(monkeypatch, store)
    run_id = _make_run()

    result = await agent_mod.record_feedback("s1", run_id, metrics={"accuracy": 0.9})

    assert result["ok"] is True and result["record_id"] > 0  # 先验存储必写
    assert result["kg"]["ok"] is False and result["kg"]["degraded"] is True
    assert "neo4j" in result["kg"]["error"]
    assert prior_store.count_experiments() == 1
    assert any(a["action"] == "kg_feedback_degraded"
               for a in audit_store.recent_audit("s1"))


async def test_feedback_skips_kg_when_build_disabled(env, monkeypatch):
    store = _KGStore()
    called = {"n": 0}

    async def _gs():
        called["n"] += 1
        return store

    monkeypatch.setattr(agent_mod, "get_graph_store", _gs)
    disabled = SimpleNamespace(build_enabled=False, entity_types=[], relation_types=[])
    monkeypatch.setattr(agent_mod, "get_settings", lambda: SimpleNamespace(kg=disabled))
    run_id = _make_run()

    result = await agent_mod.record_feedback("s1", run_id, metrics={"accuracy": 0.9})

    assert result["kg"]["degraded"] is True
    assert result["kg"]["error"] == "kg.build_enabled=false"
    assert called["n"] == 0 and store.entities == [] and store.edges == []
    assert result["record_id"] > 0  # KG 关闭不影响先验存储


async def test_feedback_missing_run_is_error(env):
    result = await agent_mod.record_feedback("s1", "20260101-000000-nope")
    assert result == {"ok": False, "error": "设计运行不存在"}
    assert prior_store.count_experiments() == 0


async def test_feedback_metric_fallback_and_candidate_override(env, monkeypatch):
    store = _KGStore()
    _patch_graph(monkeypatch, store)
    run_id = _make_run()

    result = await agent_mod.record_feedback("s1", run_id, candidate_id="c9", metrics={})

    assert result["metric"] == "accuracy" and result["value"] is None  # 回退推荐指标
    assert result["candidate_id"] == "c9"  # 显式指定优先于 recommended 元数据
    assert f"exp:s1:{run_id}:c9" in {e["entity_id"] for e in store.entities}
