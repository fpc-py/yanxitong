"""规格⑨ 产出打包：run 目录装配 / 手写 Notebook / manifest / 清理 / zip / 路径守卫。"""

import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.agents.data_analyst import packaging


@pytest.fixture()
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _make_run(tmp_path, session="sess1"):
    run_id, run_dir = packaging.new_run_dir(session)
    return run_id, run_dir


# ---- run 目录 ----------------------------------------------------------------


def test_new_run_dir_naming_and_creation(workdir):
    run_id, run_dir = packaging.new_run_dir("sess1")
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", run_id)
    assert run_dir.is_dir()
    assert run_dir.resolve() == (workdir / "data" / "analysis" / "sess1" / run_id).resolve()


def test_session_id_sanitized(workdir):
    run_id, run_dir = packaging.new_run_dir("../../evil sess!?")
    resolved = run_dir.resolve()
    base = (workdir / "data" / "analysis").resolve()
    assert resolved.is_relative_to(base)
    assert all(part not in ("..", ".") for part in resolved.relative_to(base).parts)


def test_prune_runs_keeps_latest(workdir, monkeypatch):
    monkeypatch.setattr(packaging, "get_settings",
                        lambda: SimpleNamespace(analysis=SimpleNamespace(max_runs=3)))
    base = workdir / "data" / "analysis" / "sess1"
    for i in range(5):
        (base / f"2026010{i}-000000-aaaaaa").mkdir(parents=True)
    # 下载缓存 zip 应随对应 run 一起被清理，不残留孤儿文件
    (base / "20260100-000000-aaaaaa.zip").write_bytes(b"PK")
    (base / "20260104-000000-aaaaaa.zip").write_bytes(b"PK")
    removed = packaging.prune_runs("sess1")
    assert removed == 2
    remaining = sorted(p.name for p in base.iterdir())
    assert remaining == [
        "20260102-000000-aaaaaa", "20260103-000000-aaaaaa",
        "20260104-000000-aaaaaa", "20260104-000000-aaaaaa.zip",
    ]


# ---- Notebook ----------------------------------------------------------------


def test_build_notebook_structure():
    nb = packaging.build_notebook("df = load_data()\nprint(df.shape)", "## 发现\n身高均值 172", "比较两组身高")
    assert nb["nbformat"] == 4 and nb["nbformat_minor"] == 5
    assert nb["metadata"]["kernelspec"]["name"] == "python3"
    assert [c["cell_type"] for c in nb["cells"]] == ["markdown", "code", "markdown"]
    assert "比较两组身高" in "".join(nb["cells"][0]["source"])
    code_cell = nb["cells"][1]
    assert code_cell["execution_count"] is None
    assert code_cell["outputs"] == []
    assert "load_data()" in "".join(code_cell["source"])
    assert "身高均值 172" in "".join(nb["cells"][2]["source"])
    # 每一行都带换行尾（nbformat 约定），可被 json.loads / jupyter 解析
    for cell in nb["cells"]:
        for line in cell["source"][:-1]:
            assert line.endswith("\n")


def test_build_notebook_without_report():
    nb = packaging.build_notebook("print(1)", "", "意图")
    assert [c["cell_type"] for c in nb["cells"]] == ["markdown", "code"]


# ---- assemble_run ------------------------------------------------------------


def test_assemble_run_moves_figures_and_writes_files(workdir, monkeypatch):
    monkeypatch.setattr(packaging, "get_settings",
                        lambda: SimpleNamespace(analysis=SimpleNamespace(max_runs=5)))
    run_id, run_dir = _make_run(workdir)
    (run_dir / "身高分布.png").write_bytes(b"png-bytes")
    (run_dir / "pub").mkdir()
    (run_dir / "pub" / "身高分布_300dpi.svg").write_text("<svg/>", encoding="utf-8")
    (run_dir / "cleaned.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    manifest = packaging.assemble_run(
        run_dir,
        session_id="sess1",
        run_id=run_id,
        intent="比较两组身高",
        manifest_extra={"degraded": False, "attempts": 1},
        report_md="## 主要发现\n均值 172",
        code="df = load_data()",
        findings={"summary": "均值 172"},
        env_lock="pandas==2.2.0\n",
    )

    assert not (run_dir / "身高分布.png").exists()
    assert (run_dir / "figures" / "身高分布.png").is_file()
    assert manifest["figures_moved"] == 1
    assert manifest["degraded"] is False and manifest["attempts"] == 1

    names = {f["name"] for f in manifest["files"]}
    # manifest.json 自身在文件清单生成之后写入，因此不在清单里（其余产物都在）
    assert {"analysis.ipynb", "findings.json", "report.md",
            "environment.lock", "figures/身高分布.png", "pub/身高分布_300dpi.svg",
            "cleaned.csv"} <= names
    assert "manifest.json" not in names
    assert all(f["size"] >= 0 for f in manifest["files"])

    on_disk = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["run_id"] == run_id and on_disk["intent"] == "比较两组身高"
    assert json.loads((run_dir / "findings.json").read_text(encoding="utf-8")) == {"summary": "均值 172"}
    nb = json.loads((run_dir / "analysis.ipynb").read_text(encoding="utf-8"))
    assert nb["cells"][1]["source"] == ["df = load_data()"]
    assert (run_dir / "environment.lock").read_text(encoding="utf-8") == "pandas==2.2.0\n"


def test_list_runs_newest_first(workdir, monkeypatch):
    monkeypatch.setattr(packaging, "get_settings",
                        lambda: SimpleNamespace(analysis=SimpleNamespace(max_runs=5)))
    for stamp in ("20260101-000000-aaaaaa", "20260102-000000-bbbbbb"):
        run_dir = workdir / "data" / "analysis" / "sess1" / stamp
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": stamp}), encoding="utf-8")
    runs = packaging.list_runs("sess1")
    assert [r["run_id"] for r in runs] == ["20260102-000000-bbbbbb", "20260101-000000-aaaaaa"]
    assert packaging.list_runs("no_such") == []


def test_read_manifest(workdir):
    run_id, run_dir = _make_run(workdir)
    assert packaging.read_manifest("sess1", run_id) is None
    (run_dir / "manifest.json").write_text("{not json", encoding="utf-8")
    assert packaging.read_manifest("sess1", run_id) is None
    (run_dir / "manifest.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    assert packaging.read_manifest("sess1", run_id)["run_id"] == run_id


# ---- find_file 路径守卫 -------------------------------------------------------


def test_find_file_happy_and_nested(workdir):
    run_id, run_dir = _make_run(workdir)
    (run_dir / "figures").mkdir()
    (run_dir / "figures" / "a.png").write_bytes(b"x")
    assert packaging.find_file("sess1", run_id, "figures/a.png") == (run_dir / "figures" / "a.png").resolve()


@pytest.mark.parametrize("name", [
    "",
    "..",
    "../manifest.json",
    "figures/../../manifest.json",
    "/etc/passwd",
    "..\\..\\secret.txt",
    "a\\b",
])
def test_find_file_rejects_traversal(workdir, name):
    run_id, run_dir = _make_run(workdir)
    (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
    assert packaging.find_file("sess1", run_id, name) is None


def test_find_file_missing_returns_none(workdir):
    run_id, _ = _make_run(workdir)
    assert packaging.find_file("sess1", run_id, "nope.txt") is None


# ---- zip ---------------------------------------------------------------------


def test_zip_package(workdir):
    run_id, run_dir = _make_run(workdir)
    (run_dir / "findings.json").write_text("{}", encoding="utf-8")
    (run_dir / "figures").mkdir()
    (run_dir / "figures" / "a.png").write_bytes(b"png")

    zip_path, download_name = packaging.zip_package("sess1", run_id)
    assert zip_path is not None and zip_path.is_file()
    assert download_name == f"analysis_{run_id}.zip"

    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        assert {"findings.json", "figures/a.png"} <= set(zf.namelist())
    # zip 本身不进入 manifest 文件清单（assemble 时过滤 .zip）
    assert packaging.zip_package("sess1", "no-such-run") == (None, "")
