"""规格④⑤⑥单元测试：候选生成（引用门控/启发式补齐）+ Optuna 多目标优化排序。

Optuna 为可选依赖：内置采样路径通过 monkeypatch ``optimization.optuna = None``
强制覆盖，保证双引擎行为都被验证。
"""

from types import SimpleNamespace

from src.agents.experiment_designer import candidates as cand_mod
from src.agents.experiment_designer import optimization as opt_mod

ANCHOR_KB = {
    "id": "kb:design:0", "kind": "kb", "ref": "实验设计库/design_priors.md",
    "library": "design", "label": "实验设计库", "section": "超参先验区间（学习率）",
    "similarity": 0.8, "text": "CNN 用 SGD 0.05–0.1，Transformer 用 AdamW 1e-5–1e-3",
}
ANCHOR_KG = {
    "id": "kg:chain:ax:1234.5678", "kind": "kg-chain", "ref": "ax:1234.5678",
    "title": "BetterNet", "year": 2023, "text": "方法: BetterNet；数据集: CIFAR-10(n=50000)",
}
EVIDENCE = {"anchors": [ANCHOR_KB, ANCHOR_KG], "kg": {}, "kb": {}, "literature": []}

DIAGNOSIS = {"bottlenecks": [{
    "type": "model_outdated", "severity": "high", "finding": "当前 ResNet-50 落后",
    "evidence_refs": [ANCHOR_KG["id"]], "recommendation": "升级骨干",
}], "summary": "结构落后"}


def _cand(cid: str, route: str, **over) -> dict:
    base = {
        "id": cid, "route": route, "title": f"{route} 方案", "description": "",
        "config_patch": {}, "hyperparams": {"learning_rate": 1e-3, "batch_size": 64, "epochs": 100},
        "evidence_refs": [ANCHOR_KB["id"]], "expected": "", "risk": "medium",
        "complexity": "medium", "interpretability": "medium", "severity": "medium",
        "source": "test",
    }
    base.update(over)
    return base


# ---- 候选生成 ---------------------------------------------------------------


async def test_llm_candidates_gated_by_evidence():
    async def _llm(_prompt):
        return """{"candidates": [
            {"route": "structure", "title": "换 BetterNet", "hyperparams": {"learning_rate": 0.001},
             "evidence_refs": ["kg:chain:ax:1234.5678"], "risk": "high", "complexity": "high"},
            {"route": "hyperparam", "title": "无引用方案", "evidence_refs": ["kg:ent:ghost"]},
            {"route": "banana", "title": "非法路由", "evidence_refs": ["kb:design:0"]}
        ]}"""

    result = await cand_mod.generate_candidates(
        "升级模型", {"model": {"family": "cnn"}}, DIAGNOSIS, EVIDENCE, None, _llm
    )
    assert [c["route"] for c in result] == ["structure"]
    assert result[0]["evidence_refs"] == ["kg:chain:ax:1234.5678"]
    assert result[0]["severity"] == "high"  # 来自 model_outdated 瓶颈
    assert result[0]["risk"] == "high"


async def test_llm_failure_falls_back_to_heuristics_for_all_routes():
    async def _boom(_prompt):
        raise RuntimeError("llm down")

    result = await cand_mod.generate_candidates(
        "设计实验", {"model": {"family": "cnn"}}, DIAGNOSIS, EVIDENCE, None, _boom
    )
    assert {c["route"] for c in result} == {"structure", "hyperparam", "data", "strategy"}
    assert all(c["source"] == "heuristic" for c in result)
    known = {a["id"] for a in EVIDENCE["anchors"]}
    assert all(set(c["evidence_refs"]) <= known and c["evidence_refs"] for c in result)
    hp = next(c for c in result if c["route"] == "hyperparam")["hyperparams"]
    assert hp["learning_rate"] == opt_mod.FAMILY_DEFAULTS["cnn"]["learning_rate"]


async def test_heuristic_uses_prior_means_when_available():
    priors = {"rows": 3, "hyperparams": {"learning_rate": {"n": 3, "mean": 2e-4, "min": 1e-4, "max": 3e-4}}}

    async def _boom(_prompt):
        raise RuntimeError("llm down")

    result = await cand_mod.generate_candidates(
        "设计实验", {"model": {"family": "vit"}}, {"bottlenecks": []}, EVIDENCE, priors, _boom
    )
    hp = next(c for c in result if c["route"] == "hyperparam")["hyperparams"]
    assert hp["learning_rate"] == 2e-4


async def test_no_anchors_means_no_candidates():
    async def _boom(_prompt):
        raise RuntimeError("llm down")

    result = await cand_mod.generate_candidates(
        "设计实验", {}, {"bottlenecks": []}, {"anchors": []}, None, _boom
    )
    assert result == []


# ---- 搜索空间与剪枝 ----------------------------------------------------------


def test_build_space_prior_bounds_and_kb_fallback():
    priors = {"hyperparams": {
        "learning_rate": {"n": 3, "min": 1e-4, "max": 1e-2, "p10": 2e-4, "p90": 8e-3, "mean": 1e-3},
    }}
    candidates = [_cand("c1", "hyperparam")]
    space = opt_mod.build_space(candidates, {"model": {"family": "cnn"}}, priors, deep_verify=True)
    assert space["learning_rate"]["source"] == "prior"
    assert space["learning_rate"]["low"] == 2e-4 and space["learning_rate"]["high"] == 8e-3
    assert space["learning_rate"]["log"] is True
    assert space["batch_size"]["source"] == "kb"
    assert space["batch_size"]["kind"] == "int"

    loose = opt_mod.build_space(candidates, {"model": {"family": "cnn"}}, priors, deep_verify=False)
    assert loose["learning_rate"]["low"] == 1e-4 and loose["learning_rate"]["high"] == 1e-2


def test_prior_pruning_removes_out_of_range_candidate():
    priors = {"hyperparams": {"learning_rate": {"n": 3, "min": 1e-3, "max": 1e-2}}}
    inside = _cand("c1", "hyperparam", hyperparams={"learning_rate": 5e-3})
    outside = _cand("c2", "hyperparam", hyperparams={"learning_rate": 0.5})
    active, pruned = opt_mod._prune_candidates([inside, outside], priors)
    assert [c["id"] for c in active] == ["c1"]
    assert pruned[0]["id"] == "c2" and "超出先验区间" in pruned[0]["reason"]


def test_prior_pruning_skipped_when_evidence_thin():
    """n=1/2 的先验只作参考不作否决：单条历史 min==max 区间会误杀全部候选。

    回归缺陷：闭环反馈写入一条实验后，重跑设计全部候选被先验剪枝、无胜者。
    """
    priors = {"hyperparams": {"learning_rate": {"n": 1, "min": 5e-2, "max": 5e-2},
                              "epochs": {"n": 2, "min": 100.0, "max": 100.0}}}
    cand = _cand("c1", "hyperparam", hyperparams={"learning_rate": 1e-3, "epochs": 90})
    active, pruned = opt_mod._prune_candidates([cand], priors)
    assert [c["id"] for c in active] == ["c1"] and pruned == []


def test_prior_pruning_allows_margin_band():
    """证据充分时区间外仍留一档探索余量（对数维度 10×，线性维度 ±50%）。"""
    priors = {"hyperparams": {"learning_rate": {"n": 4, "min": 1e-3, "max": 1e-2},
                              "batch_size": {"n": 4, "min": 64.0, "max": 128.0}}}
    near = _cand("c1", "hyperparam", hyperparams={"learning_rate": 0.05, "batch_size": 40})
    far = _cand("c2", "hyperparam", hyperparams={"learning_rate": 1.0})
    active, pruned = opt_mod._prune_candidates([near, far], priors)
    assert [c["id"] for c in active] == ["c1"]
    assert pruned[0]["id"] == "c2"


# ---- 多目标优化 --------------------------------------------------------------


def _designer_stub(**over):
    base = dict(max_gpu_hours=24.0, max_memory_gb=16.0, n_trials=24, top_k=3,
                deep_verify=True, weights=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_optimize_runs_pareto_and_weighted_ranking():
    candidates = [
        _cand("c1", "structure", severity="high", risk="high", complexity="high"),
        _cand("c2", "hyperparam", severity="medium", risk="low", complexity="low",
              interpretability="high"),
        _cand("c3", "strategy", severity="low", risk="medium", complexity="medium"),
    ]
    result = opt_mod.optimize(candidates, {"model": {"family": "cnn", "params_m": 25}}, None, _designer_stub())

    assert result["ok"] is True
    assert result["method"] == "optuna"
    assert result["fallback"] is False
    assert result["n_evaluated"] > 0
    assert result["note"].startswith("性能/成本/时间")
    # 排序全局按分数降序、rank 连续
    scores = [r["score"] for r in result["ranking"]]
    assert scores == sorted(scores, reverse=True)
    assert [r["rank"] for r in result["ranking"]] == list(range(1, len(result["ranking"]) + 1))
    assert len(result["top_k"]) == min(3, len(result["ranking"]))
    # 每候选只出现一次（取最佳采样点）
    ids = [r["candidate_id"] for r in result["ranking"]]
    assert len(ids) == len(set(ids))
    # Pareto 前沿非支配性
    for i, p in enumerate(result["pareto"]):
        for j, q in enumerate(result["pareto"]):
            if i != j:
                assert not opt_mod._dominates(q, p)
    # 估计点必须带不确定度
    for r in result["ranking"]:
        assert set(r["uncertainty"]) == {"performance", "cost", "time"}


def test_optimize_builtin_fallback_is_deterministic(monkeypatch):
    monkeypatch.setattr(opt_mod, "optuna", None)
    candidates = [_cand("c1", "hyperparam"), _cand("c2", "strategy")]
    first = opt_mod.optimize(candidates, {"model": {"family": "cnn"}}, None, _designer_stub(n_trials=16))
    second = opt_mod.optimize(candidates, {"model": {"family": "cnn"}}, None, _designer_stub(n_trials=16))
    assert first["method"] == "builtin" and first["fallback"] is True and first["ok"] is True
    assert [(r["candidate_id"], r["score"]) for r in first["ranking"]] == \
           [(r["candidate_id"], r["score"]) for r in second["ranking"]]


def test_optimize_resource_pruning_yields_no_points():
    candidates = [_cand("c1", "structure")]
    result = opt_mod.optimize(candidates, {"model": {"family": "cnn"}}, None,
                              _designer_stub(max_gpu_hours=0.01, max_memory_gb=0.1))
    assert result["ok"] is False
    assert result["pruned"]["resource"] > 0
    assert "资源剪枝" in result["error"]


def test_optimize_all_candidates_prior_pruned():
    priors = {"hyperparams": {"learning_rate": {"n": 3, "min": 1e-4, "max": 1e-3}}}
    candidates = [_cand("c1", "hyperparam", hyperparams={"learning_rate": 0.9})]
    result = opt_mod.optimize(candidates, {}, priors, _designer_stub())
    assert result["ok"] is False
    assert result["pruned"]["prior"] == 1
    assert "先验剪枝" in result["error"]
