"""配置加载单元测试（不依赖外部服务）。

覆盖：config.yaml 键名与 pydantic 字段对齐后确实生效（retriever 扁平键、
kg 块、新增 mcp/extraction/pdf/kb 块）；真实环境变量优先于 YAML（含嵌套块）；
嵌套块的优先级为 env > 显式 dict 参数（无 env 时 dict 生效）；缺失 YAML 回退默认值。
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core.config import Settings

# 这些键若在测试机真实环境中存在会干扰断言，统一隔离。
_ENV_KEYS = [
    "APP_PORT",
    "RETRIEVER_TOP_K",
    "RETRIEVER_INDEX_PATH",
    "PDF_OCR_MAX_PAGES",
    "KG_MAX_ENTITIES_PER_DOC",
    "MCP_TRANSPORT",
    "EXTRACTION_MAX_PAPERS",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_yaml_values_are_effective():
    """键名对齐后，config.yaml 中的值（含此前被静默忽略的块）真正生效。"""
    s = Settings.from_yaml()

    assert s.retriever.index_path == "data/faiss"
    assert s.retriever.top_k == 8
    assert s.retriever.arxiv_max_results == 20
    assert s.retriever.semantic_scholar_max_results == 20
    assert s.retriever.openalex_max_results == 20

    assert s.kg.entity_types[0] == "ResearchProblem"
    assert "CONTRADICTS" in s.kg.relation_types
    assert s.kg.max_entities_per_doc == 60

    assert s.mcp.transport == "memory"
    assert s.mcp.server_timeout_seconds == 30
    assert s.extraction.max_papers == 25
    assert s.extraction.concurrency == 5
    assert s.pdf.min_chars_per_page == 100
    assert s.kb.chunk_size == 800
    assert s.kb.chunk_overlap == 100


def test_env_overrides_yaml_top_level(monkeypatch):
    monkeypatch.setenv("APP_PORT", "9099")
    assert Settings.from_yaml().app.port == 9099


def test_env_overrides_yaml_nested(monkeypatch):
    """嵌套块中 env 也必须压过 YAML（修复前的行为是反过来的）。"""
    monkeypatch.setenv("RETRIEVER_TOP_K", "7")
    monkeypatch.setenv("PDF_OCR_MAX_PAGES", "5")
    s = Settings.from_yaml()
    assert s.retriever.top_k == 7
    assert s.pdf.ocr_max_pages == 5


def test_init_dict_applies_when_no_env_override():
    """无环境变量时，构造参数（dict）生效。"""
    s = Settings(retriever={"top_k": 3})
    assert s.retriever.top_k == 3


def test_env_beats_init_dict_for_nested(monkeypatch):
    """嵌套块中 env 压过显式 dict —— 这是 env > yaml 能成立的同一个机制。"""
    monkeypatch.setenv("RETRIEVER_TOP_K", "7")
    s = Settings(retriever={"top_k": 3})
    assert s.retriever.top_k == 7


def test_missing_yaml_falls_back_to_defaults(tmp_path):
    s = Settings.from_yaml(tmp_path / "does_not_exist.yaml")
    assert s.retriever.top_k == 10
    assert s.mcp.transport == "memory"
    # 恢复默认路径，避免污染后续用例
    Settings.from_yaml(Path(__file__).resolve().parents[2] / "config.yaml")
