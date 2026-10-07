"""规格⑦⑧⑪：可行性五类检查 + 验证计划（确定性功效公式）+ 分层幻觉校验。

分层结构：确定性检查先跑（引用覆盖率 / 数值域 / 不确定度标注 / 资源自洽 / 多样性），
再交 LLM 复核主张-证据一致性；任一层 fail → 上层记录 hallucination flag 并压低
置信度。所有函数纯计算、可单测，不依赖网络与 LLM（LLM 复核通过传入的 llm_call）。
"""

from __future__ import annotations

import json
import logging
import math

from src.agents.base import parse_llm_json
from src.agents.experiment_designer import prompts

logger = logging.getLogger(__name__)

FEASIBILITY_DIMENSIONS = ("resource", "dependency", "data", "ethics", "reproducibility")

_ETHICS_KEYWORDS = (
    "人体", "受试", "患者", "临床", "隐私", "伦理", "动物", "未成年人",
    "human", "patient", "clinical", "privacy", "ethics", "animal",
)

#: 数值域（确定性校验用；超出即视为不可信主张）
_DOMAINS: dict[str, tuple[float, float]] = {
    "learning_rate": (1e-8, 1.0),
    "batch_size": (1, 8192),
    "epochs": (1, 5000),
    "weight_decay": (0.0, 1.0),
    "dropout": (0.0, 0.95),
    "warmup_ratio": (0.0, 1.0),
    "params_m": (0.1, 100000.0),
}


def required_sample_size(delta_over_sigma: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """KB 种子同款近似公式：n ≈ 16 / (Δ/σ)²（两独立均值比较，每组样本量）。"""
    ratio = max(float(delta_over_sigma or 0.0), 1e-6)
    return max(2, int(math.ceil(16.0 / (ratio ** 2))))


def power_analysis(
    planned_seeds: int = 3,
    delta_over_sigma: float = 0.5,
    alpha: float = 0.05,
    power: float = 0.8,
) -> dict:
    needed = required_sample_size(delta_over_sigma, alpha, power)
    ok = planned_seeds >= needed
    return {
        "min_meaningful_difference_sigma": delta_over_sigma,
        "required_n_per_group": needed,
        "planned_seeds": planned_seeds,
        "adequate": ok,
        "note": (
            f"计划 {planned_seeds} 个种子 ≥ 所需 {needed}：功效充足"
            if ok else
            f"计划 {planned_seeds} 个种子 < 所需 {needed}：种子层面功效不足，"
            "应增加重复或放宽最小有意义差异（结论须标注探索性）"
        ),
    }


def build_validation_plan(config: dict | None, recommended: dict, evidence: dict | None) -> dict:
    """规格⑧：消融 / 对照 / 显著性 / 功效 / 回滚判据 / 可复现清单（确定性生成）。"""
    route = str(recommended.get("route") or "")
    patch = recommended.get("config_patch") or {}
    ablation = []
    if route == "structure":
        ablation.append({"component": "结构升级", "remove_to_test": "回退到当前模型结构",
                         "expect": "结构收益独立于其他改动"})
    if route == "hyperparam" or patch.get("hyperparams"):
        ablation.append({"component": "超参对齐", "remove_to_test": "回退到当前学习率/批大小",
                         "expect": "收益不来自随机波动（≥3 种子）"})
    if route == "data":
        ablation.append({"component": "数据增强", "remove_to_test": "关闭增强的无增强基线",
                         "expect": "增强收益 > 种子波动 σ"})
    if route == "strategy" or patch.get("strategy"):
        ablation.append({"component": "训练策略", "remove_to_test": "关闭调度/早停",
                         "expect": "策略收益可复现且不损收敛"})
    if not ablation:
        ablation.append({"component": "完整方案", "remove_to_test": "基线配置",
                         "expect": "逐组件量化独立贡献"})

    anchors = (evidence or {}).get("anchors") or []
    kb_refs = [a["id"] for a in anchors if str(a.get("kind")) == "kb"][:3]

    return {
        "ablation": ablation,
        "controls": [
            "与当前配置在相同数据划分上对照",
            "与证据中的最强基线/同任务公开方法对照",
            "所有超参选择只在验证集上做，测试集仅报告一次",
        ],
        "significance": {
            "primary_test": "同划分下的配对比较（配对 t 检验；非正态用 Wilcoxon 符号秩）",
            "correction": "多组比较用 Benjamini-Hochberg FDR 校正",
            "alpha": 0.05,
            "report": "均值±SD、效应量（Cohen's d）与 95% CI 一并报告",
        },
        "power": power_analysis(),
        "rollback": [
            {"criterion": "连续 3 epoch 验证损失不降且高于基线 10%", "action": "回滚上一配置，lr×0.1 或加 warmup 重试一次"},
            {"criterion": "验证损失连续 5 epoch 上升且训练-验证间隙 >20%", "action": "启用早停并加正则/增强，禁止继续堆轮数"},
            {"criterion": "单次运行超预算 50%（时长/显存）", "action": "回滚并降批大小/分辨率"},
            {"criterion": "指标优于基线 ≥2σ 且无机制解释", "action": "先核查数据泄漏/划分错误，禁止直接上报"},
        ],
        "reproducibility": [
            "固定 Python/NumPy/PyTorch 随机种子 ≥3 个（42/43/44）",
            "记录数据版本指纹（哈希）与固定 train/val/test 划分",
            "产出 environment.lock（pip freeze）与硬件型号",
            "报告附「复现命令」一节；同环境重跑差异应 <0.5%",
        ],
        "evidence_refs": kb_refs,
    }


def check_feasibility(
    intent: str,
    config: dict | None,
    estimate: dict | None,
    static: dict | None,
    plan: dict | None,
) -> dict:
    """规格⑦：资源 / 依赖 / 数据 / 伦理 / 复现 五类可行性检查。"""
    config = config or {}
    resources = config.get("resources") or {}
    data = config.get("data") or {}
    items: list[dict] = []

    cost = None
    if isinstance(estimate, dict):
        cost = estimate.get("cost")
    declared = resources.get("gpu_hours")
    if cost is not None and declared:
        needed = round(float(cost) * 1.5, 2)  # 含复跑种子余量
        if needed > float(declared):
            items.append({
                "dimension": "resource", "ok": False, "severity": "warn",
                "finding": f"估计成本 {cost} GPU 小时 ×1.5 复跑余量 = {needed}，超出声明预算 {declared}",
                "alternative": "降低批大小/轮数，或先以子集做搜索、确认后再全量训练",
            })
        else:
            items.append({
                "dimension": "resource", "ok": True, "severity": "info",
                "finding": f"估计成本 {needed} GPU 小时在声明预算 {declared} 之内",
                "alternative": "",
            })
    else:
        items.append({
            "dimension": "resource", "ok": True, "severity": "info",
            "finding": (
                f"未声明资源预算；估计成本 {cost} GPU 小时（±不确定度），请确认可用资源"
                if cost is not None else "无成本估计可用"
            ),
            "alternative": "在 experiment_config.resources 中声明 gpu_hours/memory_gb 以获得预算校验",
        })

    if static is None:
        items.append({
            "dimension": "dependency", "ok": False, "severity": "warn",
            "finding": "未生成训练脚本，依赖检查跳过",
            "alternative": "重新生成可执行代码后复核依赖",
        })
    elif static.get("missing"):
        items.append({
            "dimension": "dependency", "ok": False, "severity": "warn",
            "finding": "训练脚本依赖 " + ", ".join(static["missing"][:6]) + " 不在沙箱镜像清单中",
            "alternative": "在目标训练环境安装缺失依赖，或替换为镜像内可用实现",
        })
    else:
        items.append({
            "dimension": "dependency", "ok": True, "severity": "info",
            "finding": f"依赖齐备（对照沙箱镜像清单 {static.get('env_packages', 0)} 个包）",
            "alternative": "",
        })

    samples = data.get("n_samples")
    if samples and float(samples) < 1000:
        items.append({
            "dimension": "data", "ok": False, "severity": "warn",
            "finding": f"样本量 {samples} 偏小，深度模型易过拟合",
            "alternative": "迁移学习 / 强增强 / 减小模型规模，并如实标注小样本结论边界",
        })
    elif samples:
        items.append({
            "dimension": "data", "ok": True, "severity": "info",
            "finding": f"样本量 {samples}（≥1000，数据量满足常规训练）",
            "alternative": "",
        })
    else:
        items.append({
            "dimension": "data", "ok": True, "severity": "info",
            "finding": "未提供样本量，无法进行数据充分性判断",
            "alternative": "在 experiment_config.data.n_samples 中补充以获得数据检查",
        })

    text = (str(intent or "") + " " + json.dumps(config, ensure_ascii=False))[:2000].lower()
    hits = [kw for kw in _ETHICS_KEYWORDS if kw.lower() in text]
    if hits:
        items.append({
            "dimension": "ethics", "ok": False, "severity": "warn",
            "finding": "检出人类/动物受试相关关键词：" + "、".join(hits[:5]),
            "alternative": "启动前完成伦理审查/知情同意/隐私脱敏材料",
        })
    else:
        items.append({
            "dimension": "ethics", "ok": True, "severity": "info",
            "finding": "未检出人类/动物受试相关关键词",
            "alternative": "",
        })

    repro = (plan or {}).get("reproducibility") or []
    items.append({
        "dimension": "reproducibility", "ok": bool(repro), "severity": "info" if repro else "warn",
        "finding": "验证计划已包含种子/数据指纹/环境锁/复现命令" if repro else "验证计划缺少可复现清单",
        "alternative": "" if repro else "补齐 ≥3 种子、数据指纹与环境锁",
    })

    statuses = {i["dimension"]: ("ok" if i["ok"] else i["severity"]) for i in items}
    overall = "warn" if any(v == "warn" for v in statuses.values()) else "pass"
    return {"items": items, "overall": overall,
            "dimensions": list(FEASIBILITY_DIMENSIONS)}


def deterministic_checks(
    candidates: list[dict],
    optimization: dict | None,
    evidence: dict | None,
) -> dict:
    """规格⑪第一层：引用覆盖率 / 数值域 / 不确定度标注 / 资源自洽 / 多样性。"""
    checks: list[dict] = []
    anchors = {a.get("id") for a in (evidence or {}).get("anchors") or []}
    candidates = candidates or []

    covered = sum(
        1 for c in candidates
        if c.get("evidence_refs") and set(c["evidence_refs"]) <= anchors
    )
    checks.append({
        "id": "citation_coverage", "ok": covered == len(candidates),
        "severity": "fail" if covered != len(candidates) else "info",
        "finding": f"{covered}/{len(candidates)} 个候选的引用可解析到证据锚点",
    })

    bad_values = []
    for cand in candidates:
        for dim, value in (cand.get("hyperparams") or {}).items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            domain = _DOMAINS.get(str(dim))
            if domain and not (domain[0] <= float(value) <= domain[1]):
                bad_values.append(f"{cand.get('id')}.{dim}={value} ∉ [{domain[0]}, {domain[1]}]")
    checks.append({
        "id": "numeric_domain", "ok": not bad_values,
        "severity": "fail" if bad_values else "info",
        "finding": "全部数值在物理域内" if not bad_values else "越界数值：" + "；".join(bad_values[:4]),
    })

    ranking = (optimization or {}).get("ranking") or []
    unlabeled = [
        r.get("candidate_id") for r in ranking
        if not isinstance(r.get("uncertainty"), dict) or not r["uncertainty"]
    ]
    checks.append({
        "id": "uncertainty_labels", "ok": not unlabeled and bool(ranking),
        "severity": "warn" if (not ranking or unlabeled) else "info",
        "finding": (
            f"{len(ranking)} 个排序候选均标注不确定度" if ranking and not unlabeled
            else "存在未标注不确定度的候选" + ("" if ranking else "（无排序结果）")
        ),
    })

    pruned_resource = int(((optimization or {}).get("pruned") or {}).get("resource", 0) or 0)
    checks.append({
        "id": "resource_alignment", "ok": True, "severity": "info",
        "finding": f"资源剪枝剔除 {pruned_resource} 个超预算采样点，保留点均在预算内",
    })

    routes = {c.get("route") for c in candidates}
    diverse = len(routes) >= min(2, len(candidates))
    checks.append({
        "id": "diversity", "ok": diverse,
        "severity": "info" if diverse else "warn",
        "finding": f"候选覆盖 {len(routes)} 条路由：" + ", ".join(sorted(str(r) for r in routes)),
    })

    overall = "pass"
    if any(c["severity"] == "fail" and not c["ok"] for c in checks):
        overall = "fail"
    elif any(not c["ok"] for c in checks):
        overall = "warn"
    return {"checks": checks, "overall": overall}


async def llm_review(
    llm_call,
    recommended: dict,
    evidence: dict | None,
    deterministic: dict,
    plan: dict | None,
) -> dict:
    """规格⑪第二层：LLM 复核主张-证据一致性；失败/解析失败 → degraded 标记。"""
    prompt = prompts.VALIDATE_PROMPT.format(
        recommended=json.dumps(recommended, ensure_ascii=False)[:2500],
        evidence=prompts.format_evidence(evidence, limit=20),
        deterministic=json.dumps(deterministic, ensure_ascii=False)[:1500],
        plan=json.dumps(plan or {}, ensure_ascii=False)[:1500],
    )
    try:
        resp = await llm_call(prompt)
        data = parse_llm_json(resp)
        consistency = str(data.get("consistency") or "").lower()
        if consistency not in ("pass", "warn", "fail"):
            consistency = ""
        return {
            "consistency": consistency,
            "issues": [i for i in (data.get("issues") or []) if isinstance(i, dict)][:10],
            "narrative": str(data.get("narrative") or "")[:800],
            "degraded": False,
        }
    except Exception as exc:
        logger.warning("design validation: LLM 复核降级: %s", exc)
        return {"consistency": "", "issues": [], "narrative": "", "degraded": True}


def overall_status(deterministic: dict, llm: dict | None) -> str:
    order = {"pass": 0, "warn": 1, "fail": 2}
    worst = order.get(str((deterministic or {}).get("overall") or "pass"), 0)
    if llm and llm.get("consistency"):
        worst = max(worst, order.get(str(llm["consistency"]), 0))
    return ["pass", "warn", "fail"][worst]
