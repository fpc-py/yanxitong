"""规格③ 内置知识库：种子切块 / 幂等装载 / 向量召回 / 关键词回退。

4 个库（绘图模板/统计方法/期刊规范/学科教材）写入独立命名空间
``kb:lib:{name}``，与用户 team/personal 双区互不可见（不污染 list_files）。
"""

import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.knowledge import kb as kb_mod
from src.knowledge import knowledge_libs as kl
from src.knowledge.vector_store import VectorStore

REPO_ROOT = Path(__file__).resolve().parents[2]

_SEEDS = {
    "plotting_templates.md": (
        "# 绘图模板库\n\n## 分组柱状图（误差线）\n\n"
        "适合组间均值比较：柱高为均值，误差线用 ±SE，显著性用星号标注。\n\n"
        "## 箱线图（离群点标注）\n\n箱体为 IQR，须标注离群点，色盲安全配色。\n"
    ),
    "statistical_methods.md": (
        "# 统计方法库\n\n## 独立样本 t 检验\n\n"
        "前提：独立、近正态、方差齐性；方差齐性不满足用 Welch 校正。\n\n"
        "## 效应量（Cohen's d）\n\n报告 d 值并说明小/中/大效应区间。\n"
    ),
    "journal_specs.md": (
        "# 期刊规范库\n\n## Nature 制图规范\n\n"
        "单栏 89mm，字号不小于 7pt，位图不低于 300dpi。\n\n"
        "## 色盲安全配色\n\n使用 Okabe-Ito 调色板，避免红绿对比。\n"
    ),
    "textbook_notes.md": (
        "# 学科教材库\n\n## 标准差与标准误\n\n"
        "SD 描述离散程度，SE = SD/√n 描述均值稳定性。\n\n"
        "## IQR 与异常值判定\n\nQ1-1.5IQR 与 Q3+1.5IQR 之外视为可疑异常值。\n"
    ),
}

_RECALL_BLOCKS = (
    "pandas describe 描述统计；matplotlib 直方图与箱线图；scipy.stats 检验；"
    "线性回归与相关系数；样本量 n 与置信区间"
)


_RECALL_HITS_MIN = 1


@pytest.fixture()
def lib_env(tmp_path, monkeypatch, fake_encoder_cls):
    store = VectorStore(dim=8, encoder=fake_encoder_cls())
    monkeypatch.setattr(kb_mod, "_kb_store", store)
    monkeypatch.setattr(kb_mod, "KB_DIR", str(tmp_path / "kb"))

    seed_dir = tmp_path / "knowledge_libs"
    seed_dir.mkdir()
    for name, text in _SEEDS.items():
        (seed_dir / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(kl, "SEED_DIR", str(seed_dir))
    monkeypatch.setattr(kl, "_seeded", False)
    monkeypatch.setattr(kl, "_seed_failed", False)
    return kl


# ---- 切块 ------------------------------------------------------------------


def test_split_sections_ignores_headings_in_code_fence():
    text = (
        "# 标题\n\n前言\n\n## 第一节\n\n正文一\n\n"
        "```python\n## 这是代码注释不是标题\nprint(1)\n```\n\n## 第二节\n\n正文二\n"
    )
    sections = kl._split_sections(text)
    titles = [s["section_title"] for s in sections]
    assert titles == ["总览", "第一节", "第二节"]
    assert "这是代码注释" not in titles
    assert "print(1)" in next(s["text"] for s in sections if s["section_title"] == "第一节")


def test_lib_scopes_configuration():
    assert kl.LIB_SCOPES == ["kb:lib:plotting", "kb:lib:methods", "kb:lib:journal", "kb:lib:textbook"]
    assert kl.lib_scope("methods") == "kb:lib:methods"
    assert set(kl.LIB_FILES) == set(kl.LIB_LABELS)


def test_real_seed_files_exist_and_parse():
    """仓库内真实种子文件（演示数据）必须存在且可切块。"""
    for library in kl.LIB_LABELS:
        path = REPO_ROOT / "data" / "knowledge_libs" / kl.LIB_FILES[library]
        assert path.is_file(), f"缺少种子文件: {path}"
        sections = kl._split_sections(path.read_text(encoding="utf-8"))
        assert len(sections) >= 2, f"{path.name} 切块过少"


# ---- 幂等装载 ---------------------------------------------------------------


def test_ensure_seeded_and_idempotent(lib_env):
    added = lib_env.ensure_seeded()
    assert added == 12  # 4 库 × 3 小节（总览 + 2 个 ## 小节）

    # 二次调用（重置进程标志后）依旧零新增：块级 sha256 去重
    lib_env._seeded = False
    assert lib_env.ensure_seeded() == 0
    assert lib_env._seeded is True


def test_lib_chunks_invisible_to_user_libraries(lib_env):
    lib_env.ensure_seeded()
    kb_mod.add_chunks(
        "user_a.pdf",
        [{"text": "用户上传的论文内容 " * 10, "section_title": "Body", "chunk_index": 0}],
        "personal", "user:1", "uh1",
    )
    files = kb_mod.list_files(owner="user:1")
    assert {f["filename"] for f in files} == {"user_a.pdf"}

    # 用户区检索不会混入内置库内容
    hits = kb_mod.query_chunks("箱线图", scopes=["kb:user:1"], top_k=5)
    assert all(h["scope"] == "kb:user:1" for h in hits)


# ---- 召回 ------------------------------------------------------------------


def test_recall_vector_channel(lib_env):
    result = lib_env.recall("比较两组差异并画出带误差线的柱状图")
    assert result["degraded"] is False
    assert result["sources"] > 0
    assert set(result["libraries"]) == set(kl.LIB_LABELS)
    total = sum(len(v["chunks"]) for v in result["libraries"].values())
    assert total == result["sources"]
    for lib, info in result["libraries"].items():
        for chunk in info["chunks"]:
            assert chunk["source"].startswith(info["label"])
            assert len(chunk["text"]) <= 900
            assert isinstance(chunk["similarity"], float)


def test_recall_keyword_fallback(lib_env, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("encoder unavailable")

    monkeypatch.setattr(lib_env.kb, "query_chunks", _boom)
    result = lib_env.recall("箱线图 离群点标注")
    assert result["degraded"] is True

    plotting = result["libraries"]["plotting"]["chunks"]
    assert plotting, "关键词回退也应有命中"
    assert any("箱线图" in chunk["text"] for chunk in plotting)


def test_keyword_fallback_zero_overlap_takes_first_sections(lib_env):
    libraries = lib_env._keyword_libraries("zzz qqq 无关词", top_k=3)
    for lib in kl.LIB_LABELS:
        chunks = libraries[lib]["chunks"]
        assert len(chunks) == 2  # 零命中 → 前 2 节（总览 + 第一个 ## 小节）
        assert chunks[0]["section"] == "总览"
        assert all(chunk["text"] for chunk in chunks)


def test_augment_query_includes_profile_columns(lib_env):
    query = lib_env._augment_query(
        "分析",
        {"format": "csv/utf-8", "columns": [{"name": "身高", "dtype": "float64"},
                                             {"name": "组别", "dtype": "object"}]},
    )
    assert "csv/utf-8" in query
    assert "身高" in query and "组别" in query
    assert "float64" in query


def test_seed_content_hash_present_in_documents(lib_env):
    lib_env.ensure_seeded()
    docs = list(lib_env.kb.get_kb_store()._documents.values())
    lib_docs = [d for d in docs if d["scope"].startswith("kb:lib:")]
    assert lib_docs
    expected = hashlib.sha256(_SEEDS["plotting_templates.md"].encode("utf-8")).hexdigest()
    plotting = [d for d in lib_docs if d["scope"] == "kb:lib:plotting"]
    assert all(d["content_hash"] == expected for d in plotting)
    assert all(d["owner"] == "system" for d in lib_docs)
