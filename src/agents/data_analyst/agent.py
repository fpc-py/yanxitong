"""Data Analyst Agent v3.0 — 9 段流水线数据分析智能体。

① 数据画像 → ②③ RAG 知识召回 + 任务规划 → ④ coder 代码生成 →
⑤ 沙箱安全执行 → ⑥ 自动 Debug（≤3 轮，仍失败静态降级）→ ⑦ 两级结果校验 →
⑧ 解释报告 → ⑨ 产出打包（run 目录 + Notebook + 环境锁）。

结果字典沿用旧契约键（stdout/stderr/exit_code/timed_out/code/figures/findings/
has_data_file），新增 profile / task_plan / knowledge_recall / validation /
report / findings_data / analysis_run 供 API 与前端 Dossier 使用。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from src.agents.base import AgentResult, BaseAgent, parse_llm_json
from src.agents.data_analyst import packaging, prompts
from src.agents.data_analyst import validation as validation_mod
from src.core.config import get_settings
from src.knowledge import knowledge_libs
from src.tools.profiler import profile_data_file
from src.tools.sandbox import _sanitize_mount_name, get_sandbox

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class DataAnalystAgent(BaseAgent):
    name = "data_analyst"
    description = "数据分析智能体 v3.0 - 数据画像/RAG规划/coder代码生成/沙箱执行/自动Debug/结果校验/出版级图表/产出打包"
    model_role = "lightweight"

    async def _execute_impl(self, state: dict) -> AgentResult:
        intent = state.get("user_query", "数据分析") or "数据分析"
        session_id = str(state.get("session_id") or "")
        data_file = state.get("data_file_path", "") or ""
        has_data = bool(data_file and os.path.exists(data_file))
        settings = get_settings()

        stages: list[dict] = []
        stage_mark = time.perf_counter()

        def stage(name: str, ok: bool = True, **extra) -> None:
            nonlocal stage_mark
            now = time.perf_counter()
            entry = {"stage": name, "ok": ok, "ms": round((now - stage_mark) * 1000)}
            entry.update(extra)
            stages.append(entry)
            stage_mark = now
            self._audit(f"analysis_{name}", {"ok": ok, **extra})
            self._stage_audit(session_id, name, {"ok": ok, **extra})

        self._audit("analysis_start", {"intent": intent, "has_data_file": has_data})
        run_id, run_dir = packaging.new_run_dir(session_id or "anon")

        # ① 数据画像（已在 /profile 端点做过则直接复用；文件指纹不符则重新生成）
        profile = state.get("data_profile")
        if not isinstance(profile, dict) or not profile:
            profile = None
        if profile is not None and has_data:
            profiled_file = str(profile.get("file_path") or "")
            if not profiled_file or os.path.abspath(profiled_file) != os.path.abspath(data_file):
                self._audit("profile_stale_discarded", {"profiled": profiled_file, "current": data_file})
                profile = None
        if has_data and profile is None:
            profile = await profile_data_file(data_file)
        profile_degraded = bool(isinstance(profile, dict) and profile.get("degraded"))
        if profile_degraded:
            self._audit("profile_degraded", {"error": str(profile.get("error", ""))[:200]})
        stage("profile", ok=not profile_degraded,
              detail="无数据文件" if not has_data else str((profile or {}).get("format", "")))

        # ②③ 知识召回（独立降级）+ 任务规划（合并为一次 LLM 调用）
        recall = {"query": intent, "libraries": {}, "degraded": True, "sources": 0}
        try:
            recall = knowledge_libs.recall(intent, profile)
        except Exception as exc:
            self._audit("recall_degraded", {"error": str(exc)[:200]})
        stage("recall", ok=not recall.get("degraded"), sources=recall.get("sources", 0))

        plan = await self._plan(intent, profile, recall)
        stage("plan", ok=not plan.get("fallback"), steps=len(plan.get("steps") or []))

        # ④ 代码生成（coder 角色，失败回退默认模型）
        code = await self._generate_code(intent, profile, plan, recall)
        if not code:
            self._audit("codegen_empty", {})
            if not has_data:
                return AgentResult(success=False, error="模型未返回可执行代码，请重试。", confidence=0.0)
        stage("codegen", ok=bool(code), code_len=len(code))

        # ⑤⑥ 沙箱执行 + 自动 Debug（≤3 轮）；仍失败且确有数据 → 静态降级脚本
        sandbox = get_sandbox()
        mount_name = _sanitize_mount_name(Path(data_file).name) if has_data else "data.csv"
        final = None
        attempts = 0
        degraded = False
        degrade_reason = ""
        if code:
            for attempt in range(MAX_ATTEMPTS):
                attempts = attempt + 1
                result = await self._run_code(sandbox, code, data_file if has_data else "", mount_name, run_dir)
                if result["exit_code"] == 0 and not result.get("timed_out"):
                    final = result
                    self._audit("execution_success", {"attempt": attempts})
                    break
                self._audit("execution_failed", {
                    "attempt": attempts, "error": str(result.get("stderr", ""))[:200],
                })
                if attempt < MAX_ATTEMPTS - 1:
                    fixed = await self._debug(intent, code, result.get("stderr", ""))
                    if fixed:
                        code = fixed
                else:
                    final = result

        if final is None or final["exit_code"] != 0 or final.get("timed_out"):
            degraded = True
            degrade_reason = "codegen_empty" if not code else "execution_failed_after_3_attempts"
            self._audit("degraded_start", {"reason": degrade_reason})
            if has_data:
                fallback = await self._run_code(
                    sandbox, prompts.DEGRADED_SCRIPT, data_file, mount_name, run_dir
                )
                if fallback["exit_code"] == 0 and not fallback.get("timed_out"):
                    final = fallback
                    self._audit("degraded_fallback_success", {})
                else:
                    self._audit("degraded_fallback_failed", {
                        "error": str(fallback.get("stderr", ""))[:200],
                    })
                    final = final or fallback
            if final is None:
                return AgentResult(success=False, error="代码生成与执行均失败，请重试。", confidence=0.0)
        stage("execute", ok=final["exit_code"] == 0 and not final.get("timed_out"),
              attempts=attempts, degraded=degraded, exit_code=final["exit_code"])

        stdout_raw = final.get("stdout", "") or ""
        findings_data = self._parse_findings(stdout_raw)
        stdout_clean = self._strip_sentinel(stdout_raw)
        findings_bullets = [
            line.strip() for line in stdout_clean.splitlines()
            if line.strip() and line.strip() != prompts.FINDINGS_SENTINEL
        ][:20]

        # ⑦ 结果验证：确定性校验（必跑）+ LLM 复核（deep_verify 开关）
        deterministic = validation_mod.deterministic_checks(findings_data, profile)
        llm_review = None
        if settings.analysis.deep_verify and not degraded:
            llm_review = await self._llm_validate(intent, profile, findings_data, stdout_clean)
        validation_report = validation_mod.combine(deterministic, llm_review)
        if validation_report["overall"] == "fail":
            self._flag_hallucination(session_id, validation_report)
        stage("validate", ok=validation_report["overall"] != "fail",
              overall=validation_report["overall"],
              issues=deterministic.get("issues", 0))

        # ⑧ 解释报告（LLM 失败 → 空串，answer 端点回退 stdout）
        report_md = ""
        if final["exit_code"] == 0 and not final.get("timed_out"):
            report_md = await self._interpret(
                intent, profile, plan, findings_data, stdout_clean, validation_report, degraded
            )
        stage("interpret", ok=bool(report_md), report_len=len(report_md))

        # ⑨ 产出打包：run 目录装配（manifest/notebook/环境锁/清理旧 run）
        env_lock = ""
        try:
            env_lock = await sandbox.env_lock()
        except Exception as exc:
            self._audit("env_lock_degraded", {"error": str(exc)[:200]})
        manifest = packaging.assemble_run(
            run_dir,
            session_id=session_id or "anon",
            run_id=run_id,
            intent=intent,
            manifest_extra={
                "degraded": degraded,
                "degrade_reason": degrade_reason,
                "attempts": attempts,
                "exit_code": final["exit_code"],
                "stages": stages,
                "knowledge_sources": recall.get("sources", 0),
                "validation_overall": validation_report["overall"],
                "profile_format": (profile or {}).get("format"),
            },
            report_md=report_md,
            code=code,
            findings=findings_data,
            env_lock=env_lock or "",
        )
        stage("package", ok=True, files=len(manifest.get("files", [])))

        report = {
            "intent": intent,
            "stdout": stdout_clean[:5000],
            "stderr": str(final.get("stderr", ""))[:1000],
            "exit_code": final["exit_code"],
            "timed_out": final.get("timed_out", False),
            "code": code,
            "has_data_file": has_data,
            "figures": final.get("figures", []),
            "findings": findings_bullets,
            "findings_data": findings_data,
            "profile": profile,
            "task_plan": plan,
            "knowledge_recall": recall,
            "validation": validation_report,
            "report": report_md,
            "analysis_run": {
                "run_id": run_id,
                "degraded": degraded,
                "degrade_reason": degrade_reason,
                "attempts": attempts,
                "stages": stages,
                "artifacts": final.get("artifacts", []),
                "files": manifest.get("files", []),
                "validation_overall": validation_report["overall"],
            },
        }

        ok = final["exit_code"] == 0 and not final.get("timed_out")
        confidence = 0.9 if ok else 0.3
        if degraded:
            confidence = min(confidence, 0.55)
        if validation_report["overall"] == "fail":
            confidence = min(confidence, 0.4)
        self._audit("analysis_complete", {
            "success": ok, "degraded": degraded,
            "validation": validation_report["overall"], "run_id": run_id,
        })
        return AgentResult(
            success=ok,
            data=report,
            confidence=confidence,
            error=None if ok else "代码执行失败，请查看执行输出与降级说明。",
        )

    # ---- 各阶段实现 ------------------------------------------------------

    async def _plan(self, intent: str, profile: dict | None, recall: dict) -> dict:
        prompt = (
            prompts.PLANNING_PROMPT
            .replace("__INTENT__", intent)
            .replace("__PROFILE_DIGEST__", prompts.profile_digest(profile))
            .replace("__RECALL_DIGEST__", prompts.recall_digest(recall))
        )
        try:
            raw = await self._call_llm(prompt, json_mode=True, enable_thinking=False)
            plan = parse_llm_json(raw)
        except Exception as exc:
            self._audit("plan_failed", {"error": str(exc)[:200]})
            plan = {}
        if not plan.get("steps"):
            plan = prompts.heuristic_plan(intent)
        plan.setdefault("methods", [])
        plan.setdefault("templates", [])
        plan.setdefault("journal_rules", [])
        return plan

    async def _generate_code(self, intent: str, profile: dict | None, plan: dict, recall: dict) -> str:
        prompt = (
            prompts.CODEGEN_PROMPT
            .replace("__INTENT__", intent)
            .replace("__PROFILE_DIGEST__", prompts.profile_digest(profile))
            .replace("__PLAN_JSON__", json.dumps(plan, ensure_ascii=False, indent=1)[:4000])
            .replace("__RECALL_DIGEST__", prompts.recall_digest(recall, max_chunks_per_lib=2, max_chars=350))
        )
        try:
            code = self._clean(await self._call_llm(prompt, enable_thinking=False, role="coder"))
            if code:
                return code
            raise ValueError("模型返回空代码")
        except Exception as exc:
            self._audit("coder_fallback", {"error": str(exc)[:200]})
        try:
            return self._clean(await self._call_llm(prompt, enable_thinking=False))
        except Exception as exc:
            self._audit("codegen_failed", {"error": str(exc)[:200]})
            return ""

    async def _debug(self, intent: str, code: str, error: str) -> str:
        prompt = (
            prompts.DEBUG_PROMPT
            .replace("__INTENT__", intent)
            .replace("__ERROR__", str(error or "")[:1500])
            .replace("__CODE__", code[:6000])
        )
        try:
            fixed = self._clean(await self._call_llm(prompt, enable_thinking=False, role="coder"))
            if fixed:
                return fixed
        except Exception as exc:
            self._audit("debug_coder_fallback", {"error": str(exc)[:200]})
        try:
            return self._clean(await self._call_llm(prompt, enable_thinking=False))
        except Exception as exc:
            self._audit("debug_failed", {"error": str(exc)[:200]})
            return ""

    async def _run_code(
        self,
        sandbox,
        code: str,
        data_file: str,
        mount_name: str,
        run_dir,
    ) -> dict:
        """preamble + 代码在沙箱内执行；产物全量收集到 run 目录。"""
        has_data = bool(data_file)
        preamble = prompts.build_preamble(
            f"/workspace/{mount_name}" if has_data else "",
            Path(data_file).name if has_data else "",
        )
        exec_code = preamble + "\n" + code
        try:
            if has_data:
                return await sandbox.execute_with_file(
                    exec_code, data_file, mount_name=mount_name, collect_to=str(run_dir)
                )
            return await sandbox.execute(exec_code, collect_to=str(run_dir))
        except Exception as exc:
            logger.warning("Sandbox execution raised: %s", exc)
            return {
                "stdout": "", "stderr": str(exc), "exit_code": -1, "timed_out": False,
                "figures": [], "artifacts": [],
            }

    async def _llm_validate(
        self, intent: str, profile: dict | None, findings: dict, stdout: str
    ) -> dict | None:
        prompt = (
            prompts.VALIDATE_PROMPT
            .replace("__INTENT__", intent)
            .replace("__PROFILE_DIGEST__", prompts.profile_digest(profile))
            .replace("__FINDINGS_JSON__", json.dumps(findings or {}, ensure_ascii=False)[:3000])
            .replace("__STDOUT__", stdout[:1500])
        )
        try:
            review = parse_llm_json(await self._call_llm(prompt, json_mode=True, enable_thinking=False))
        except Exception as exc:
            self._audit("llm_validate_failed", {"error": str(exc)[:200]})
            return None
        if not review or "overall" not in review:
            return None
        review.setdefault("assumptions", [])
        review.setdefault("narrative", [])
        review.setdefault("comments", [])
        return review

    async def _interpret(
        self,
        intent: str,
        profile: dict | None,
        plan: dict,
        findings: dict,
        stdout: str,
        validation_report: dict,
        degraded: bool,
    ) -> str:
        summary_lines = [f"总体结论: {validation_report.get('overall', 'unknown')}"]
        for check in (validation_report.get("deterministic") or {}).get("checks", []):
            if check.get("status") != "ok":
                summary_lines.append(f"- [{check['status']}] {check.get('check')}: {check.get('detail', '')}")
        if degraded:
            summary_lines.append("- 本次执行为降级模式（静态兜底脚本），结论有限。")
        prompt = (
            prompts.INTERPRET_PROMPT
            .replace("__INTENT__", intent)
            .replace("__PROFILE_DIGEST__", prompts.profile_digest(profile))
            .replace("__PLAN_STEPS__", prompts.plan_steps_digest(plan))
            .replace("__FINDINGS_JSON__", json.dumps(findings or {}, ensure_ascii=False)[:3000])
            .replace("__STDOUT__", stdout[:2500])
            .replace("__VALIDATION_SUMMARY__", "\n".join(summary_lines))
        )
        try:
            report = (await self._call_llm(prompt, enable_thinking=False)).strip()
        except Exception as exc:
            self._audit("interpret_failed", {"error": str(exc)[:200]})
            return ""
        if report.startswith("```"):
            report = self._clean(report)
        return report

    # ---- 辅助 ------------------------------------------------------------

    def _stage_audit(self, session_id: str, stage_name: str, detail: dict) -> None:
        try:
            from src.observability.audit_store import record_audit

            record_audit(session_id, self.name, f"analysis_{stage_name}", detail)
        except Exception:
            logger.debug("stage audit degraded", exc_info=True)

    def _flag_hallucination(self, session_id: str, validation_report: dict) -> None:
        failed = [
            {"check": c.get("check"), "detail": c.get("detail")}
            for c in (validation_report.get("deterministic") or {}).get("checks", [])
            if c.get("status") == "fail"
        ][:5]
        detail = {
            "overall": validation_report.get("overall"),
            "failed_checks": failed,
            "llm_comments": (validation_report.get("llm") or {}).get("comments", [])[:3],
        }
        self._audit("validation_failed", detail)
        try:
            from src.observability.audit_store import record_hallucination_flag
            from src.observability.metrics import track_hallucination_flag

            record_hallucination_flag(session_id, "result_validation", "high", detail)
            track_hallucination_flag("result_validation", "high")
        except Exception:
            logger.debug("hallucination flag degraded", exc_info=True)

    @staticmethod
    def _parse_findings(stdout: str) -> dict:
        """从 stdout 的 sentinel 行解析结构化发现；无则返回 {}。"""
        for line in (stdout or "").splitlines():
            idx = line.find(prompts.FINDINGS_SENTINEL)
            if idx < 0:
                continue
            payload = line[idx + len(prompts.FINDINGS_SENTINEL):].strip()
            try:
                data = json.loads(payload)
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}
        return {}

    @staticmethod
    def _strip_sentinel(stdout: str) -> str:
        return "\n".join(
            line for line in (stdout or "").splitlines()
            if prompts.FINDINGS_SENTINEL not in line
        )

    @staticmethod
    def _clean(c: str) -> str:
        c = (c or "").strip()
        if c.startswith("```python"):
            c = c[9:]
        elif c.startswith("```"):
            c = c[3:]
        if c.endswith("```"):
            c = c[:-3]
        return c.strip()
