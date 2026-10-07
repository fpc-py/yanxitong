"""规格⑦ 结果验证：确定性数据事实校验 + 两层结论合并。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.agents.data_analyst import validation as v


def _statuses(result, prefix=""):
    return {c["check"]: c["status"] for c in result["checks"] if c["check"].startswith(prefix)}


PROFILE = {
    "format": "csv/utf-8", "rows": 90, "cols": 2,
    "columns": [
        {"name": "身高", "dtype": "float64", "min": 150.0, "max": 200.0, "mean": 172.0},
        {"name": "体重", "dtype": "float64", "min": 40.0, "max": 100.0, "mean": 65.0},
    ],
}


def _clean_findings():
    return {
        "summary": "两组身高差异显著（p=0.003）",
        "tests": [{"name": "Welch t 检验", "statistic": 3.21, "p_value": 0.003, "groups": ["A", "B"]}],
        "statistics": {"身高": {"mean": 172.0, "std": 8.0, "min": 150.0, "max": 200.0, "median": 171.0, "n": 90}},
        "percentages": {"A 组": 45.0, "B 组": 55.0},
        "charts": ["身高分布.png"],
        "notes": [],
    }


# ---- 基线 -----------------------------------------------------------------


def test_clean_findings_pass():
    result = v.deterministic_checks(_clean_findings(), PROFILE)
    assert result["status"] == "ok"
    assert result["issues"] == 0


def test_missing_findings_warn():
    result = v.deterministic_checks({}, PROFILE)
    assert result["status"] == "warn"
    assert result["checks"][0]["check"] == "结构化发现"


def test_empty_summary_warns():
    findings = _clean_findings()
    findings["summary"] = "  "
    result = v.deterministic_checks(findings, PROFILE)
    assert _statuses(result, "结果摘要")["结果摘要"] == "warn"


# ---- fail 捕获 ---------------------------------------------------------------


def test_p_value_out_of_range_fails():
    findings = _clean_findings()
    findings["tests"][0]["p_value"] = 1.5
    result = v.deterministic_checks(findings, PROFILE)
    assert result["status"] == "fail"
    assert _statuses(result, "p 值")["p 值（Welch t 检验）"] == "fail"
    assert "1.5" in next(c["detail"] for c in result["checks"] if c["check"].startswith("p 值"))


def test_p_value_unparsable_warns():
    findings = _clean_findings()
    findings["tests"][0]["p_value"] = "not-a-number"
    result = v.deterministic_checks(findings, PROFILE)
    assert _statuses(result, "p 值")["p 值（Welch t 检验）"] == "warn"


def test_percentage_out_of_range_fails():
    findings = _clean_findings()
    findings["percentages"] = {"A 组": 120.0, "B 组": -20.0}
    result = v.deterministic_checks(findings, PROFILE)
    assert result["status"] == "fail"
    assert _statuses(result, "占比")["占比（A 组）"] == "fail"


def test_percentage_total_mismatch_fails():
    findings = _clean_findings()
    findings["percentages"] = {"A 组": 60.0, "B 组": 50.0}
    result = v.deterministic_checks(findings, PROFILE)
    assert result["status"] == "fail"
    assert any(c["check"] == "占比合计" for c in result["checks"])


def test_mean_outside_profile_bounds_fails():
    findings = _clean_findings()
    findings["statistics"]["身高"]["mean"] = 260.0
    findings["statistics"]["身高"]["max"] = 300.0  # 自身区间也放大，专测画像对照
    result = v.deterministic_checks(findings, PROFILE)
    assert result["status"] == "fail"
    detail = next(c["detail"] for c in result["checks"] if c["check"] == "均值（身高）")
    assert "画像" in detail and "260" in detail


def test_mean_outside_own_range_fails():
    findings = _clean_findings()
    findings["statistics"]["体重"] = {"mean": 500.0, "std": 5.0, "min": 40.0, "max": 100.0, "median": 60.0, "n": 90}
    result = v.deterministic_checks(findings, PROFILE)
    assert result["status"] == "fail"
    assert _statuses(result, "均值（体重）")["均值（体重）"] == "fail"


def test_sample_size_zero_fails():
    findings = _clean_findings()
    findings["statistics"]["身高"]["n"] = 0
    result = v.deterministic_checks(findings, PROFILE)
    assert _statuses(result, "样本量")["样本量（身高）"] == "fail"


def test_negative_std_fails():
    findings = _clean_findings()
    findings["statistics"]["身高"]["std"] = -1.0
    result = v.deterministic_checks(findings, PROFILE)
    assert _statuses(result, "标准差")["标准差（身高）"] == "fail"


def test_inverted_min_max_fails():
    findings = _clean_findings()
    findings["statistics"]["身高"]["min"] = 220.0
    findings["statistics"]["身高"]["max"] = 150.0
    result = v.deterministic_checks(findings, PROFILE)
    assert _statuses(result, "取值上下界")["取值上下界（身高）"] == "fail"


def test_non_finite_statistic_fails():
    findings = _clean_findings()
    findings["tests"][0]["statistic"] = float("inf")
    result = v.deterministic_checks(findings, PROFILE)
    assert _statuses(result, "统计量")["统计量（Welch t 检验）"] == "fail"


def test_missing_charts_warns():
    findings = _clean_findings()
    findings["charts"] = []
    result = v.deterministic_checks(findings, PROFILE)
    assert result["status"] == "warn"


def test_no_profile_skips_real_bounds_check():
    findings = _clean_findings()
    result = v.deterministic_checks(findings, None)
    assert result["status"] == "ok"


# ---- combine ---------------------------------------------------------------


def test_combine_pass_without_llm():
    report = v.combine(v.deterministic_checks(_clean_findings(), PROFILE), None)
    assert report["overall"] == "pass"
    assert report["llm"] is None
    assert report["revision_suggestions"] == []


def test_combine_fail_dominates():
    findings = _clean_findings()
    findings["tests"][0]["p_value"] = 1.5
    det = v.deterministic_checks(findings, PROFILE)
    report = v.combine(det, {"overall": "pass", "comments": []})
    assert report["overall"] == "fail"
    assert any("p 值" in s for s in report["revision_suggestions"])


def test_combine_warn_from_llm():
    det = v.deterministic_checks(_clean_findings(), PROFILE)
    report = v.combine(det, {"overall": "warn", "comments": ["样本量偏小，建议扩大样本"], "assumptions": [], "narrative": []})
    assert report["overall"] == "warn"
    assert "样本量偏小，建议扩大样本" in report["revision_suggestions"]


def test_combine_suggestions_from_deterministic_checks():
    det = v.deterministic_checks({}, PROFILE)  # warn：结构化发现缺失带建议
    report = v.combine(det, None)
    assert report["overall"] == "warn"
    assert any("检查代码生成输出契约" in s for s in report["revision_suggestions"])


def test_combine_llm_fail_dominates_det_ok():
    det = v.deterministic_checks(_clean_findings(), PROFILE)
    report = v.combine(det, {"overall": "fail", "comments": ["结论超出数据支持范围"]})
    assert report["overall"] == "fail"
