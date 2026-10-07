"""规格④：多路由候选方案生成 —— LLM 生成 + 引用门控 + 启发式补齐。

四条路由（structure / hyperparam / data / strategy）保证方案多样性；每个候选
必须携带至少一个可解析的 evidence anchor id，否则剔除（规格⑪"无引用不建议"）。
LLM 不可用或输出不合规时，按诊断出的瓶颈类型生成启发式候选，管道不中断。
"""

from __future__ import annotations

import json
import logging
import re

from src.agents.base import parse_llm_json
from src.agents.experiment_designer import prompts
from src.agents.experiment_designer.optimization import FAMILY_DEFAULTS

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 8

#: 瓶颈类型 → 方案路由（resource_mismatch 通过调超参缓解，归入 hyperparam）
ROUTE_FOR_BOTTLENECK = {
    "model_outdated": "structure",
    "hyperparam_drift": "hyperparam",
    "data_insufficient": "data",
    "strategy_missing": "strategy",
    "resource_mismatch": "hyperparam",
}

_ROUTE_ANCHOR_KEYWORDS = {
    "structure": ("方法", "模型", "结构", "骨架"),
    "hyperparam": ("学习率", "批大小", "优化器", "调度", "超参"),
    "data": ("增强", "数据", "样本量", "功效", "不均衡"),
    "strategy": ("正则", "回退", "复现", "消融", "迁移", "早停"),
}

_METHOD_RE = re.compile(r"方法:\s*([^；;,，]+)")


def _anchor_ids(evidence: dict | None) -> set[str]:
    return {a.get("id") for a in (evidence or {}).get("anchors") or [] if a.get("id")}


def _coerce_hyperparams(raw: dict | None) -> dict:
    out: dict = {}
    for key, value in (raw or {}).items():
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out[str(key)] = value
        elif isinstance(value, str) and value.strip():
            out[str(key)] = value.strip()[:40]
    return out


def _normalize(raw: dict, known_ids: set[str], idx: int) -> dict | None:
    """校验收敛：路由合法 + 至少一个可解析引用，否则返回 None（剔除）。"""
    if not isinstance(raw, dict):
        return None
    route = str(raw.get("route") or "").strip().lower()
    if route not in prompts.VALID_ROUTES:
        return None
    refs = []
    for ref in raw.get("evidence_refs") or []:
        ref = str(ref)
        if ref in known_ids and ref not in refs:
            refs.append(ref)
    if not refs:
        return None
    cat = prompts._CATEGORICAL_FIELDS
    return {
        "id": f"c{idx}",
        "route": route,
        "title": str(raw.get("title") or f"候选方案 {idx}")[:60],
        "description": str(raw.get("description") or "")[:300],
        "config_patch": dict(raw.get("config_patch") or {}) if isinstance(raw.get("config_patch"), dict) else {},
        "hyperparams": _coerce_hyperparams(raw.get("hyperparams")),
        "evidence_refs": refs,
        "expected": str(raw.get("expected") or "")[:200],
        "risk": str(raw.get("risk") or "medium").lower() if str(raw.get("risk") or "").lower() in cat["risk"] else "medium",
        "complexity": str(raw.get("complexity") or "medium").lower() if str(raw.get("complexity") or "").lower() in cat["complexity"] else "medium",
        "interpretability": str(raw.get("interpretability") or "medium").lower() if str(raw.get("interpretability") or "").lower() in cat["interpretability"] else "medium",
        "source": "llm",
    }


def _find_anchor(anchors: list[dict], route: str) -> dict | None:
    keywords = _ROUTE_ANCHOR_KEYWORDS.get(route, ())
    preferred = ("kg-chain", "kg-entity") if route == "structure" else ("kb",)
    for kind in preferred:
        for anchor in anchors:
            if anchor.get("kind") != kind:
                continue
            hay = " ".join(str(anchor.get(k) or "") for k in ("section", "title", "name", "text"))
            if not keywords or any(kw in hay for kw in keywords):
                return anchor
    for anchor in anchors:  # 放宽：任何锚点都好过无引用
        return anchor
    return None


def _method_from_anchors(anchors: list[dict]) -> str:
    for anchor in anchors:
        if anchor.get("kind") == "kg-chain":
            match = _METHOD_RE.search(str(anchor.get("text") or ""))
            if match:
                return match.group(1).strip()
        if anchor.get("kind") == "kg-entity" and anchor.get("type") == "Method":
            return str(anchor.get("name") or "").strip()
    return ""


def _heuristic_one(
    route: str,
    bottleneck: dict | None,
    config: dict,
    evidence: dict,
    priors: dict | None,
    idx: int,
) -> dict | None:
    anchors = (evidence or {}).get("anchors") or []
    known = _anchor_ids(evidence)
    refs = [r for r in ((bottleneck or {}).get("evidence_refs") or []) if r in known][:2]
    if not refs:
        anchor = _find_anchor(anchors, route)
        if anchor is None:
            return None
        refs = [anchor["id"]]

    family = str(((config or {}).get("model") or {}).get("family") or "default").lower()
    defaults = FAMILY_DEFAULTS.get(family, FAMILY_DEFAULTS["default"])
    finding = str((bottleneck or {}).get("finding") or "")[:200]

    if route == "structure":
        method = _method_from_anchors(anchors) or "证据中的同任务方法"
        return {
            "id": f"c{idx}", "route": route, "source": "heuristic",
            "title": f"结构升级：{method}",
            "description": finding or "采用证据中同任务的更强模型结构",
            "config_patch": {"model.name": method},
            "hyperparams": {"learning_rate": defaults["learning_rate"], "batch_size": defaults["batch_size"]},
            "evidence_refs": refs,
            "expected": "结构收益需通过消融验证（估计带不确定度）",
            "risk": "high", "complexity": "high", "interpretability": "medium",
        }
    if route == "hyperparam":
        hyper = {}
        for dim, value in defaults.items():
            prior = ((priors or {}).get("hyperparams") or {}).get(dim)
            hyper[dim] = round(prior["mean"], 6) if prior and prior.get("n") else value
        if any((bottleneck or {}).get("type") == "resource_mismatch" for b in (bottleneck,)):
            hyper["batch_size"] = min(int(hyper.get("batch_size", 64)), 64)
            hyper["epochs"] = min(int(hyper.get("epochs", 100)), 80)
        return {
            "id": f"c{idx}", "route": route, "source": "heuristic",
            "title": "超参对齐先验区间",
            "description": finding or "把学习率/批大小/优化器对齐到证据先验区间",
            "config_patch": {f"hyperparams.{k}": v for k, v in hyper.items()},
            "hyperparams": hyper,
            "evidence_refs": refs,
            "expected": "对齐经验区间，降低不收敛风险",
            "risk": "low", "complexity": "low", "interpretability": "high",
        }
    if route == "data":
        return {
            "id": f"c{idx}", "route": route, "source": "heuristic",
            "title": "数据增强与样本保障",
            "description": finding or "补充增强/采样策略，缓解样本不足",
            "config_patch": {"data.augmentation": ["RandAugment", "MixUp"]},
            "hyperparams": {"epochs": defaults["epochs"]},
            "evidence_refs": refs,
            "expected": "增强策略收益需与无增强基线消融对比",
            "risk": "medium", "complexity": "medium", "interpretability": "medium",
        }
    return {
        "id": f"c{idx}", "route": "strategy", "source": "heuristic",
        "title": "训练策略补齐",
        "description": finding or "补齐调度/早停/固定种子等策略",
        "config_patch": {"strategy": ["cosine 调度", "早停（patience 10）", "固定随机种子 ≥3 个"]},
        "hyperparams": {"scheduler": "cosine"},
        "evidence_refs": refs,
        "expected": "提升可复现性与稳定性",
        "risk": "low", "complexity": "low", "interpretability": "high",
    }


def _heuristic_for_routes(
    routes: list[str],
    config: dict,
    diagnosis: dict,
    evidence: dict,
    priors: dict | None,
    start: int,
) -> list[dict]:
    bottlenecks = diagnosis.get("bottlenecks") or []
    out = []
    idx = start
    for route in routes:
        matching = next(
            (b for b in bottlenecks if ROUTE_FOR_BOTTLENECK.get(b.get("type")) == route), None
        )
        candidate = _heuristic_one(route, matching, config, evidence, priors, idx)
        if candidate:
            out.append(candidate)
            idx += 1
    return out


def _attach_severity(candidates: list[dict], diagnosis: dict) -> None:
    """按候选路由关联瓶颈的最高严重度，供排序的"风险收益"参考。"""
    severities: dict[str, str] = {}
    for b in diagnosis.get("bottlenecks") or []:
        route = ROUTE_FOR_BOTTLENECK.get(b.get("type"))
        if not route:
            continue
        sev = str(b.get("severity") or "medium").lower()
        order = {"high": 3, "medium": 2, "low": 1}
        if order.get(sev, 2) > order.get(severities.get(route, "low"), 1):
            severities[route] = sev
    for cand in candidates:
        cand["severity"] = severities.get(cand["route"], "medium")


async def generate_candidates(
    intent: str,
    config: dict,
    diagnosis: dict,
    evidence: dict,
    priors: dict | None,
    llm_call,
    max_candidates: int = MAX_CANDIDATES,
) -> list[dict]:
    """生成候选方案集（LLM 优先，证据门控 + 启发式补齐；永不抛异常）。"""
    known = _anchor_ids(evidence)
    candidates: list[dict] = []

    prompt = prompts.CANDIDATE_PROMPT.format(
        intent=str(intent or "")[:300],
        config=prompts.format_config(config),
        diagnosis=json.dumps(diagnosis or {}, ensure_ascii=False)[:1500],
        evidence=prompts.format_evidence(evidence),
        priors=prompts.format_priors(priors),
    )
    try:
        resp = await llm_call(prompt)
        data = parse_llm_json(resp)
        for raw in data.get("candidates") or []:
            if len(candidates) >= max_candidates:
                break
            cand = _normalize(raw, known, len(candidates) + 1)
            if cand is None:
                continue
            candidates.append(cand)
    except Exception as exc:  # LLM 不可用：全量走启发式
        logger.warning("design candidates: LLM 生成降级: %s", exc)

    routes = {c["route"] for c in candidates}
    if not candidates:
        # LLM 完全失败：全部路由走启发式（锚点允许时），保证方案多样性
        candidates = _heuristic_for_routes(
            list(prompts.VALID_ROUTES), config, diagnosis, evidence, priors, start=1
        )
    else:
        needed = {
            ROUTE_FOR_BOTTLENECK[b.get("type")]
            for b in (diagnosis or {}).get("bottlenecks") or []
            if b.get("type") in ROUTE_FOR_BOTTLENECK
        }
        missing = [r for r in prompts.VALID_ROUTES if r in needed and r not in routes]
        if missing and len(candidates) < max_candidates:
            candidates.extend(_heuristic_for_routes(
                missing, config, diagnosis, evidence, priors, start=len(candidates) + 1
            ))

    _attach_severity(candidates, diagnosis or {})
    return candidates[:max_candidates]
