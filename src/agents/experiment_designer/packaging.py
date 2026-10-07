"""规格⑨⑫产出打包：design run 目录装配 / stages.json / manifest / zip / 保留清理。

产物落盘结构（每次设计一个不可变 run 目录）::

    data/design/{session_id}/{run_id}/
        manifest.json        # 运行元数据 + 文件清单 + 阶段摘要
        stages.json          # 10 段编排：parse→retrieve→diagnose→build→generate→
                             #   optimize→rank→plan→validate→package（每段 ms/ok/degraded）
        report.md            # 结构化报告（即 answer）
        recommended.json     # 推荐配置（含 _meta 估计声明）
        candidates/*.json    # 全部候选方案（含 evidence_refs）
        evidence.json        # EvidenceSet（含锚点原文）
        validation.json      # 可行性 + 验证计划 + 幻觉校验
        code/train.py        # coder 生成的训练脚本（静态校验通过的可解析版本）

每会话只保留最近 ``settings.designer.max_runs`` 次 run（run_id 以时间戳开头，
字典序即新旧序）；清理 run 目录时同步删除对应 zip，zip 供前端一次下载。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import get_settings

logger = logging.getLogger(__name__)

DESIGN_DIR = "data/design"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(token: str) -> str:
    return _UNSAFE.sub("_", str(token or ""))[:80] or "unknown"


def session_dir(session_id: str) -> Path:
    return Path(DESIGN_DIR) / _safe(session_id)


def new_run_dir(session_id: str) -> tuple[str, Path]:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    path = session_dir(session_id) / run_id
    path.mkdir(parents=True, exist_ok=True)
    return run_id, path


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def assemble_run(
    run_dir: Path,
    *,
    session_id: str,
    run_id: str,
    intent: str,
    stages: list[dict],
    candidates: list[dict],
    recommended: dict,
    code: str,
    evidence: dict,
    validation: dict,
    report_md: str,
    manifest_extra: dict | None = None,
) -> dict:
    """装配 run 目录全部文件并返回 manifest（同时负责旧 run 清理）。"""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "stages.json").write_text(
        json.dumps(stages or [], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(report_md or "", encoding="utf-8")
    _dump(run_dir / "recommended.json", recommended or {})
    _dump(run_dir / "evidence.json", evidence or {})
    _dump(run_dir / "validation.json", validation or {})

    candidates_dir = run_dir / "candidates"
    for index, candidate in enumerate(candidates or []):
        name = _safe(candidate.get("id") or f"c{index + 1}")
        _dump(candidates_dir / f"{name}.json", candidate)
    (run_dir / "code").mkdir(parents=True, exist_ok=True)
    (run_dir / "code" / "train.py").write_text(code or "", encoding="utf-8")

    files = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_file() and path.suffix != ".zip":
            files.append({
                "name": path.relative_to(run_dir).as_posix(),
                "size": path.stat().st_size,
            })

    manifest = {
        "run_id": run_id,
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "intent": intent,
        "candidates": len(candidates or []),
        "stages": [
            {"stage": s.get("stage"), "ok": s.get("ok"), "ms": s.get("ms"),
             "degraded": bool(s.get("degraded"))}
            for s in (stages or [])
        ],
        "recommended": {
            "candidate_id": (recommended or {}).get("_meta", {}).get("candidate_id", ""),
            "title": (recommended or {}).get("_meta", {}).get("title", ""),
            "route": (recommended or {}).get("_meta", {}).get("route", ""),
        },
        "files": files,
        **(manifest_extra or {}),
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    prune_runs(session_id)
    return manifest


def prune_runs(session_id: str) -> int:
    """只保留最近 max_runs 次 run；删除旧 run 目录时同步清理对应 zip。"""
    keep = max(1, get_settings().designer.max_runs)
    base = session_dir(session_id)
    if not base.is_dir():
        return 0
    runs = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)
    removed = 0
    for old in runs[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
        (base / f"{old.name}.zip").unlink(missing_ok=True)
        removed += 1
    return removed


def list_runs(session_id: str) -> list[dict]:
    base = session_dir(session_id)
    if not base.is_dir():
        return []
    runs = []
    for path in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            runs.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return runs


def read_manifest(session_id: str, run_id: str) -> dict | None:
    path = session_dir(session_id) / _safe(run_id) / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def find_file(session_id: str, run_id: str, name: str) -> Path | None:
    """定位 run 内文件（支持 code/train.py 等相对路径）；拒绝穿越到 run 目录外。"""
    if not name or ".." in name or name.startswith("/") or "\\" in name:
        return None
    base = (session_dir(session_id) / _safe(run_id)).resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return None
    return target


def zip_package(session_id: str, run_id: str) -> tuple[Path | None, str]:
    base = session_dir(session_id) / _safe(run_id)
    if not base.is_dir():
        return None, ""
    zip_path = session_dir(session_id) / f"{_safe(run_id)}.zip"
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(base).as_posix())
    except OSError as exc:
        logger.warning("design zip_package degraded: %s", exc)
        return None, ""
    return zip_path, f"design_{_safe(run_id)}.zip"
