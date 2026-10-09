"""规格① 数据接入与画像：在沙箱内做格式识别 + Schema 推断 + 数据质量报告。

后端 venv 未安装 pandas，画像必须跑在沙箱里。脚本经 stdout 的
``__YXT_PROFILE__`` sentinel 行回传结构化 JSON；解析失败/沙箱不可用时返回
``{"ok": False, "degraded": True, ...}``，分析与上传流程继续。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from src.core.config import get_settings
from src.tools.sandbox import _sanitize_mount_name, get_sandbox

logger = logging.getLogger(__name__)

SENTINEL = "__YXT_PROFILE__"

_PROFILE_SCRIPT_TEMPLATE = r'''# --- 数据画像脚本（平台生成，无头运行） ---
import json, os, sys, traceback, warnings
warnings.filterwarnings("ignore")
SENTINEL = "__YXT_PROFILE__"


def emit(payload):
    print(SENTINEL + json.dumps(payload, ensure_ascii=True, default=str))


def main():
    import pandas as pd

    path = os.environ.get("YXT_MOCK_DATA_FILE") or __YXT_CONTAINER_PATH__
    original = __YXT_ORIGINAL_NAME__
    suffix = os.path.splitext(original)[1].lower()
    errors = []

    state = {"df": None, "fmt": ""}

    def try_reader(label, fn, *args, **kwargs):
        if state["df"] is not None:
            return
        try:
            d = fn(*args, **kwargs)
            if not isinstance(d, pd.DataFrame):
                d = pd.DataFrame(d)
            state["df"] = d
            state["fmt"] = label
        except Exception as exc:
            errors.append("%s: %s" % (label, str(exc)[:160]))

    if suffix in (".csv", ".txt", ""):
        for enc in ("utf-8", "gbk", "latin-1"):
            try_reader("csv/" + enc, pd.read_csv, path, encoding=enc)
    if state["df"] is None and suffix in (".xlsx", ".xls", ".xlsm"):
        try_reader("excel", pd.read_excel, path)
    if state["df"] is None and suffix in (".json", ".jsonl"):
        try_reader("json", pd.read_json, path)
        if state["df"] is None:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh)
                state["df"] = pd.json_normalize(raw)
                state["fmt"] = "json-normalize"
            except Exception as exc:
                errors.append("json-normalize: %s" % str(exc)[:160])
    if state["df"] is None and suffix in (".parquet", ".pq"):
        try_reader("parquet", pd.read_parquet, path)
    if state["df"] is None:
        try_reader("csv/utf-8", pd.read_csv, path, encoding="utf-8")
        try_reader("excel", pd.read_excel, path)
        try_reader("parquet", pd.read_parquet, path)

    df = state["df"]
    if df is None or df.shape[1] == 0:
        emit({
            "ok": False,
            "error": "无法解析数据文件：" + ("；".join(errors[:4]) or "未知格式"),
            "filename": original,
            "format_attempts": errors[:6],
        })
        return

    cols = []
    numeric_cols, cat_cols, dt_cols, issues = [], [], [], []
    n_rows = int(len(df))
    for name in list(df.columns)[:60]:
        s = df[name]
        missing = int(s.isna().sum())
        missing_pct = round(missing * 100.0 / max(n_rows, 1), 2)
        uniq = int(s.nunique(dropna=True))
        entry = {
            "name": str(name),
            "dtype": str(s.dtype),
            "missing": missing,
            "missing_pct": missing_pct,
            "unique": uniq,
        }
        non_null = s.dropna()
        if len(non_null):
            entry["sample"] = str(non_null.iloc[0])[:60]
        if pd.api.types.is_numeric_dtype(s):
            numeric_cols.append(str(name))
            if len(non_null):
                entry["min"] = float(non_null.min())
                entry["max"] = float(non_null.max())
                entry["mean"] = round(float(non_null.mean()), 6)
            if len(non_null) >= 8:
                q1, q3 = non_null.quantile(0.25), non_null.quantile(0.75)
                iqr = q3 - q1
                if iqr > 0:
                    outliers = int(((non_null < q1 - 1.5 * iqr) | (non_null > q3 + 1.5 * iqr)).sum())
                    entry["outliers_iqr"] = outliers
                    if outliers:
                        issues.append({
                            "kind": "outliers", "column": str(name),
                            "detail": "IQR 法检出 %d 个可疑异常值" % outliers,
                        })
        elif pd.api.types.is_datetime64_any_dtype(s):
            dt_cols.append(str(name))
        else:
            head_str = non_null.astype(str).head(50)
            numlike = float(pd.to_numeric(head_str, errors="coerce").notna().mean()) if len(head_str) else 0.0
            if numlike >= 0.8 and uniq > 3:
                issues.append({
                    "kind": "type_conflict", "column": str(name),
                    "detail": "该列绝大多数取值可解析为数值，当前为文本类型，可能存在类型冲突",
                })
            else:
                cat_cols.append(str(name))
        if missing:
            issues.append({
                "kind": "missing", "column": str(name),
                "detail": "缺失 %d 个（%.2f%%）" % (missing, missing_pct),
            })
        if n_rows > 0 and uniq <= 1:
            issues.append({"kind": "constant", "column": str(name), "detail": "常量列（无区分度）"})
        cols.append(entry)

    dup = int(df.duplicated().sum())
    if dup:
        issues.append({"kind": "duplicates", "detail": "整行重复 %d 行" % dup})

    emit({
        "ok": True,
        "filename": original,
        "format": state["fmt"],
        "rows": n_rows,
        "cols": int(df.shape[1]),
        "columns": cols,
        "columns_truncated": int(df.shape[1]) > 60,
        "numeric_columns": numeric_cols,
        "categorical_columns": cat_cols,
        "datetime_columns": dt_cols,
        "quality": {
            "duplicates": dup,
            "duplicate_pct": round(dup * 100.0 / max(n_rows, 1), 2),
            "issue_count": len(issues),
            "issues": issues[:40],
        },
        "head": df.head(3).to_dict(orient="records"),
    })


if __name__ == "__main__":
    try:
        main()
    except Exception:
        emit({"ok": False, "error": traceback.format_exc()[-600:]})
'''


def _build_script(container_path: str, original_name: str) -> str:
    return (
        _PROFILE_SCRIPT_TEMPLATE
        .replace("__YXT_CONTAINER_PATH__", json.dumps(container_path))
        .replace("__YXT_ORIGINAL_NAME__", json.dumps(original_name, ensure_ascii=True))
    )


def parse_profile_output(stdout: str) -> Optional[dict]:
    """从 stdout 中提取 sentinel 行上的画像 JSON；无则返回 None。"""
    for line in (stdout or "").splitlines():
        idx = line.find(SENTINEL)
        if idx < 0:
            continue
        payload = line[idx + len(SENTINEL):].strip()
        try:
            data = json.loads(payload)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None
    return None


async def profile_data_file(path: str, sandbox=None) -> dict:
    """在沙箱中对数据文件做格式识别 / Schema 推断 / 质量报告。

    降级语义：文件缺失、沙箱不可用、脚本失败时返回 ``ok=False, degraded=True``，
    绝不抛出——画像失败不阻塞分析主流程。
    """
    settings = get_settings()
    filename = os.path.basename(path or "")
    degraded = {"ok": False, "degraded": True, "filename": filename, "file_path": path}
    if not path or not os.path.exists(path):
        return {**degraded, "error": "数据文件不存在"}

    try:
        sandbox = sandbox or get_sandbox()
    except Exception as exc:  # SandboxUnavailableError：画像降级，不阻塞主流程
        logger.warning("Sandbox unavailable for profiling: %s", exc)
        return {**degraded, "error": f"沙箱不可用：{exc}"}
    mount_name = _sanitize_mount_name(filename)
    script = _build_script(f"/workspace/{mount_name}", filename)
    try:
        result = await sandbox.execute_with_file(
            script, path, timeout=settings.analysis.profile_timeout, mount_name=mount_name
        )
    except Exception as exc:
        logger.warning("Profiling execution degraded: %s", exc)
        return {**degraded, "error": f"画像执行失败：{exc}"}

    profile = parse_profile_output(result.get("stdout", ""))
    if not profile:
        err = (result.get("stderr") or "").strip()[:300]
        return {**degraded, "error": f"画像脚本未返回结果：{err or '无输出'}"}

    profile.setdefault("filename", filename)
    profile["file_path"] = path
    profile["sandbox"] = type(sandbox).__name__
    return profile
