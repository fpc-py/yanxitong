"""规格⑨：推荐配置合并 + coder 训练脚本 + 沙箱静态校验。

静态校验（诚实边界）：当前环境不实际训练（沙箱镜像无 torch）。检查项为
①语法可解析（``ast.parse`` + ``compile``，等价 py_compile）；②AST import 扫描
对照**沙箱镜像真实包清单**（``sandbox.env_lock()``，Docker 不可用时用内置静态
快照）；结果标注 ``mode="static"``，缺失依赖如实列出供目标训练环境参考。
"""

from __future__ import annotations

import ast
import json
import logging
import re
import sys

logger = logging.getLogger(__name__)

#: import 名 → 包名别名（pip 名与 import 名不一致的常见项）
_IMPORT_ALIASES = {
    "pil": "pillow",
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "yaml": "pyyaml",
    "dateutil": "python-dateutil",
    "mpl_toolkits": "matplotlib",
    "torchvision": "torchvision",
    "fitz": "pymupdf",
}


def _apply_patch(base: dict, patch: dict) -> None:
    for path, value in (patch or {}).items():
        parts = str(path).split(".")
        node = base
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value


def build_recommended_config(config: dict | None, winner: dict) -> dict:
    """把排序第一的方案补丁合并进当前配置，产出可交付的 recommended config JSON。"""
    merged = json.loads(json.dumps(config or {}, default=str))
    _apply_patch(merged, winner.get("config_patch") or {})
    hyper = merged.setdefault("hyperparams", {})
    for key, value in (winner.get("params") or {}).items():
        if isinstance(value, float):
            hyper[key] = round(value, 6)
        else:
            hyper[key] = value
    merged["_meta"] = {
        "candidate_id": winner.get("candidate_id", ""),
        "title": winner.get("title", ""),
        "route": winner.get("route", ""),
        "evidence_refs": winner.get("evidence_refs") or [],
        "score": winner.get("score"),
        "objectives": winner.get("objectives") or {},
        "uncertainty": winner.get("uncertainty") or {},
        "estimates_note": "性能/成本/时间为代理模型估计值（±不确定度），非真实训练结果",
        "execution_note": "配置可用于训练脚本；实际训练需在具备相应 GPU 与依赖的环境执行",
    }
    return merged


def parse_imports(code: str) -> list[str]:
    """顶层 import 模块名（AST 扫描；语法错误抛 SyntaxError）。"""
    tree = ast.parse(code)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return sorted(modules)


def env_packages(env_lock_text: str) -> set[str]:
    """pip freeze 文本 → 规范化包名集合。"""
    packages: set[str] = set()
    for line in (env_lock_text or "").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-", "@", "http")):
            continue
        name = re.split(r"[=<>!;\[\s]", line.split(" @ ")[0], 1)[0].strip().lower()
        if name:
            packages.add(name.replace("_", "-"))
    return packages


def _normalize_import(module: str) -> str:
    lowered = module.lower().replace("_", "-")
    return _IMPORT_ALIASES.get(lowered, lowered)


async def static_check(code: str, sandbox=None) -> dict:
    """语法 + 依赖静态校验。``sandbox`` 可注入（默认取全局沙箱以复用 env_lock 缓存）。"""
    syntax_ok = True
    syntax_error = ""
    imports: list[str] = []
    try:
        ast.parse(code)
        compile(code, "train.py", "exec")
    except SyntaxError as exc:
        syntax_ok = False
        syntax_error = f"{exc.msg} (line {exc.lineno})"
    if syntax_ok:
        imports = parse_imports(code)

    lock_text = ""
    if sandbox is None:
        try:
            from src.tools.sandbox import get_sandbox

            sandbox = get_sandbox()
        except Exception as exc:  # SandboxUnavailableError：依赖清单为空，missing 全列出
            logger.warning("static_check: sandbox unavailable, env packages unknown: %s", exc)
            sandbox = None
    if sandbox is not None:
        try:
            lock_text = await sandbox.env_lock()
        except Exception as exc:
            logger.warning("static_check: env_lock 降级: %s", exc)
            lock_text = ""
    packages = env_packages(lock_text)

    stdlib = set(getattr(sys, "stdlib_module_names", set())) | {"__future__"}
    missing = [
        module for module in imports
        if module not in stdlib and _normalize_import(module) not in packages
    ]
    return {
        "ok": syntax_ok,
        "mode": "static",
        "syntax_ok": syntax_ok,
        "syntax_error": syntax_error,
        "imports": imports,
        "missing": missing,
        "env_packages": len(packages),
        "note": "静态校验（不实际执行训练）；missing 为对照沙箱镜像清单的缺失项，目标训练环境需具备",
    }


async def generate_code(
    intent: str,
    recommended: dict,
    evidence: dict | None,
    llm_call,
) -> str:
    """coder 角色生成 train.py；失败返回空串（调用方降级为仅交付配置 JSON）。"""
    from src.agents.experiment_designer import prompts

    prompt = prompts.CODEGEN_PROMPT.format(
        intent=str(intent or "")[:300],
        config=json.dumps(recommended, ensure_ascii=False, indent=1)[:3000],
        evidence=prompts.format_evidence(evidence, limit=12),
    )
    try:
        resp = await llm_call(prompt)
    except Exception as exc:
        logger.warning("design codegen: coder 调用失败: %s", exc)
        return ""
    return _strip_fences(resp or "")


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()
