"""设计先验按领域包隔离单元测试：kb 列过滤 + 旧空值视同默认包。

同包跨会话沉淀（单领域深耕闭环）：A 包跑设计+反馈回填的数据点只在
A 包的 matrix/priors 中出现；``kb_id=""`` 查询保持旧行为（全量）。
"""

import pytest

from src.knowledge import prior_store

KB_A = "kb_aaaaaaaa"
KB_B = "kb_bbbbbbbb"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(prior_store, "DB_PATH", str(tmp_path / "experiments.db"))
    monkeypatch.setattr(prior_store, "_conn", None)
    yield prior_store
    if prior_store._conn is not None:
        prior_store._conn.close()
        prior_store._conn = None


def _record(store, kb_id, method, value):
    return store.record_experiment(
        session_id="s1", task="图像分类", method=method, dataset="CIFAR-10",
        metric="accuracy", value=value,
        config={"hyperparams": {"lr": 0.01}}, source="feedback", kb_id=kb_id,
    )


def test_matrix_kb_isolation(store):
    _record(store, KB_A, "A-Method", 0.90)
    _record(store, KB_B, "B-Method", 0.91)
    assert {m["method"] for m in store.query_matrix(task="图像分类", kb_id=KB_A)} == {"A-Method"}
    assert {m["method"] for m in store.query_matrix(task="图像分类", kb_id=KB_B)} == {"B-Method"}
    # kb_id="" 保持旧行为：全量
    assert {m["method"] for m in store.query_matrix(task="图像分类")} == {"A-Method", "B-Method"}


def test_default_kb_includes_legacy_empty_rows(store):
    # 直接落一行 kb_id=''（模拟迁移前旧库）——default 查询必须覆盖它
    with store._lock:
        conn = store._get_conn()
        conn.execute(
            "INSERT INTO experiments (session_id, task, method, metric, value, kb_id)"
            " VALUES ('s0', '图像分类', 'legacy', 'accuracy', 0.5, '')"
        )
        conn.commit()
    _record(store, KB_A, "packed", 0.9)
    names = {m["method"] for m in store.query_matrix(task="图像分类", kb_id="default")}
    assert names == {"legacy"}  # 空值视同默认包，不收实包行
    assert {m["method"] for m in store.query_matrix(task="图像分类", kb_id=KB_A)} == {"packed"}


def test_query_priors_kb_isolation(store):
    _record(store, KB_A, "A-Method", 0.90)
    _record(store, KB_B, "B-Method", 0.95)
    priors_a = store.query_priors(method="A-Method", kb_id=KB_A)
    assert priors_a["rows"] == 1  # A 包先验有数据点
    assert priors_a["hyperparams"]["lr"]["n"] == 1
    priors_b = store.query_priors(method="A-Method", kb_id=KB_B)
    assert priors_b["rows"] == 0  # B 包看不到 A 的数据点
    assert priors_b["hyperparams"] == {}
    assert store.query_priors(method="A-Method")["rows"] == 1  # 旧行为：不加包过滤


def test_same_pack_accumulates_across_sessions(store):
    _record(store, KB_A, "A-Method", 0.90)
    store.record_experiment(
        session_id="s2", task="图像分类", method="A-Method", dataset="CIFAR-10",
        metric="accuracy", value=0.92, config={"hyperparams": {"lr": 0.01}}, kb_id=KB_A,
    )
    matrix = store.query_matrix(task="图像分类", kb_id=KB_A)
    row = next(m for m in matrix if m["method"] == "A-Method")
    assert row["n"] == 2  # 同包跨会话沉淀
    assert {m["n"] for m in store.query_matrix(task="图像分类", kb_id=KB_B)} == set()
