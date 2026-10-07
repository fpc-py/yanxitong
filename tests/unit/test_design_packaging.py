"""规格⑨⑫产出打包单元测试：run 目录装配 / manifest 契约 / 路径穿越守卫 / zip / 保留清理。

全部落盘到 tmp_path（monkeypatch ``packaging.DESIGN_DIR``）；max_runs 用桩配置
（不需要真实 config.yaml 参与）。路径守卫是本文件的重点：``find_file`` 是 API
层文件下载的唯一入口，任何 ``..`` / 绝对路径 / 反斜杠都必须被拒绝。
"""

from __future__ import annotations

import zipfile
from types import SimpleNamespace

import pytest

from src.agents.experiment_designer import packaging


@pytest.fixture()
def design_root(tmp_path, monkeypatch):
    root = tmp_path / "design"
    monkeypatch.setattr(packaging, "DESIGN_DIR", str(root))
    monkeypatch.setattr(
        packaging, "get_settings",
        lambda: SimpleNamespace(designer=SimpleNamespace(max_runs=3)),
    )
    return root


def _make_run(session="s1", **over):
    run_id, run_dir = packaging.new_run_dir(session)
    kwargs = dict(
        session_id=session, run_id=run_id, intent="升级模型",
        stages=[
            {"stage": "parse", "ok": True, "ms": 3},
            {"stage": "package", "ok": True, "ms": 5, "degraded": False},
        ],
        candidates=[{"id": "c1", "route": "structure", "title": "升级"}],
        recommended={"_meta": {"candidate_id": "c1", "title": "升级", "route": "structure"}},
        code="print('hi')", evidence={"anchors": []}, validation={"overall": "pass"},
        report_md="# 报告",
    )
    kwargs.update(over)
    manifest = packaging.assemble_run(run_dir, **kwargs)
    return run_id, run_dir, manifest


def test_assemble_run_layout_and_manifest(design_root):
    run_id, run_dir, manifest = _make_run()

    assert manifest["run_id"] == run_id and manifest["session_id"] == "s1"
    assert manifest["intent"] == "升级模型" and manifest["candidates"] == 1
    assert manifest["recommended"] == {"candidate_id": "c1", "title": "升级", "route": "structure"}
    assert [s["stage"] for s in manifest["stages"]] == ["parse", "package"]
    assert manifest["stages"][1]["degraded"] is False

    names = {f["name"] for f in manifest["files"]}
    assert {"stages.json", "report.md", "recommended.json", "evidence.json",
            "validation.json", "candidates/c1.json", "code/train.py"} <= names
    assert all(f["size"] >= 0 for f in manifest["files"])
    # manifest 自身在文件清单生成之后才落盘，故不在清单内（zip 时由 rglob 收录）
    assert "manifest.json" not in names

    assert (run_dir / "code" / "train.py").read_text(encoding="utf-8") == "print('hi')"
    assert (run_dir / "report.md").read_text(encoding="utf-8") == "# 报告"


def test_read_manifest_and_list_runs_roundtrip(design_root):
    first_id, _, _ = _make_run()
    second_id, _, _ = _make_run()

    listed = packaging.list_runs("s1")
    assert [m["run_id"] for m in listed] == sorted([first_id, second_id], reverse=True)
    assert packaging.read_manifest("s1", second_id)["run_id"] == second_id
    assert packaging.read_manifest("s1", "20260101-000000-nope") is None
    assert packaging.list_runs("ghost-session") == []


def test_find_file_path_guards(design_root):
    run_id, _, _ = _make_run()

    assert packaging.find_file("s1", run_id, "code/train.py").name == "train.py"
    assert packaging.find_file("s1", run_id, "manifest.json").name == "manifest.json"

    for bad in ("../manifest.json", "..\\manifest.json", "/etc/passwd",
                "code/../../x", "C:\\windows\\system32", ""):
        assert packaging.find_file("s1", run_id, bad) is None, bad
    assert packaging.find_file("s1", run_id, "ghost.txt") is None
    assert packaging.find_file("s1", "nope-run", "manifest.json") is None


def test_zip_package_contents(design_root):
    run_id, _, _ = _make_run()

    zip_path, download_name = packaging.zip_package("s1", run_id)
    assert zip_path is not None and download_name == f"design_{run_id}.zip"
    assert zip_path.name == f"{run_id}.zip"
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert {"manifest.json", "stages.json", "report.md",
            "candidates/c1.json", "code/train.py"} <= names


def test_zip_package_missing_run(design_root):
    assert packaging.zip_package("s1", "20260101-000000-nope") == (None, "")


def test_prune_runs_keeps_latest_and_removes_old_zip(design_root):
    base = packaging.session_dir("s1")
    names = [f"20260101-00000{i}-aaaaaa" for i in range(6)]
    for name in names:
        (base / name).mkdir(parents=True)
        (base / name / "manifest.json").write_text("{}", encoding="utf-8")
    (base / f"{names[0]}.zip").write_text("zip", encoding="utf-8")
    (base / f"{names[5]}.zip").write_text("zip", encoding="utf-8")

    removed = packaging.prune_runs("s1")

    assert removed == 3
    assert sorted(p.name for p in base.iterdir() if p.is_dir()) == names[3:]
    assert not (base / f"{names[0]}.zip").exists()  # 旧 run 的 zip 同步清理
    assert (base / f"{names[5]}.zip").exists()      # 保留 run 的 zip 不动


def test_session_dir_sanitises_traversal(design_root):
    path = packaging.session_dir("../../evil")
    assert path.parent == design_root  # 穿越序列被压成单段目录名，不出根目录
    assert path.name == ".._.._evil"
