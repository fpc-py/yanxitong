"""规格① 数据画像：sentinel 解析 + 沙箱执行 + 全路径降级。

画像脚本在沙箱（后端 venv 无 pandas）内运行，结果经 stdout 的
``__YXT_PROFILE__`` sentinel 回传；任何一步失败都必须返回 degraded 而非抛异常。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.tools import profiler


def _sentinel_line(payload: dict) -> str:
    return profiler.SENTINEL + json.dumps(payload, ensure_ascii=True)


class FakeSandbox:
    """记录调用参数的假沙箱；outcomes 逐个弹出（用尽后返回失败）。"""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def execute_with_file(self, code, input_file, timeout=None, *, mount_name="data.csv", collect_to=None):
        self.calls.append({
            "code": code, "input_file": input_file, "timeout": timeout,
            "mount_name": mount_name, "collect_to": collect_to,
        })
        if self.outcomes:
            return self.outcomes.pop(0)
        return {"stdout": "", "stderr": "no outcome", "exit_code": -1, "timed_out": False}


class RaisingSandbox:
    async def execute_with_file(self, *args, **kwargs):
        raise RuntimeError("docker daemon unreachable")


def _ok(**payload):
    base = {"stdout": "", "stderr": "", "exit_code": 0, "timed_out": False}
    base.update(payload)
    return base


# ---- parse_profile_output -------------------------------------------------


def test_parse_sentinel_line():
    profile = {"ok": True, "rows": 10, "cols": 2}
    stdout = "noise line\n" + _sentinel_line(profile) + "\ntail"
    assert profiler.parse_profile_output(stdout) == profile


def test_parse_sentinel_with_prefix_text():
    profile = {"ok": True}
    stdout = "2026-10-07 log " + _sentinel_line(profile)
    assert profiler.parse_profile_output(stdout) == profile


def test_parse_missing_or_invalid_returns_none():
    assert profiler.parse_profile_output("") is None
    assert profiler.parse_profile_output("plain text only") is None
    assert profiler.parse_profile_output(profiler.SENTINEL + "{not json") is None
    assert profiler.parse_profile_output(profiler.SENTINEL + "[1,2]") is None


# ---- profile_data_file -----------------------------------------------------


@pytest.mark.asyncio
async def test_profile_happy_path(tmp_path):
    data = tmp_path / "demo_experiment.csv"
    data.write_text("a,b\n1,2\n", encoding="utf-8")
    payload = {
        "ok": True, "format": "csv/utf-8", "rows": 2, "cols": 2,
        "columns": [{"name": "a", "dtype": "int64", "min": 1, "max": 1}],
        "quality": {"duplicates": 0, "issues": []},
    }
    sandbox = FakeSandbox(_ok(stdout="banner\n" + _sentinel_line(payload)))

    result = await profiler.profile_data_file(str(data), sandbox=sandbox)

    assert result["ok"] is True
    assert result["format"] == "csv/utf-8"
    assert result["filename"] == "demo_experiment.csv"
    assert result["file_path"] == str(data)
    assert result["sandbox"] == "FakeSandbox"
    call = sandbox.calls[0]
    assert call["mount_name"] == "demo_experiment.csv"
    assert call["timeout"] == 15
    # 脚本里注入的容器路径与原始文件名
    assert "\"/workspace/demo_experiment.csv\"" in call["code"]
    assert "\"demo_experiment.csv\"" in call["code"]


@pytest.mark.asyncio
async def test_profile_missing_file_degrades(tmp_path):
    result = await profiler.profile_data_file(str(tmp_path / "nope.csv"), sandbox=FakeSandbox())
    assert result["ok"] is False
    assert result["degraded"] is True
    assert "不存在" in result["error"]


@pytest.mark.asyncio
async def test_profile_sandbox_exception_degrades(tmp_path):
    data = tmp_path / "a.csv"
    data.write_text("x\n1\n", encoding="utf-8")
    result = await profiler.profile_data_file(str(data), sandbox=RaisingSandbox())
    assert result["ok"] is False and result["degraded"] is True
    assert "docker daemon unreachable" in result["error"]


@pytest.mark.asyncio
async def test_profile_no_sentinel_degrades_with_stderr(tmp_path):
    data = tmp_path / "a.csv"
    data.write_text("x\n1\n", encoding="utf-8")
    sandbox = FakeSandbox(_ok(stdout="only noise", stderr="ModuleNotFoundError: pandas"))
    result = await profiler.profile_data_file(str(data), sandbox=sandbox)
    assert result["ok"] is False and result["degraded"] is True
    assert "未返回结果" in result["error"]
    assert "pandas" in result["error"]


@pytest.mark.asyncio
async def test_profile_script_failure_payload_passthrough(tmp_path):
    data = tmp_path / "bad.xyz"
    data.write_bytes(b"\x00\x01")
    payload = {"ok": False, "error": "无法解析数据文件：未知格式", "filename": "bad.xyz"}
    sandbox = FakeSandbox(_ok(stdout=_sentinel_line(payload)))
    result = await profiler.profile_data_file(str(data), sandbox=sandbox)
    # 脚本内部失败（ok=False）不是「降级」——脚本本身跑通了，如实返回解析失败
    assert result["ok"] is False
    assert "无法解析" in result["error"]
    assert result.get("degraded") is not True


@pytest.mark.asyncio
async def test_mount_name_sanitized_for_hostile_filename(tmp_path):
    data = tmp_path / "weird name.csv"
    data.write_text("x\n1\n", encoding="utf-8")
    sandbox = FakeSandbox(_ok(stdout=_sentinel_line({"ok": True})))
    await profiler.profile_data_file(str(data), sandbox=sandbox)
    assert sandbox.calls[0]["mount_name"] == "weird_name.csv"
