"""规格⑨ 产出打包：run 目录装配 / Notebook / manifest / 环境锁 / 保留清理 / zip。

产物落盘结构（每次分析一个不可变 run 目录）::

    data/analysis/{session_id}/{run_id}/
        manifest.json        # 运行元数据 + 文件清单 + 阶段耗时 + 知识来源
        report.md            # 解释报告（即 answer）
        analysis.ipynb       # 零依赖手写 nbformat 4（后端 venv 无 nbformat）
        findings.json        # 结构化发现（校验的对象）
        cleaned.csv          # 脚本清洗后数据（如产生）
        environment.lock     # 沙箱 pip freeze（失败回退静态快照）
        figures/*.png        # 150dpi 展示图（与前端 data URL 同源）
        pub/*               # 300dpi PNG / SVG / PDF 出版资产

每会话只保留最近 ``settings.analysis.max_runs`` 次 run（按 run_id 前缀时间戳
排序清理）；zip 打包供前端一次下载。
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

ANALYSIS_DIR = "data/analysis"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe(token: str) -> str:
    return _UNSAFE.sub("_", str(token or ""))[:80] or "unknown"


def session_dir(session_id: str) -> Path:
    return Path(ANALYSIS_DIR) / _safe(session_id)


def new_run_dir(session_id: str) -> tuple[str, Path]:
    """创建新 run 目录，返回 ``(run_id, path)``。"""
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    path = session_dir(session_id) / run_id
    path.mkdir(parents=True, exist_ok=True)
    return run_id, path


def _lines(text: str) -> list[str]:
    if not text:
        return []
    return text.splitlines(keepends=True)


def build_notebook(code: str, report_md: str, intent: str) -> dict:
    """手写 nbformat 4 Notebook JSON（不依赖 nbformat 库）。"""
    header = (
        f"# 数据分析 Notebook\n\n"
        f"**分析意图**：{intent}\n\n"
        f"由研析通数据分析智能体生成。下方代码单元格为最终执行版本；\n"
        f"运行前请将 `load_data()` 的数据路径替换为本地文件。\n"
    )
    cells = [
        {"cell_type": "markdown", "metadata": {}, "source": _lines(header)},
        {
            "cell_type": "code",
            "execution_count": None,
            "metadata": {},
            "outputs": [],
            "source": _lines(code or "# 无代码"),
        },
    ]
    if report_md:
        cells.append({"cell_type": "markdown", "metadata": {}, "source": _lines(report_md)})
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.12"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def assemble_run(
    run_dir: Path,
    *,
    session_id: str,
    run_id: str,
    intent: str,
    manifest_extra: dict,
    report_md: str,
    code: str,
    findings: dict | None,
    env_lock: str,
) -> dict:
    """装配 run 目录全部文件并返回 manifest（同时负责旧 run 清理）。"""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    figures_dir = run_dir / "figures"
    moved = 0
    for png in sorted(run_dir.glob("*.png")):
        figures_dir.mkdir(exist_ok=True)
        try:
            shutil.move(str(png), str(figures_dir / png.name))
            moved += 1
        except OSError:
            continue

    (run_dir / "findings.json").write_text(
        json.dumps(findings or {}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(report_md or "", encoding="utf-8")
    (run_dir / "environment.lock").write_text(env_lock or "", encoding="utf-8")
    (run_dir / "analysis.ipynb").write_text(
        json.dumps(build_notebook(code, report_md, intent), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

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
        "figures_moved": moved,
        "files": files,
        **manifest_extra,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    prune_runs(session_id)
    return manifest


def prune_runs(session_id: str) -> int:
    """只保留最近 max_runs 次 run（run_id 以时间戳开头，字典序即新旧序）。"""
    keep = max(1, get_settings().analysis.max_runs)
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
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        runs.append(manifest)
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
    """定位 run 内文件（支持 figures/x.png 等相对路径）；拒绝穿越到 run 目录外。"""
    if not name or ".." in name or name.startswith("/") or "\\" in name:
        return None
    base = (session_dir(session_id) / _safe(run_id)).resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return None
    return target


def zip_package(session_id: str, run_id: str) -> tuple[Path | None, str]:
    """把 run 目录打包为 zip（会话目录下 ``{run_id}.zip``），返回 (路径, 下载文件名)。"""
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
        logger.warning("zip_package degraded: %s", exc)
        return None, ""
    return zip_path, f"analysis_{_safe(run_id)}.zip"
