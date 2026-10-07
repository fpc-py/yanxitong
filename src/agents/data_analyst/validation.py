"""规格⑦ 结果验证：确定性数据事实校验（不依赖 LLM，始终先跑）。

校验对象是脚本按契约 ``emit_findings(...)`` 输出的结构化发现，以及数据画像
提供的真实列取值范围。任何数字与画像/自身上下界矛盾 → fail；无法核对或
契约缺失 → warn。LLM 的假设适用性/叙述一致性复核由 agent 另行调用，
本模块的 :func:`combine` 负责两层结论合并与修复建议汇总。
"""

from __future__ import annotations

import math

PCT_KEYS_HINT = ("%", "百分", "占比", "pct", "percent")
_RANGE_TOL = 1e-6


def _num(value) -> float | None:
    """把数值或数值字符串转成 float；不可解析返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%"))
        except ValueError:
            return None
    return None


def _profile_bounds(profile: dict | None) -> dict[str, tuple[float, float]]:
    bounds: dict[str, tuple[float, float]] = {}
    if not isinstance(profile, dict):
        return bounds
    for col in profile.get("columns") or []:
        if not isinstance(col, dict):
            continue
        low, high = _num(col.get("min")), _num(col.get("max"))
        if low is not None and high is not None:
            bounds[str(col.get("name"))] = (low, high)
    return bounds


def _within(value: float, low: float, high: float) -> bool:
    tol = max(abs(low), abs(high), 1.0) * _RANGE_TOL + 1e-9
    return low - tol <= value <= high + tol


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def deterministic_checks(findings: dict | None, profile: dict | None = None) -> dict:
    """确定性校验，返回 ``{checks, status, issues}``。"""
    checks: list[dict] = []

    def add(name: str, status: str, detail: str = "", suggestion: str = "") -> None:
        checks.append({"check": name, "status": status, "detail": detail, "suggestion": suggestion})

    if not isinstance(findings, dict) or not findings:
        add("结构化发现", "warn", "脚本未按契约输出 emit_findings(...) 结果", "检查代码生成输出契约")
        return {"checks": checks, "status": "warn", "issues": 1}

    if not (findings.get("summary") or "").strip():
        add("结果摘要", "warn", "summary 为空", "让脚本在 emit_findings 中给出含关键数字的结论")

    for i, test in enumerate(findings.get("tests") or []):
        if not isinstance(test, dict):
            continue
        label = test.get("name") or f"检验 #{i + 1}"
        p_value = _num(test.get("p_value"))
        if p_value is None:
            add(f"p 值（{label}）", "warn", "未给出 p 值（缺失或不可解析）", "补充 p 值或说明未做显著性检验")
        elif not _within(p_value, 0.0, 1.0):
            add(f"p 值（{label}）", "fail", f"p = {p_value} 超出 [0, 1]",
                "检查检验方法选择与统计量计算，修正报告中引用的 p 值")
        statistic = _num(test.get("statistic"))
        if test.get("statistic") is not None and statistic is not None and not _finite(statistic):
            add(f"统计量（{label}）", "fail", f"statistic = {test.get('statistic')} 非有限值",
                "检查数据是否包含 inf/nan 或分组为空")

    bounds = _profile_bounds(profile)
    for col, block in (findings.get("statistics") or {}).items():
        if not isinstance(block, dict):
            continue
        mean, median = _num(block.get("mean")), _num(block.get("median"))
        low, high = _num(block.get("min")), _num(block.get("max"))
        std, n = _num(block.get("std")), _num(block.get("n"))
        if n is not None and n < 1:
            add(f"样本量（{col}）", "fail", f"n = {n} < 1", "检查缺失值处理与分组逻辑")
        if std is not None and std < 0:
            add(f"标准差（{col}）", "fail", f"std = {std} < 0", "标准差计算有误")
        if low is not None and high is not None and low > high:
            add(f"取值上下界（{col}）", "fail", f"min = {low} > max = {high}", "检查极值统计")
        for stat_name, value in (("均值", mean), ("中位数", median)):
            if value is not None and low is not None and high is not None and not _within(value, low, high):
                add(f"{stat_name}（{col}）", "fail",
                    f"{stat_name} {value} 不在自身 [min, max] = [{low}, {high}] 内",
                    "核心统计量与极值矛盾，检查计算口径")
        real = bounds.get(str(col))
        if real and _finite(mean) and not _within(mean, real[0], real[1]):
            add(f"均值（{col}）", "fail",
                f"均值 {mean} 超出画像中该列真实范围 [{real[0]}, {real[1]}]",
                "均值超出原始数据范围，疑似计算错误或列错位")

    percentages = findings.get("percentages")
    if isinstance(percentages, dict) and percentages:
        values = []
        for key, raw in percentages.items():
            value = _num(raw)
            if value is None:
                add(f"占比（{key}）", "warn", f"占比 {raw!r} 不可解析", "占比应为数值")
                continue
            values.append(value)
            if not _within(value, 0.0, 100.0):
                add(f"占比（{key}）", "fail", f"占比 {value} 超出 [0, 100]", "检查百分比换算是否乘了 100")
        if len(values) >= 2:
            total = sum(values)
            if not _within(total, 99.0, 101.0):
                add("占比合计", "fail", f"各占比合计 {round(total, 2)} ≠ 100",
                    "检查分类是否互斥完备或换算口径")

    if not (findings.get("charts") or []):
        add("图表产出", "warn", "结构化发现未列出任何图表", "确认是否应至少输出一张图")

    failed = sum(1 for c in checks if c["status"] == "fail")
    warned = sum(1 for c in checks if c["status"] == "warn")
    status = "fail" if failed else ("warn" if warned else "ok")
    return {"checks": checks, "status": status, "issues": failed + warned}


def combine(deterministic: dict, llm: dict | None) -> dict:
    """合并确定性校验与 LLM 复核，产出最终验证报告。"""
    suggestions = [
        f"{c['check']}：{c['suggestion']}"
        for c in (deterministic or {}).get("checks", [])
        if c.get("status") != "ok" and c.get("suggestion")
    ]
    llm_overall = (llm or {}).get("overall")
    det_status = (deterministic or {}).get("status", "ok")
    if det_status == "fail" or llm_overall == "fail":
        overall = "fail"
    elif det_status == "warn" or llm_overall == "warn":
        overall = "warn"
    else:
        overall = "pass"  # 含 llm 未执行（deep_verify 关闭或 LLM 失败）仅确定性校验通过
    for comment in (llm or {}).get("comments") or []:
        if isinstance(comment, str) and comment.strip():
            suggestions.append(comment.strip())
    return {
        "deterministic": deterministic,
        "llm": llm,
        "overall": overall,
        "revision_suggestions": suggestions[:10],
    }
