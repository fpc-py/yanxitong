"""Tests for the experiment-prior store (designer closed loop ⑩)."""

from __future__ import annotations

import pytest

from src.knowledge import prior_store


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(prior_store, "DB_PATH", str(tmp_path / "experiments.db"))
    monkeypatch.setattr(prior_store, "_conn", None)
    yield prior_store
    if prior_store._conn is not None:
        prior_store._conn.close()
        prior_store._conn = None


def test_record_and_matrix_aggregation(store):
    row_id = store.record_experiment(
        session_id="s1", run_id="r1", candidate_id="c1", task="图像分类",
        method="ResNet-50", dataset="CIFAR-10", metric="accuracy", value=0.94,
        cost=1.5, duration_hours=3.0,
        config={"hyperparams": {"lr": 0.01, "batch_size": 64}}, source="feedback",
    )
    assert row_id and row_id > 0
    store.record_experiment(
        session_id="s1", run_id="r1", candidate_id="c2", task="图像分类",
        method="ResNet-50", dataset="CIFAR-10", metric="accuracy", value=0.96,
        config={"hyperparams": {"lr": 0.02, "batch_size": 128}},
    )
    store.record_experiment(
        session_id="s1", method="ViT-B/16", dataset="CIFAR-10",
        metric="accuracy", value=0.97,
        config={"hyperparams": {"lr": 0.001, "batch_size": 256}},
    )

    matrix = store.query_matrix(task="图像分类")
    acc = [m for m in matrix if m["metric"] == "accuracy"]
    resnet = next(m for m in acc if m["method"] == "ResNet-50")
    assert resnet["n"] == 2
    assert abs(resnet["mean"] - 0.95) < 1e-9
    assert abs(resnet["std"] - 0.01) < 1e-9
    assert resnet["min"] == 0.94 and resnet["max"] == 0.96

    # 不带 task 过滤时 ViT 也出现
    assert {m["method"] for m in store.query_matrix()} >= {"ResNet-50", "ViT-B/16"}


def test_query_priors_ranges_and_cost(store):
    store.record_experiment(
        method="ResNet-50", task="图像分类",
        config={"hyperparams": {"lr": 0.01, "batch_size": 64}},
        cost=2.0, duration_hours=4.0, value=0.9, metric="accuracy",
    )
    store.record_experiment(
        method="ResNet-50", task="图像分类",
        config={"hyperparams": {"lr": 0.03, "batch_size": 128}, "epochs": 100},
        cost=4.0, duration_hours=6.0, value=0.92, metric="accuracy",
    )
    priors = store.query_priors(method="ResNet-50")
    assert priors["rows"] == 2
    lr = priors["hyperparams"]["lr"]
    assert lr["min"] == 0.01 and lr["max"] == 0.03
    assert abs(lr["mean"] - 0.02) < 1e-9
    # 顶层数值键（epochs）也进入先验
    assert priors["hyperparams"]["epochs"]["max"] == 100
    assert priors["cost"]["mean"] == 3.0
    assert priors["duration"]["max"] == 6.0
    assert any(m["metric"] == "accuracy" for m in priors["metrics"])


def test_recent_and_count(store):
    store.record_experiment(session_id="s1", method="A", config={"hyperparams": {"lr": 0.1}})
    store.record_experiment(session_id="s2", method="B")
    assert store.count_experiments() == 2
    recent = store.recent_experiments(limit=5, session_id="s1")
    assert len(recent) == 1
    assert recent[0]["method"] == "A"
    assert recent[0]["config"]["hyperparams"]["lr"] == 0.1


def test_degrades_without_raising(tmp_path, monkeypatch):
    # 指向一个目录路径 → sqlite 无法打开；读写都必须优雅降级而不是抛异常
    monkeypatch.setattr(prior_store, "DB_PATH", str(tmp_path))
    monkeypatch.setattr(prior_store, "_conn", None)
    assert prior_store.record_experiment(method="X") is None
    assert prior_store.query_matrix() == []
    assert prior_store.query_priors(method="X")["rows"] == 0
    assert prior_store.count_experiments() == 0
