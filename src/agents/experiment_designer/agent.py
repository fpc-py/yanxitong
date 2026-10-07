"""Experiment Designer Agent v3.0 — 10 段实验方案优化引擎.

① 配置解析 → ② 证据检索（KG 路径 + 知识库 + 文献锚点）→ ① 瓶颈诊断 →
③ 方案空间建模 → ④ 多路候选生成 → ⑤ Optuna 多目标优化 → ⑥ 加权排序推荐 →
⑧ 验证计划 + ⑨ 推荐配置与可执行代码 → ⑦⑪ 可行性/幻觉分层校验 → ⑫ 产出打包。

阶段顺序说明：瓶颈诊断消费证据锚点，故检索先于诊断；可行性校验消费静态校验
结果与验证计划，故 plan（含代码生成）先于 validate。

经典契约保持不变：``experiment_design``（假设/变量/实验组/统计方法/冲突…）、
``has_conflicts`` / ``conflict_count`` / ``citation_gate`` 与置信度公式
（0.9 / 0.85 / 0.5、全剔除 0.4、部分剔除 ×0.9；校验 fail 追加 cap 0.4）；
新增 ``design_engine``（配置/诊断/证据/候选/优化/校验/计划/推荐/静态校验/报告）
与 ``design_run``（run_id/阶段轨迹/产物清单/降级状态）供 API 与前端使用。

模块级 :func:`record_feedback` 实现规格⑩闭环：实验结果回流先验存储（必写）与
知识图谱（Experiment 节点 + ACHIEVES/USES_DATASET 边）；KG 不可用仅降级。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from src.agents.base import AgentResult, BaseAgent, parse_llm_json
from src.agents.experiment_designer import candidates as candidates_mod
from src.agents.experiment_designer import codegen, packaging, prompts
from src.agents.experiment_designer import evidence as evidence_mod
from src.agents.experiment_designer import optimization
from src.agents.experiment_designer import validation as validation_mod
from src.core.config import get_settings
from src.knowledge import prior_store
from src.knowledge.graph_store import get_graph_store, make_entity_id
from src.tools.paper_schema import make_paper_id

logger = logging.getLogger(__name__)

_CITE_RE = re.compile(r"\[(\d+)\]")

DESIGN_PROMPT = """你是一个科研实验设计专家。请基于文献调研结果和数据分析发现，设计实验方案。

文献发现:
{literature}

数据分析发现:
{data_findings}

请设计实验方案，返回JSON (no markdown fences):
{{
    "hypothesis": "研究假设",
    "rationale": "假设依据",
    "variables": {{
        "independent": ["自变量1"],
        "dependent": ["因变量1"],
        "controlled": ["控制变量1"]
    }},
    "experimental_groups": ["实验组1 [1]", "对照组 [2]"],
    "statistical_methods": ["统计方法1 [1]"],
    "expected_outcomes": ["预期结果1 [1]"],
    "conflicts": [
        {{"claim_a": "文献A的观点", "source_a": "文献A标题", "claim_b": "文献B的观点", "source_b": "文献B标题", "suggested_resolution": "建议的验证方法"}}
    ],
    "recommended_validation": "推荐的验证方案",
    "risk_assessment": "实验风险评估"
}}

引用要求：experimental_groups、statistical_methods、expected_outcomes 的每一项都必须以 [编号]
标注其文献依据，编号与「文献发现」列表一致（如 [1]、[2]）；没有文献依据的条目一律不要输出。
如果没有发现冲突，conflicts为空数组。所有文本字段使用中文。"""


# Default plan skeleton used whenever the model output cannot be parsed as JSON.
_EMPTY_PLAN: dict[str, Any] = {
    "hypothesis": "",
    "rationale": "",
    "variables": {"independent": [], "dependent": [], "controlled": []},
    "experimental_groups": [],
    "statistical_methods": [],
    "expected_outcomes": [],
    "conflicts": [],
    "recommended_validation": "",
    "risk_assessment": "",
}

#: 规范配置的顶级键；其余键在解析阶段丢弃
_CONFIG_KEYS = ("task", "model", "hyperparams", "data", "resources", "metrics", "missing")
_FLAT_HYPER_KEYS = (
    "learning_rate", "batch_size", "epochs", "optimizer", "scheduler",
    "weight_decay", "dropout",
)


def _strip_code_fences(text: str) -> str:
    """Remove leading/trailing markdown code fences from an LLM completion."""
    text = text.strip()
    if text.startswith("```"):
        # Drop the opening fence (```json / ```python / ```).
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _format_literature(papers: list[dict[str, Any]], limit: int = 10) -> str:
    """Render retrieved papers into a compact, prompt-friendly digest."""
    if not papers:
        return "暂无文献数据"

    blocks: list[str] = []
    for i, paper in enumerate(papers[:limit]):
        title = paper.get("title") or "(无标题)"
        year = paper.get("year") or ""
        findings = paper.get("key_findings") or []
        if not findings:
            abstract = (paper.get("abstract") or "").strip()
            findings = [abstract[:200]] if abstract else []
        findings_text = "; ".join(findings) if findings else "无明确发现"
        methods = paper.get("methods") or []
        methods_text = (
            f"\n方法: {', '.join(methods[:5])}" if methods else ""
        )
        blocks.append(
            f"[{i + 1}] {title} ({year})\n"
            f"发现: {findings_text}{methods_text}"
        )
    return "\n\n".join(blocks)


def _format_data_findings(experiment_results: dict[str, Any] | None) -> str:
    """Render the data-analyst report into prompt-friendly text."""
    if not experiment_results:
        return "暂无数据分析结果"

    text = experiment_results.get("stdout") or ""
    if not text:
        findings = experiment_results.get("findings") or []
        text = "\n".join(findings) if findings else "暂无数据分析结果"

    text = str(text)
    if len(text) > 2000:
        text = text[:2000] + "..."
    return text


def _normalize_config(raw: dict | None) -> dict:
    """把用户/LLM 产出的配置收敛到规范形状（未知键丢弃、标量提升为结构）。"""
    out: dict = {}
    if not isinstance(raw, dict) or not raw:
        return out

    task = str(raw.get("task") or "").strip()
    if task:
        out["task"] = task[:120]

    model = raw.get("model")
    if isinstance(model, str) and model.strip():
        out["model"] = {"name": model.strip()[:80]}
    elif isinstance(model, dict):
        entry: dict = {}
        name = str(model.get("name") or "").strip()
        if name:
            entry["name"] = name[:80]
        family = str(model.get("family") or "").strip().lower()
        if family:
            entry["family"] = family[:20]
        params_m = model.get("params_m")
        if isinstance(params_m, (int, float)) and not isinstance(params_m, bool):
            entry["params_m"] = float(params_m)
        if entry:
            out["model"] = entry

    hyper: dict = {}
    source_hyper = raw.get("hyperparams")
    if isinstance(source_hyper, dict):
        for key, value in source_hyper.items():
            if value is None or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                hyper[str(key)] = value
            elif isinstance(value, str) and value.strip():
                hyper[str(key)] = value.strip()[:40]
    for key in _FLAT_HYPER_KEYS:  # 平铺写法（lr=0.01 等）也接受
        if key in raw and key not in hyper and raw[key] is not None:
            value = raw[key]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                hyper[key] = value
            elif isinstance(value, str) and value.strip():
                hyper[key] = value.strip()[:40]
    if hyper:
        out["hyperparams"] = hyper

    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw.get("dataset")
    if isinstance(data, dict):
        entry = {}
        name = str(data.get("name") or "").strip()
        if name:
            entry["name"] = name[:80]
        for key in ("n_samples", "n_classes"):
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                entry[key] = int(value)
        augmentation = data.get("augmentation")
        if isinstance(augmentation, list) and augmentation:
            entry["augmentation"] = [str(a)[:40] for a in augmentation[:8]]
        if entry:
            out["data"] = entry

    resources = raw.get("resources")
    if isinstance(resources, dict):
        entry = {}
        gpu = str(resources.get("gpu") or "").strip()
        if gpu:
            entry["gpu"] = gpu[:60]
        for key in ("gpu_hours", "memory_gb"):
            value = resources.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                entry[key] = float(value)
        if entry:
            out["resources"] = entry

    metrics = raw.get("metrics")
    if isinstance(metrics, list) and metrics:
        out["metrics"] = [str(m)[:40] for m in metrics[:8] if str(m).strip()]

    missing = raw.get("missing")
    if isinstance(missing, list) and missing:
        out["missing"] = [str(m)[:60] for m in missing[:10] if str(m).strip()]

    return out


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并（override 优先）；override 中的空值不覆盖 base 的非空值。"""
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        elif value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _fallback_report(design: dict, gate: dict) -> str:
    """LLM 报告失败时的结构化回退（仍含假设/变量/统计方法小节）。"""
    variables = design.get("variables") or {}
    return "\n".join([
        "# 实验方案报告",
        "## 研究假设",
        design.get("hypothesis") or "（未生成假设）",
        "## 变量设计",
        f"- 自变量: {', '.join(variables.get('independent') or []) or '暂无'}",
        f"- 因变量: {', '.join(variables.get('dependent') or []) or '暂无'}",
        f"- 控制变量: {', '.join(variables.get('controlled') or []) or '暂无'}",
        "## 统计方法",
        ", ".join(design.get("statistical_methods") or []) or "暂无",
        "## 说明",
        f"LLM 报告生成失败，本报告由结构化字段回退生成；引用门控 保留 {gate.get('kept', 0)}/"
        f"{gate.get('checked', 0)} 条；性能/成本/时间均为代理模型估计值。",
    ])


class ExperimentDesignerAgent(BaseAgent):
    """实验设计智能体 — 假设生成、变量设计、冲突检测、验证方案推荐."""

    name = "experiment_designer"
    description = "实验设计智能体 — 假设生成、变量设计、冲突检测、验证方案推荐"
    model_role = "deep_reasoning"

    async def _execute_impl(self, state: dict[str, Any]) -> AgentResult:
        intent = str(state.get("user_query") or state.get("research_topic") or "")
        session_id = str(state.get("session_id") or "")
        papers = state.get("literature_results") or []
        experiment_results = state.get("experiment_results") or {}
        if not isinstance(experiment_results, dict):
            experiment_results = {}
        settings = get_settings()

        stages: list[dict] = []
        stage_mark = time.perf_counter()

        def stage(name: str, ok: bool = True, **extra) -> None:
            nonlocal stage_mark
            now = time.perf_counter()
            entry = {"stage": name, "ok": bool(ok), "ms": round((now - stage_mark) * 1000)}
            entry.update(extra)
            stages.append(entry)
            stage_mark = now
            self._audit(f"design_{name}", {"ok": bool(ok), **extra})
            self._stage_audit(session_id, name, {"ok": bool(ok), **extra})

        self._audit("design_start", {
            "papers": len(papers), "has_data": bool(experiment_results),
            "query": intent[:100],
        })

        run_id, run_dir = "", None
        try:
            run_id, run_dir = packaging.new_run_dir(session_id or "anon")
        except OSError as exc:
            self._audit("run_dir_degraded", {"error": str(exc)[:200]})

        # ① 配置解析：请求体 experiment_config 优先，query 文本由 LLM 解析补齐
        config, config_source = await self._parse_config(intent, state.get("experiment_config"))
        stage("parse", ok=True, source=config_source, fields=sorted(k for k in config if config.get(k)))

        # ② 证据检索：KG 路径 + 知识库 + 文献锚点（任一来源失败只降级）
        evidence = await evidence_mod.collect_evidence(intent, config, session_id, papers)
        stage("retrieve", ok=not evidence.get("degraded"), degraded=bool(evidence.get("degraded")),
              anchors=len(evidence.get("anchors") or []), sources=evidence.get("sources", 0))

        priors = self._load_priors(config)

        # ① 瓶颈诊断：五类；无证据锚点支撑的瓶颈剔除
        diagnosis = await self._diagnose(config, evidence, priors)
        stage("diagnose", ok=True, bottlenecks=len(diagnosis.get("bottlenecks") or []),
              dropped=diagnosis.get("dropped", 0))

        # ③ 方案空间建模：配置维度 ∩ 先验区间（候选数值维度在优化段并入）
        base_space = optimization.build_space([], config, priors, bool(settings.designer.deep_verify))
        stage("build", ok=True, dims=len(base_space))

        # ④ 候选生成（多路由 + 证据门控）+ 经典方案（假设/变量，规格④引用门控）
        candidates = await candidates_mod.generate_candidates(
            intent, config, diagnosis, evidence, priors, self._call_llm
        )
        design, gate = await self._classic_design(intent, papers, experiment_results)
        self._audit("citation_gate", {k: gate[k] for k in ("checked", "kept", "dropped", "skipped")})
        stage("generate", ok=bool(candidates), candidates=len(candidates),
              routes=sorted({str(c.get("route")) for c in candidates}),
              hypothesis=bool(design.get("hypothesis")))

        # ⑤⑥ 多目标优化：先验/资源剪枝 + Pareto 前沿 + 六维加权 Top-K
        optimization_result = optimization.optimize(candidates, config, priors, settings.designer)
        pruned = optimization_result.get("pruned") or {}
        stage("optimize", ok=optimization_result.get("ok"),
              method=optimization_result.get("method"), trials=optimization_result.get("n_trials"),
              evaluated=optimization_result.get("n_evaluated"),
              pruned_prior=pruned.get("prior", 0), pruned_resource=pruned.get("resource", 0),
              error=str(optimization_result.get("error") or "")[:120])

        top_k = optimization_result.get("top_k") or []
        winner = top_k[0] if top_k else {}
        recommended = codegen.build_recommended_config(config, winner) if candidates else {}
        stage("rank", ok=bool(winner), top_k=len(top_k),
              winner=winner.get("candidate_id", ""), score=winner.get("score"))

        # ⑧⑨ 验证计划 + 推荐配置的可执行代码（coder 角色；沙箱静态校验）
        plan = validation_mod.build_validation_plan(config, winner, evidence)
        code = await self._generate_train_code(intent, recommended, evidence) if winner else ""
        static = await codegen.static_check(code) if code else None
        stage("plan", ok=True, ablation=len(plan.get("ablation") or []),
              code_len=len(code), syntax_ok=bool((static or {}).get("syntax_ok")),
              missing_deps=len((static or {}).get("missing") or []))

        # ⑦⑪ 校验：可行性五类 + 确定性检查 + LLM 复核（deep_verify）
        estimate = {"cost": (winner.get("objectives") or {}).get("cost"),
                    "time": (winner.get("objectives") or {}).get("time")} if winner else None
        feasibility = validation_mod.check_feasibility(intent, config, estimate, static, plan)
        deterministic = validation_mod.deterministic_checks(candidates, optimization_result, evidence)
        llm_review = None
        if settings.designer.deep_verify:
            llm_review = await validation_mod.llm_review(
                self._call_llm, recommended, evidence, deterministic, plan
            )
        overall = validation_mod.overall_status(deterministic, llm_review)
        validation_report = {
            "overall": overall,
            "deterministic": deterministic,
            "llm": llm_review,
            "feasibility": feasibility,
        }
        if overall == "fail":
            self._flag_hallucination(session_id, validation_report, design)
        stage("validate", ok=overall != "fail", overall=overall,
              feasibility=feasibility.get("overall"),
              degraded=bool((llm_review or {}).get("degraded")))

        # ⑫ 解释报告 + 产出打包（run 目录：stages/候选/推荐/证据/校验/代码/报告）
        report_md = await self._report(intent, {
            "config": config, "diagnosis": diagnosis,
            "candidates": [
                {"id": c.get("id"), "route": c.get("route"), "title": c.get("title"),
                 "expected": c.get("expected"), "risk": c.get("risk")}
                for c in candidates
            ],
            "optimization": {"method": optimization_result.get("method"),
                             "top_k": top_k, "pareto": (optimization_result.get("pareto") or [])[:5]},
            "validation_plan": plan, "feasibility": feasibility, "classic_design": design,
        })
        if not report_md:
            report_md = _fallback_report(design, gate)
        stage("package", ok=run_dir is not None, report_len=len(report_md),
              ranked=len(optimization_result.get("ranking") or []))
        manifest: dict = {}
        if run_dir is not None:
            try:
                manifest = packaging.assemble_run(
                    run_dir,
                    session_id=session_id or "anon",
                    run_id=run_id,
                    intent=intent,
                    stages=stages,
                    candidates=candidates,
                    recommended=recommended,
                    code=code,
                    evidence=evidence,
                    validation=validation_report,
                    report_md=report_md,
                    manifest_extra={
                        "config_source": config_source,
                        "optimization": {
                            "method": optimization_result.get("method"),
                            "n_trials": optimization_result.get("n_trials"),
                            "n_evaluated": optimization_result.get("n_evaluated"),
                            "pruned": pruned,
                        },
                        "validation_overall": overall,
                        "evidence_sources": evidence.get("sources", 0),
                        "evidence_degraded": bool(evidence.get("degraded")),
                        "code_syntax_ok": bool((static or {}).get("syntax_ok")),
                    },
                )
                stages[-1]["files"] = len(manifest.get("files") or [])
            except OSError as exc:
                self._audit("package_degraded", {"error": str(exc)[:200]})

        conflict_count = len(design.get("conflicts", []) or [])
        has_conflicts = conflict_count > 0

        # 置信度公式（硬契约）：冲突提升 / 无假设降级 / 全剔除 0.4 / 部分剔除 ×0.9
        confidence = 0.9 if has_conflicts else 0.85
        if not design.get("hypothesis"):
            confidence = 0.5
        if gate["checked"] and not gate["kept"]:
            confidence = min(confidence, 0.4)  # 全部建议无引用依据
        elif gate["dropped"]:
            confidence = round(confidence * 0.9, 3)
        if validation_report["overall"] == "fail":
            confidence = min(confidence, 0.4)  # 规格⑪：校验 fail 追加封顶

        self._audit("design_complete", {
            "hypothesis": design.get("hypothesis", "")[:100],
            "conflicts": conflict_count,
            "methods": len(design.get("statistical_methods", []) or []),
            "candidates": len(candidates), "validation": overall, "run_id": run_id,
        })

        return AgentResult(
            success=True,
            data={
                # 经典契约键（保持不变）
                "experiment_design": design,
                "has_conflicts": has_conflicts,
                "conflict_count": conflict_count,
                "citation_gate": gate,
                # 实验方案优化引擎（规格①-⑫）
                "design_engine": {
                    "config": config,
                    "config_source": config_source,
                    "diagnosis": diagnosis,
                    "evidence": evidence,
                    "priors": {
                        "rows": int((priors or {}).get("rows") or 0),
                        "hyperparams": sorted(((priors or {}).get("hyperparams") or {}).keys()),
                        "metrics": ((priors or {}).get("metrics") or [])[:10],
                    },
                    "candidates": candidates,
                    "optimization": optimization_result,
                    "validation": validation_report,
                    "plan": plan,
                    "recommended": recommended,
                    "static_check": static,
                    "report": report_md,
                },
                "design_run": {
                    "run_id": run_id,
                    "degraded": bool(evidence.get("degraded") or (static is None and not code)),
                    "stages": stages,
                    "files": manifest.get("files", []),
                    "optimization_method": optimization_result.get("method", ""),
                    "validation_overall": overall,
                },
            },
            confidence=confidence,
        )

    # ---- 各阶段实现 ------------------------------------------------------

    async def _parse_config(self, intent: str, provided: dict | None) -> tuple[dict, str]:
        """请求体配置优先；query 由 LLM 解析补齐（失败仅降级为无配置）。"""
        provided = provided if isinstance(provided, dict) and provided else None
        parsed: dict = {}
        prompt = prompts.PARSE_PROMPT.format(
            query=str(intent or "")[:400],
            provided=json.dumps(provided or {}, ensure_ascii=False)[:1500],
        )
        try:
            data = parse_llm_json(await self._call_llm(prompt, json_mode=True, enable_thinking=False))
            if isinstance(data, dict):
                parsed = _normalize_config(data)
        except Exception as exc:
            self._audit("parse_failed", {"error": str(exc)[:200]})
        merged = _deep_merge(parsed, _normalize_config(provided or {}))
        source = "provided" if provided else ("parsed" if parsed else "none")
        return merged, source

    def _load_priors(self, config: dict | None) -> dict:
        try:
            return prior_store.query_priors(task=str((config or {}).get("task") or ""))
        except Exception as exc:
            self._audit("priors_degraded", {"error": str(exc)[:200]})
            return {}

    async def _diagnose(self, config: dict, evidence: dict, priors: dict) -> dict:
        prompt = prompts.DIAGNOSE_PROMPT.format(
            config=prompts.format_config(config),
            evidence=prompts.format_evidence(evidence),
            priors=prompts.format_priors(priors),
        )
        try:
            data = parse_llm_json(await self._call_llm(prompt))
            if not isinstance(data, dict):
                data = {}
        except Exception as exc:
            self._audit("diagnose_failed", {"error": str(exc)[:200]})
            data = {}

        known = {a.get("id") for a in (evidence.get("anchors") or []) if a.get("id")}
        kept: list[dict] = []
        dropped = 0
        for item in (data.get("bottlenecks") or []):
            if not isinstance(item, dict):
                continue
            refs = [str(r) for r in (item.get("evidence_refs") or []) if str(r) in known]
            if not refs:  # 规格⑪：无证据支撑的瓶颈剔除
                dropped += 1
                continue
            item["evidence_refs"] = refs[:4]
            kept.append(item)
        return {"bottlenecks": kept[:6], "summary": str(data.get("summary") or "")[:500],
                "dropped": dropped}

    async def _classic_design(
        self, intent: str, papers: list[dict], experiment_results: dict
    ) -> tuple[dict, dict]:
        """经典方案生成（假设/变量/实验组/统计方法）+ 规格④引用门控。"""
        prompt = DESIGN_PROMPT.format(
            literature=_format_literature(papers)[:5000],
            data_findings=_format_data_findings(experiment_results),
        )
        try:
            resp = await self._call_llm(prompt)
        except Exception as exc:
            self._audit("classic_design_failed", {"error": str(exc)[:200]})
            resp = ""
        design = self._parse(resp, intent)
        gate = self._enforce_citations(design, papers)
        return design, gate

    async def _generate_train_code(self, intent: str, recommended: dict, evidence: dict) -> str:
        """coder 角色生成 train.py；异常/空输出回退默认模型一次。"""
        try:
            code = await codegen.generate_code(
                intent, recommended, evidence,
                lambda prompt: self._call_llm(prompt, enable_thinking=False, role="coder"),
            )
            if code:
                return code
            raise ValueError("coder 返回空代码")
        except Exception as exc:
            self._audit("coder_fallback", {"error": str(exc)[:200]})
            try:
                return await codegen.generate_code(
                    intent, recommended, evidence,
                    lambda prompt: self._call_llm(prompt, enable_thinking=False),
                )
            except Exception as exc2:
                self._audit("codegen_failed", {"error": str(exc2)[:200]})
                return ""

    async def _report(self, intent: str, payload: dict) -> str:
        prompt = prompts.REPORT_PROMPT.format(
            intent=str(intent or "")[:300],
            payload=json.dumps(payload, ensure_ascii=False, default=str)[:6000],
        )
        try:
            return str(await self._call_llm(prompt) or "").strip()
        except Exception as exc:
            self._audit("report_failed", {"error": str(exc)[:200]})
            return ""

    # ---- 辅助 ------------------------------------------------------------

    def _stage_audit(self, session_id: str, stage_name: str, detail: dict) -> None:
        try:
            from src.observability.audit_store import record_audit

            record_audit(session_id, self.name, f"design_{stage_name}", detail)
        except Exception:
            logger.debug("stage audit degraded", exc_info=True)

    def _flag_hallucination(self, session_id: str, validation_report: dict, design: dict) -> None:
        failed = [
            {"check": c.get("id"), "detail": c.get("finding")}
            for c in (validation_report.get("deterministic") or {}).get("checks", [])
            if c.get("severity") == "fail" and not c.get("ok")
        ][:5]
        detail = {
            "overall": validation_report.get("overall"),
            "failed_checks": failed,
            "llm_consistency": (validation_report.get("llm") or {}).get("consistency", ""),
            "hypothesis": str(design.get("hypothesis") or "")[:120],
        }
        self._audit("validation_failed", detail)
        try:
            from src.observability.audit_store import record_hallucination_flag
            from src.observability.metrics import track_hallucination_flag

            record_hallucination_flag(session_id, "design_validation", "high", detail)
            track_hallucination_flag("design_validation", "high")
        except Exception:
            logger.debug("hallucination flag degraded", exc_info=True)

    def _enforce_citations(self, plan: dict[str, Any], papers: list[dict[str, Any]]) -> dict[str, Any]:
        """规格④：建议条目必须引用检索到的文献（KG Paper 节点身份），无引用剔除。

        文献编号与 :func:`_format_literature` 的 [n] 一致（前 10 篇）；条目中的
        每个 [n] 都必须解析到一篇真实论文。没有文献可引用时跳过门控（降级）。
        """
        resolvable: dict[int, dict[str, Any]] = {}
        for i, paper in enumerate(papers[:10]):
            pid = make_paper_id(paper)
            if pid:
                resolvable[i + 1] = {
                    "paper_id": pid,
                    "title": paper.get("title", ""),
                    "arxiv_id": paper.get("arxiv_id", ""),
                }

        stats: dict[str, Any] = {
            "checked": 0, "kept": 0, "dropped": 0,
            "dropped_items": [], "cited_papers": {}, "skipped": False,
        }
        if not resolvable:
            stats["skipped"] = True
            stats["reason"] = "无可引用文献"
            return stats

        for field in ("experimental_groups", "statistical_methods", "expected_outcomes"):
            kept: list[str] = []
            for item in plan.get(field) or []:
                stats["checked"] += 1
                refs = [resolvable[int(n)] for n in _CITE_RE.findall(str(item)) if int(n) in resolvable]
                if refs:
                    kept.append(item)
                    stats["kept"] += 1
                    for ref in refs:
                        stats["cited_papers"][ref["paper_id"]] = ref
                else:
                    stats["dropped"] += 1
                    stats["dropped_items"].append({"field": field, "item": str(item)[:120]})
            plan[field] = kept
        return stats

    def _parse(self, resp: str, query: str) -> dict[str, Any]:
        """Parse the model's JSON plan, falling back to a degraded skeleton."""
        try:
            design = json.loads(_strip_code_fences(resp))
            if not isinstance(design, dict):
                raise json.JSONDecodeError("not an object", resp, 0)
        except (json.JSONDecodeError, ValueError):
            logger.warning("ExperimentDesigner: model output was not valid JSON")
            design = {
                **_EMPTY_PLAN,
                "hypothesis": query or "无法解析的实验设计",
                "rationale": (resp or "")[:500],
            }

        # Normalise the schema so downstream consumers always find every key.
        plan = {**_EMPTY_PLAN, **design}
        variables = dict(plan["variables"]) if isinstance(plan.get("variables"), dict) else {}
        variables.setdefault("independent", [])
        variables.setdefault("dependent", [])
        variables.setdefault("controlled", [])
        plan["variables"] = variables
        for key in (
            "experimental_groups",
            "statistical_methods",
            "expected_outcomes",
            "conflicts",
        ):
            if not isinstance(plan.get(key), list):
                plan[key] = []
        if not isinstance(plan.get("hypothesis"), str):
            plan["hypothesis"] = str(plan.get("hypothesis", ""))
        return plan


async def record_feedback(
    session_id: str,
    run_id: str,
    candidate_id: str = "",
    metrics: dict | None = None,
    cost: float | None = None,
    duration_hours: float | None = None,
    notes: str = "",
) -> dict:
    """规格⑩闭环：实验结果回流 → 先验存储（必写）+ KG（尽力）。

    KG 写入：Experiment 节点（``exp:{session_id}:{run_id}:{cid}``）+
    ``ACHIEVES``（Experiment→Method/Metric）与 ``USES_DATASET``（Experiment→Dataset）
    边；受 ``kg.build_enabled`` 总开关与 schema 白名单约束，Neo4j 不可用仅降级
    （先验存储已写入，audit 记 ``kg_feedback_degraded``）。
    """
    manifest = packaging.read_manifest(session_id, run_id)
    if not manifest:
        return {"ok": False, "error": "设计运行不存在"}

    recommended: dict = {}
    path = packaging.find_file(session_id, run_id, "recommended.json")
    if path is not None:
        try:
            recommended = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            recommended = {}
    meta = recommended.get("_meta") or {}
    cid = candidate_id or str(meta.get("candidate_id") or "")
    task = str(recommended.get("task") or "")
    method = str((recommended.get("model") or {}).get("name") or "")
    dataset = str((recommended.get("data") or {}).get("name") or "")

    metric, value = "", None
    for name, val in (metrics or {}).items():
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            metric, value = str(name)[:60], float(val)
            break
    if not metric and recommended.get("metrics"):
        metric = str(recommended["metrics"][0])[:60]

    record_id = prior_store.record_experiment(
        session_id=session_id, run_id=run_id, candidate_id=cid, task=task,
        method=method, dataset=dataset, metric=metric, value=value,
        cost=cost, duration_hours=duration_hours, config=recommended,
        source="feedback", notes=notes,
    )
    result: dict = {
        "ok": record_id is not None, "record_id": record_id, "candidate_id": cid,
        "task": task, "method": method, "dataset": dataset,
        "metric": metric, "value": value,
        "kg": {"ok": False, "degraded": False, "written": 0, "error": ""},
    }
    self_audit = {
        "run_id": run_id, "candidate_id": cid, "metric": metric,
        "value": value, "record_id": record_id,
    }
    try:
        from src.observability.audit_store import record_audit

        record_audit(session_id, "experiment_designer", "design_feedback", self_audit)
    except Exception:
        logger.debug("feedback audit degraded", exc_info=True)

    kg_cfg = get_settings().kg
    if not kg_cfg.build_enabled:
        result["kg"].update({"degraded": True, "error": "kg.build_enabled=false"})
        return result

    entity_types = set(kg_cfg.entity_types)
    relation_types = set(kg_cfg.relation_types)
    exp_id = f"exp:{session_id}:{run_id}:{cid or 'rec'}"
    entities = [{"entity_id": exp_id, "name": f"实验 {run_id}/{cid or 'rec'}", "type": "Experiment"}]
    edges: list[dict] = []
    evidence_text = "；".join(f"{k}={v}" for k, v in (metrics or {}).items())[:300] or notes[:300]
    if method and "Method" in entity_types and "ACHIEVES" in relation_types:
        entities.append({"entity_id": make_entity_id(method), "name": method, "type": "Method"})
        edges.append({
            "source_id": exp_id, "target_id": make_entity_id(method), "type": "ACHIEVES",
            "properties": {"evidence": evidence_text, "run_id": run_id, "value": value},
        })
    if metric and "Metric" in entity_types and "ACHIEVES" in relation_types:
        entities.append({"entity_id": make_entity_id(metric), "name": metric, "type": "Metric"})
        edges.append({
            "source_id": exp_id, "target_id": make_entity_id(metric), "type": "ACHIEVES",
            "properties": {"evidence": evidence_text, "run_id": run_id, "value": value},
        })
    if dataset and "Dataset" in entity_types and "USES_DATASET" in relation_types:
        entities.append({"entity_id": make_entity_id(dataset), "name": dataset, "type": "Dataset"})
        edges.append({
            "source_id": exp_id, "target_id": make_entity_id(dataset), "type": "USES_DATASET",
            "properties": {"evidence": evidence_text, "run_id": run_id},
        })

    try:
        store = await get_graph_store()
        written = await store.upsert_entities(entities, session_id=session_id)
        if edges:
            written += await store.upsert_edges(edges, session_id=session_id)
        result["kg"].update({"ok": True, "written": written})
    except Exception as exc:
        result["kg"].update({"degraded": True, "error": str(exc)[:200]})
        logger.warning("design feedback: KG 写入降级: %s", exc)
        try:
            from src.observability.audit_store import record_audit

            record_audit(session_id, "experiment_designer", "kg_feedback_degraded",
                         {"run_id": run_id, "error": str(exc)[:200]})
        except Exception:
            logger.debug("feedback degrade audit failed", exc_info=True)
    return result
