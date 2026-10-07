"""规格⑦⑧⑨⑪单元测试：可行性五类 / 验证计划与功效公式 / 分层幻觉校验 / 代码静态校验。"""

import pytest

from src.agents.experiment_designer import codegen as codegen_mod
from src.agents.experiment_designer import validation as val_mod

EVIDENCE = {"anchors": [
    {"id": "kb:design:0", "kind": "kb", "ref": "实验设计库/design_priors.md",
     "section": "超参先验区间（学习率）", "text": "CNN 用 SGD 0.05–0.1", "similarity": 0.8},
    {"id": "kb:methods:1", "kind": "kb", "ref": "统计方法库/statistical_methods.md",
     "section": "效应量与置信区间", "text": "Cohen's d", "similarity": 0.7},
]}


def _cand(cid="c1", route="hyperparam", **over):
    base = {"id": cid, "route": route, "title": "方案", "evidence_refs": ["kb:design:0"],
            "hyperparams": {"learning_rate": 1e-3, "batch_size": 64}, "config_patch": {}}
    base.update(over)
    return base


# ---- 验证计划与功效 -----------------------------------------------------------


def test_required_sample_size_formula():
    assert val_mod.required_sample_size(0.5) == 64      # 16 / 0.25
    assert val_mod.required_sample_size(1.0) == 16
    assert val_mod.required_sample_size(0.1) == 1600
    assert val_mod.required_sample_size(0.0) >= 2       # 防除零


def test_power_analysis_flags_insufficient_seeds():
    weak = val_mod.power_analysis(planned_seeds=3, delta_over_sigma=0.5)
    assert weak["required_n_per_group"] == 64 and weak["adequate"] is False
    assert "功效不足" in weak["note"]
    strong = val_mod.power_analysis(planned_seeds=64, delta_over_sigma=0.5)
    assert strong["adequate"] is True


def test_validation_plan_covers_required_sections():
    plan = val_mod.build_validation_plan(
        {"hyperparams": {"learning_rate": 1e-3}},
        {"route": "hyperparam", "config_patch": {"hyperparams.learning_rate": 5e-4}},
        EVIDENCE,
    )
    assert plan["ablation"] and plan["controls"] and plan["rollback"] and plan["reproducibility"]
    assert plan["significance"]["alpha"] == 0.05
    assert plan["power"]["planned_seeds"] == 3
    assert plan["evidence_refs"] == ["kb:design:0", "kb:methods:1"]
    assert any("结构" not in a["component"] for a in plan["ablation"])


# ---- 可行性五类 --------------------------------------------------------------


def test_feasibility_warns_on_resource_and_data_and_ethics():
    config = {
        "resources": {"gpu_hours": 5.0},
        "data": {"n_samples": 500},
    }
    result = val_mod.check_feasibility(
        "针对患者队列的实验", config,
        estimate={"cost": 10.0}, static={"missing": [], "env_packages": 30}, plan={"reproducibility": ["种子"]},
    )
    dims = {i["dimension"]: i for i in result["items"]}
    assert dims["resource"]["ok"] is False           # 10×1.5=15 > 5
    assert dims["data"]["ok"] is False               # 500 < 1000
    assert dims["ethics"]["ok"] is False             # 检出"患者"
    assert dims["dependency"]["ok"] is True
    assert dims["reproducibility"]["ok"] is True
    assert result["overall"] == "warn"
    assert set(result["dimensions"]) == set(val_mod.FEASIBILITY_DIMENSIONS)


def test_feasibility_passes_clean_case():
    config = {"resources": {"gpu_hours": 100.0}, "data": {"n_samples": 50000}}
    result = val_mod.check_feasibility(
        "图像分类实验", config,
        estimate={"cost": 10.0}, static={"missing": [], "env_packages": 30},
        plan={"reproducibility": ["种子", "指纹"]},
    )
    assert result["overall"] == "pass"
    assert all(i["ok"] for i in result["items"])


def test_feasibility_flags_missing_dependencies_and_code_absent():
    no_code = val_mod.check_feasibility("实验", {}, None, None, None)
    dep = next(i for i in no_code["items"] if i["dimension"] == "dependency")
    assert dep["ok"] is False and "跳过" in dep["finding"]

    missing = val_mod.check_feasibility(
        "实验", {}, None, {"missing": ["torch"], "env_packages": 30}, None
    )
    dep = next(i for i in missing["items"] if i["dimension"] == "dependency")
    assert dep["ok"] is False and "torch" in dep["finding"]


# ---- 确定性幻觉检查 -----------------------------------------------------------


def test_deterministic_checks_pass_on_clean_input():
    optimization = {"ranking": [{"candidate_id": "c1", "uncertainty": {"performance": 0.15}}],
                    "pruned": {"resource": 2}}
    result = val_mod.deterministic_checks([_cand()], optimization, EVIDENCE)
    assert result["overall"] == "pass"
    assert all(c["ok"] for c in result["checks"])


def test_deterministic_checks_fail_on_ghost_reference():
    bad = _cand(evidence_refs=["kg:ent:ghost"])
    result = val_mod.deterministic_checks([bad], {"ranking": []}, EVIDENCE)
    coverage = next(c for c in result["checks"] if c["id"] == "citation_coverage")
    assert coverage["ok"] is False and coverage["severity"] == "fail"
    assert result["overall"] == "fail"


def test_deterministic_checks_fail_on_out_of_domain_value():
    bad = _cand(hyperparams={"learning_rate": 5.0, "batch_size": 64})
    result = val_mod.deterministic_checks([bad], {"ranking": []}, EVIDENCE)
    domain = next(c for c in result["checks"] if c["id"] == "numeric_domain")
    assert domain["ok"] is False and "learning_rate" in domain["finding"]
    assert result["overall"] == "fail"


# ---- LLM 复核与合成 -----------------------------------------------------------


async def test_llm_review_parses_and_degrades():
    async def _ok(_prompt):
        return '{"consistency": "fail", "issues": [{"claim": "c", "problem": "p", "severity": "high"}], "narrative": "n"}'

    reviewed = await val_mod.llm_review(_ok, {"x": 1}, EVIDENCE, {"overall": "pass"}, {})
    assert reviewed["consistency"] == "fail" and reviewed["issues"] and not reviewed["degraded"]

    async def _boom(_prompt):
        raise RuntimeError("llm down")

    degraded = await val_mod.llm_review(_boom, {}, None, {}, None)
    assert degraded["degraded"] is True and degraded["consistency"] == ""


def test_overall_status_takes_worst():
    assert val_mod.overall_status({"overall": "pass"}, {"consistency": "fail"}) == "fail"
    assert val_mod.overall_status({"overall": "warn"}, {"consistency": "pass"}) == "warn"
    assert val_mod.overall_status({"overall": "pass"}, {"consistency": "", "degraded": True}) == "pass"


# ---- 代码生成与静态校验 --------------------------------------------------------


def test_build_recommended_config_merges_patch_and_params():
    config = {"model": {"name": "ResNet50", "family": "cnn"}, "hyperparams": {"epochs": 100}}
    winner = {
        "candidate_id": "c2", "title": "超参对齐", "route": "hyperparam",
        "config_patch": {"model.name": "BetterNet", "strategy.seed": 42},
        "params": {"learning_rate": 0.0005, "batch_size": 64},
        "evidence_refs": ["kb:design:0"], "score": 0.71,
        "objectives": {"performance": 0.3}, "uncertainty": {"performance": 0.15},
    }
    merged = codegen_mod.build_recommended_config(config, winner)
    assert merged["model"]["name"] == "BetterNet"
    assert merged["strategy"]["seed"] == 42
    assert merged["hyperparams"]["learning_rate"] == 0.0005
    assert merged["hyperparams"]["epochs"] == 100            # 原键保留
    assert merged["_meta"]["candidate_id"] == "c2"
    assert "估计" in merged["_meta"]["estimates_note"]
    assert config["model"]["name"] == "ResNet50"             # 不改原对象


def test_env_packages_normalization():
    lock = "torch==2.2.0\nscikit-learn==1.5.2\n# comment\n-e git+https://x\nnumpy"
    packages = codegen_mod.env_packages(lock)
    assert {"torch", "scikit-learn", "numpy"} <= packages
    assert not any(p.startswith("git") for p in packages)


class _FakeSandbox:
    async def env_lock(self):
        return "torch==2.2.0\nnumpy==1.26.4\nscikit-learn==1.5.2\nmatplotlib==3.8.0"


async def test_static_check_syntax_and_missing_dependencies():
    code = (
        "import torch\nimport numpy as np\nfrom sklearn.linear_model import LinearRegression\n"
        "import carrottrain\nprint('ok')\n"
    )
    result = await codegen_mod.static_check(code, _FakeSandbox())
    assert result["ok"] is True and result["mode"] == "static"
    assert "torch" in result["imports"] and "sklearn" in result["imports"]
    assert result["missing"] == ["carrottrain"]              # sklearn 别名已归一化
    assert "静态校验" in result["note"]


async def test_static_check_reports_syntax_error():
    result = await codegen_mod.static_check("def broken(:\n    pass", _FakeSandbox())
    assert result["ok"] is False and result["syntax_ok"] is False
    assert result["syntax_error"] and result["imports"] == []


async def test_generate_code_strips_fences_and_degrades():
    async def _ok(_prompt):
        return "```python\nimport torch\nprint(1)\n```"

    code = await codegen_mod.generate_code("意图", {"a": 1}, EVIDENCE, _ok)
    assert code.startswith("import torch") and "```" not in code

    async def _boom(_prompt):
        raise RuntimeError("coder down")

    assert await codegen_mod.generate_code("意图", {}, None, _boom) == ""
