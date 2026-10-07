"""规格⑤⑥：Optuna 多目标优化（NSGA-II）与加权排序推荐。

搜索空间来自候选超参 ∩ 先验区间（``prior_store.query_priors``），无先验时回退
模型族经验带（KB 种子同款数值）。三个目标：性能（相对基线估计提升）、成本
（GPU 小时）、时间（墙钟小时）；先验剪枝 + 资源剪枝后产出 Pareto 前沿。

**诚实声明**：性能/成本/时间为代理模型估计值（携带 uncertainty），不是真实
训练结果；Optuna 不可用时自动回退内置随机采样（``method="builtin"``）。
"""

from __future__ import annotations

import logging
import math
import random

from src.agents.experiment_designer import prompts
from src.core.config import get_settings

try:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
except Exception:  # optuna 未安装/初始化失败 → 全程内置采样
    optuna = None

logger = logging.getLogger(__name__)

_SEED = 42

#: 模型族理想超参（无先验时的中心点；数值与 KB 种子"超参先验区间"一致）
FAMILY_DEFAULTS: dict[str, dict] = {
    "cnn": {"learning_rate": 0.05, "batch_size": 128, "epochs": 100,
            "weight_decay": 5e-4, "dropout": 0.3, "optimizer": "sgd", "scheduler": "cosine"},
    "vit": {"learning_rate": 3e-4, "batch_size": 64, "epochs": 100,
            "weight_decay": 0.05, "dropout": 0.1, "optimizer": "adamw", "scheduler": "cosine"},
    "transformer": {"learning_rate": 3e-4, "batch_size": 64, "epochs": 80,
                    "weight_decay": 0.01, "dropout": 0.1, "optimizer": "adamw", "scheduler": "linear"},
    "lstm": {"learning_rate": 1e-3, "batch_size": 64, "epochs": 100,
             "weight_decay": 1e-5, "dropout": 0.2, "optimizer": "adam", "scheduler": "step"},
    "gnn": {"learning_rate": 1e-3, "batch_size": 64, "epochs": 200,
            "weight_decay": 1e-4, "dropout": 0.2, "optimizer": "adam", "scheduler": "cosine"},
    "default": {"learning_rate": 1e-3, "batch_size": 64, "epochs": 100,
                "weight_decay": 1e-4, "dropout": 0.2, "optimizer": "adamw", "scheduler": "cosine"},
}

#: 各维度兜底边界（无先验且模型族未覆盖时）
_DIM_RANGES: dict[str, tuple[float, float]] = {
    "learning_rate": (1e-6, 1.0),
    "batch_size": (8, 512),
    "epochs": (10, 400),
    "weight_decay": (1e-6, 0.5),
    "dropout": (0.0, 0.6),
    "warmup_ratio": (0.0, 0.2),
    "num_augment_ops": (0, 4),
}

#: 模型族经验带（KB 种子"超参先验区间"）
FAMILY_BANDS: dict[str, dict[str, tuple[float, float]]] = {
    "cnn": {"learning_rate": (1e-4, 0.2), "batch_size": (32, 512), "epochs": (30, 300),
            "weight_decay": (1e-5, 1e-2), "dropout": (0.0, 0.5)},
    "vit": {"learning_rate": (1e-5, 1e-3), "batch_size": (16, 256), "epochs": (30, 300),
            "weight_decay": (1e-3, 0.1), "dropout": (0.0, 0.3)},
    "transformer": {"learning_rate": (1e-5, 1e-3), "batch_size": (16, 256), "epochs": (20, 200),
                    "weight_decay": (1e-3, 0.1), "dropout": (0.0, 0.3)},
    "lstm": {"learning_rate": (1e-4, 1e-2), "batch_size": (16, 256), "epochs": (50, 200),
             "weight_decay": (0.0, 1e-3), "dropout": (0.0, 0.5)},
    "default": {"learning_rate": (1e-5, 0.1), "batch_size": (16, 256), "epochs": (20, 300),
                "weight_decay": (1e-6, 0.1), "dropout": (0.0, 0.5)},
}

_LOG_DIMS = {"learning_rate", "weight_decay"}
_INT_DIMS = {"batch_size", "epochs", "num_augment_ops"}

_SEVERITY_BONUS = {"high": 0.35, "medium": 0.20, "low": 0.08}
_ROUTE_COST_MULT = {"structure": 1.25, "strategy": 1.10, "data": 1.05, "hyperparam": 0.90}

#: 先验剪枝的最低证据量：少于 3 条历史实验的区间无统计意义，不做否决
_PRIOR_PRUNE_MIN_N = 3
#: 区间外再放宽一档（对数维度按倍数、线性维度按相对比例），避免窄区间误杀
_PRIOR_PRUNE_LOG_FACTOR = 10.0
_PRIOR_PRUNE_LINEAR_RATIO = 0.5

_DEFAULT_WEIGHTS = {
    "performance": 0.40, "cost": 0.18, "time": 0.12,
    "interpretability": 0.10, "risk": 0.12, "complexity": 0.08,
}


def _family(config: dict | None) -> str:
    family = str(((config or {}).get("model") or {}).get("family") or "default").lower()
    return family if family in FAMILY_DEFAULTS else "default"


def build_space(
    candidates: list[dict],
    config: dict | None,
    priors: dict | None,
    deep_verify: bool = True,
) -> dict[str, dict]:
    """候选超参键 ∩ 已知维度 → 搜索空间；先验存在时以先验区间为界。"""
    family = _family(config)
    band = FAMILY_BANDS.get(family, FAMILY_BANDS["default"])
    numeric_keys: set[str] = set()
    for cand in candidates:
        for dim, value in (cand.get("hyperparams") or {}).items():
            if dim in _DIM_RANGES and _is_number(value):
                numeric_keys.add(dim)
    for dim, value in ((config or {}).get("hyperparams") or {}).items():
        if dim in _DIM_RANGES and _is_number(value):
            numeric_keys.add(dim)

    space: dict[str, dict] = {}
    for dim in sorted(numeric_keys):
        stat = ((priors or {}).get("hyperparams") or {}).get(dim)
        if stat and stat.get("n", 0) >= 2 and stat.get("max", 0) > stat.get("min", 0):
            if deep_verify and stat.get("n", 0) >= 3 and stat.get("p90", 0) > stat.get("p10", 0):
                low, high = stat["p10"], stat["p90"]
            else:
                low, high = stat["min"], stat["max"]
            source = "prior"
        else:
            low, high = band.get(dim) or _DIM_RANGES[dim]
            source = "kb"
        if dim in _INT_DIMS:
            low, high = int(math.floor(low)), int(math.ceil(high))
            if high <= low:
                high = low + 1
        else:
            low, high = float(low), float(high)
            if high <= low:
                high = low * 10 if low > 0 else low + 1.0
        space[dim] = {"low": low, "high": high, "kind": "int" if dim in _INT_DIMS else "float",
                      "log": dim in _LOG_DIMS, "source": source}
    return space


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _within_prior_band(dim: str, value: float, stat: dict) -> bool:
    """区间外再放宽一档：对数维度允许 min/10–max×10，线性维度允许 ±50% 边距。"""
    low, high = stat.get("min"), stat.get("max")
    if not _is_number(low) or not _is_number(high):
        return True
    if dim in _LOG_DIMS and low > 0 and high > 0:
        return low / _PRIOR_PRUNE_LOG_FACTOR <= value <= high * _PRIOR_PRUNE_LOG_FACTOR
    margin = max(high - low, _PRIOR_PRUNE_LINEAR_RATIO * max(abs(low), abs(high), 1.0))
    return low - margin <= value <= high + margin


def _prune_candidates(candidates: list[dict], priors: dict | None) -> tuple[list[dict], list[dict]]:
    """先验剪枝：候选自报超参明显超出本课题组先验区间 → 剔除并记录原因。

    仅在证据量 ≥ ``_PRIOR_PRUNE_MIN_N`` 时生效——单条历史实验的 min==max
    区间会否决一切偏离值，把闭环反馈变成自我封锁。
    """
    hyper_prior = (priors or {}).get("hyperparams") or {}
    active: list[dict] = []
    pruned: list[dict] = []
    for cand in candidates:
        reason = ""
        for dim, value in (cand.get("hyperparams") or {}).items():
            stat = hyper_prior.get(dim)
            if not stat or stat.get("n", 0) < _PRIOR_PRUNE_MIN_N or not _is_number(value):
                continue
            if not _within_prior_band(dim, value, stat):
                reason = f"{dim}={value} 超出先验区间 [{stat.get('min')}, {stat.get('max')}]"
                break
        if reason:
            pruned.append({"id": cand.get("id", ""), "title": cand.get("title", ""), "reason": reason})
        else:
            active.append(cand)
    return active, pruned


def _similarity(params: dict, targets: dict) -> float:
    """0..1：超参相对理想点的接近度（学习率/权重衰减按数量级距离）。"""
    scores: list[float] = []
    for dim, ideal in targets.items():
        value = params.get(dim)
        if value is None or not _is_number(value) or not ideal:
            continue
        if dim in _LOG_DIMS:
            distance = abs(math.log10(max(float(value), 1e-9)) - math.log10(max(float(ideal), 1e-9)))
            scores.append(max(0.0, 1.0 - distance / 1.5))
        else:
            low, high = _DIM_RANGES.get(dim, (0.0, 1.0))
            span = max(high - low, 1e-9)
            scores.append(max(0.0, 1.0 - abs(float(value) - float(ideal)) / span))
    return sum(scores) / len(scores) if scores else 0.5


def _estimate(candidate: dict, params: dict, priors: dict | None, config: dict | None) -> dict:
    """代理模型：全部为启发式估计，携带不确定度标注。"""
    family = _family(config)
    defaults = FAMILY_DEFAULTS.get(family, FAMILY_DEFAULTS["default"])
    hyper_prior = (priors or {}).get("hyperparams") or {}
    targets: dict = {}
    for dim in ("learning_rate", "batch_size", "epochs", "weight_decay", "dropout"):
        stat = hyper_prior.get(dim)
        targets[dim] = float(stat["mean"]) if stat and stat.get("n") and stat.get("mean") else defaults.get(dim)

    quality = _similarity(params, targets)
    severity_bonus = _SEVERITY_BONUS.get(str(candidate.get("severity") or "medium"), 0.20)
    performance = round(1.6 * quality - 0.8 + severity_bonus, 3)  # 相对基线的估计提升（百分点）

    epochs = float(params.get("epochs") or defaults["epochs"])
    batch = float(params.get("batch_size") or defaults["batch_size"])
    params_m = float(((config or {}).get("model") or {}).get("params_m") or 25)
    route_mult = _ROUTE_COST_MULT.get(str(candidate.get("route") or ""), 1.0)
    cost = round((epochs / 100.0) * (batch / 64.0) * (1 + params_m / 120.0) * route_mult, 2)
    time_hours = round(cost * 1.25, 2)
    memory_gb = round(2.0 + params_m * 0.02 * (1.5 if candidate.get("route") == "structure" else 1.0), 2)

    return {
        "objectives": {"performance": performance, "cost": cost, "time": time_hours},
        "uncertainty": {
            "performance": max(0.15, round(abs(performance) * 0.10, 3)),
            "cost": round(cost * 0.15, 2),
            "time": round(time_hours * 0.20, 2),
        },
        "memory_gb": memory_gb,
    }


def _point(trial_no: int, candidate: dict, params: dict, est: dict) -> dict:
    return {
        "trial": trial_no,
        "candidate_id": candidate.get("id", ""),
        "title": candidate.get("title", ""),
        "route": candidate.get("route", ""),
        "params": dict(params),
        "objectives": dict(est["objectives"]),
        "uncertainty": dict(est["uncertainty"]),
    }


def _sample_params(trial, space: dict) -> dict:
    params: dict = {}
    for dim, spec in space.items():
        if spec["kind"] == "int":
            params[dim] = trial.suggest_int(dim, int(spec["low"]), int(spec["high"]))
        elif spec["log"]:
            params[dim] = trial.suggest_float(dim, float(spec["low"]), float(spec["high"]), log=True)
        else:
            params[dim] = trial.suggest_float(dim, float(spec["low"]), float(spec["high"]))
    return params


def _optuna_search(
    space: dict, candidates: list[dict], priors: dict | None,
    config: dict | None, budgets: dict, n_trials: int,
) -> tuple[list[dict], int]:
    counters = {"resource": 0}
    points: list[dict] = []
    sampler = optuna.samplers.NSGAIISampler(seed=_SEED)
    study = optuna.create_study(
        directions=["maximize", "minimize", "minimize"], sampler=sampler,
    )

    def _objective(trial):
        index = trial.suggest_categorical("candidate_index", list(range(len(candidates))))
        candidate = candidates[index]
        params = _sample_params(trial, space)
        est = _estimate(candidate, params, priors, config)
        if est["objectives"]["cost"] > budgets["max_gpu_hours"] or est["memory_gb"] > budgets["max_memory_gb"]:
            counters["resource"] += 1
            raise optuna.TrialPruned()
        trial.set_user_attr("candidate_id", candidate.get("id", ""))
        trial.set_user_attr("params", params)
        trial.set_user_attr("objectives", est["objectives"])
        trial.set_user_attr("uncertainty", est["uncertainty"])
        return est["objectives"]["performance"], est["objectives"]["cost"], est["objectives"]["time"]

    study.optimize(_objective, n_trials=n_trials, catch=())
    by_id = {c.get("id", ""): c for c in candidates}
    for trial in study.trials:
        if trial.state != optuna.trial.TrialState.COMPLETE:
            continue
        candidate = by_id.get(trial.user_attrs.get("candidate_id", ""))
        if candidate is None:
            continue
        points.append(_point(
            trial.number, candidate,
            trial.user_attrs.get("params", {}),
            {"objectives": trial.user_attrs.get("objectives", {}),
             "uncertainty": trial.user_attrs.get("uncertainty", {})},
        ))
    return points, counters["resource"]


def _builtin_search(
    space: dict, candidates: list[dict], priors: dict | None,
    config: dict | None, budgets: dict, n_trials: int,
) -> tuple[list[dict], int]:
    rng = random.Random(_SEED)
    pruned = 0
    points: list[dict] = []
    for i in range(max(1, n_trials)):
        candidate = candidates[rng.randrange(len(candidates))]
        params: dict = {}
        for dim, spec in space.items():
            low, high = float(spec["low"]), float(spec["high"])
            if spec["kind"] == "int":
                params[dim] = rng.randint(int(low), int(high))
            elif spec["log"]:
                params[dim] = math.exp(rng.uniform(math.log(low), math.log(high)))
            else:
                params[dim] = rng.uniform(low, high)
        est = _estimate(candidate, params, priors, config)
        if est["objectives"]["cost"] > budgets["max_gpu_hours"] or est["memory_gb"] > budgets["max_memory_gb"]:
            pruned += 1
            continue
        points.append(_point(i, candidate, params, est))
    return points, pruned


def _dominates(a: dict, b: dict) -> bool:
    ao, bo = a["objectives"], b["objectives"]
    no_worse = (
        ao["performance"] >= bo["performance"]
        and ao["cost"] <= bo["cost"]
        and ao["time"] <= bo["time"]
    )
    strictly_better = (
        ao["performance"] > bo["performance"]
        or ao["cost"] < bo["cost"]
        or ao["time"] < bo["time"]
    )
    return no_worse and strictly_better


def pareto_front(points: list[dict], limit: int = 12) -> list[dict]:
    """非支配前沿（性能↑、成本↓、时间↓），按性能降序截断。"""
    front = [
        p for p in points
        if not any(_dominates(q, p) for q in points if q is not p)
    ]
    front.sort(key=lambda p: (-p["objectives"]["performance"], p["objectives"]["cost"]))
    return front[:limit]


def _minmax(values: list[float], higher_better: bool):
    low, high = min(values), max(values)
    if high - low < 1e-12:
        return lambda _v: 0.5
    if higher_better:
        return lambda v: (v - low) / (high - low)
    return lambda v: 1.0 - (v - low) / (high - low)


def rank_points(
    points: list[dict],
    candidates: list[dict],
    weights: dict | None,
) -> list[dict]:
    """规格⑥：按 性能/成本/时间/可解释性/风险/复杂度 加权，取每候选最佳点排序。"""
    if not points:
        return []
    w = {**_DEFAULT_WEIGHTS, **(weights or {})}
    total = sum(w.values()) or 1.0
    w = {k: v / total for k, v in w.items()}

    norm_perf = _minmax([p["objectives"]["performance"] for p in points], True)
    norm_cost = _minmax([p["objectives"]["cost"] for p in points], False)
    norm_time = _minmax([p["objectives"]["time"] for p in points], False)
    cats = prompts._CATEGORICAL_FIELDS
    by_id = {c.get("id", ""): c for c in candidates}

    for point in points:
        cand = by_id.get(point["candidate_id"], {})
        risk = cats["risk"].get(str(cand.get("risk") or "medium"), 0.5)
        complexity = cats["complexity"].get(str(cand.get("complexity") or "medium"), 0.5)
        interp = cats["interpretability"].get(str(cand.get("interpretability") or "medium"), 0.5)
        point["score"] = round(
            w["performance"] * norm_perf(point["objectives"]["performance"])
            + w["cost"] * norm_cost(point["objectives"]["cost"])
            + w["time"] * norm_time(point["objectives"]["time"])
            + w["interpretability"] * interp
            + w["risk"] * (1.0 - risk)
            + w["complexity"] * (1.0 - complexity),
            4,
        )

    best: dict[str, dict] = {}
    for point in points:
        current = best.get(point["candidate_id"])
        if current is None or point["score"] > current["score"]:
            best[point["candidate_id"]] = point
    ordered = sorted(best.values(), key=lambda p: (-p["score"], p["candidate_id"]))

    ranking = []
    for i, point in enumerate(ordered):
        cand = by_id.get(point["candidate_id"], {})
        ranking.append({
            "rank": i + 1,
            "candidate_id": point["candidate_id"],
            "title": point["title"],
            "route": point["route"],
            "severity": cand.get("severity", "medium"),
            "source": cand.get("source", ""),
            "evidence_refs": cand.get("evidence_refs") or [],
            "config_patch": cand.get("config_patch") or {},
            "params": point["params"],
            "objectives": point["objectives"],
            "uncertainty": point["uncertainty"],
            "score": point["score"],
            "risk": cand.get("risk", "medium"),
            "complexity": cand.get("complexity", "medium"),
            "interpretability": cand.get("interpretability", "medium"),
            "expected": cand.get("expected", ""),
        })
    return ranking


def optimize(
    candidates: list[dict],
    config: dict | None,
    priors: dict | None = None,
    designer=None,
) -> dict:
    """多目标优化主入口（永不抛异常；失败返回 ok=False + error）。"""
    cfg = designer if designer is not None else get_settings().designer
    budgets = {"max_gpu_hours": float(cfg.max_gpu_hours), "max_memory_gb": float(cfg.max_memory_gb)}
    result: dict = {
        "method": "optuna" if optuna is not None else "builtin",
        "fallback": optuna is None,
        "objectives": ["performance", "cost", "time"],
        "n_trials": int(cfg.n_trials),
        "n_evaluated": 0,
        "pruned": {"prior": 0, "resource": 0},
        "pruned_candidates": [],
        "space": {},
        "pareto": [],
        "ranking": [],
        "top_k": [],
        "note": "性能/成本/时间为代理模型估计值（±不确定度标注），非真实训练结果",
        "ok": False,
        "error": "",
    }
    if not candidates:
        result["error"] = "无候选方案"
        return result

    active, pruned_candidates = _prune_candidates(candidates, priors)
    result["pruned"]["prior"] = len(pruned_candidates)
    result["pruned_candidates"] = pruned_candidates
    if not active:
        result["error"] = "全部候选被先验剪枝（超参超出先验区间）"
        return result

    space = build_space(active, config, priors, bool(cfg.deep_verify))
    result["space"] = space

    if optuna is not None:
        try:
            points, pruned_trials = _optuna_search(space, active, priors, config, budgets, int(cfg.n_trials))
        except Exception as exc:  # optuna 异常 → 内置采样兜底
            logger.warning("optuna 多目标搜索失败，回退内置采样: %s", exc)
            result["fallback"] = True
            result["method"] = "builtin"
            points, pruned_trials = _builtin_search(space, active, priors, config, budgets, int(cfg.n_trials))
    else:
        points, pruned_trials = _builtin_search(space, active, priors, config, budgets, int(cfg.n_trials))

    result["pruned"]["resource"] = pruned_trials
    result["n_evaluated"] = len(points)
    if not points:
        result["error"] = "无可行采样点（全部被资源剪枝）"
        return result

    ranking = rank_points(points, active, cfg.weights)
    result["ranking"] = ranking
    result["top_k"] = ranking[: int(cfg.top_k)]
    result["pareto"] = pareto_front(points)
    result["ok"] = bool(ranking)
    return result
