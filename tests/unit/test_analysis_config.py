"""数据分析流水线 P0 地基：coder 模型角色 + AnalysisConfig 配置块。"""

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.core.config import Settings
from src.core.llm_factory import DEFAULT_PRICING, LLMRouter, get_router, reset_router

_ENV_KEYS = ["LLM_CODER", "ANALYSIS_MAX_RUNS", "ANALYSIS_RECALL_TOP_K", "ANALYSIS_DEEP_VERIFY"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    reset_router()
    yield
    reset_router()


def test_coder_role_from_yaml_and_default():
    s = Settings.from_yaml()
    assert s.llm.coder == "qwen3-coder-flash"
    assert Settings().llm.coder == "qwen3-coder-flash"


def test_coder_env_override(monkeypatch):
    monkeypatch.setenv("LLM_CODER", "qwen3-coder-plus")
    assert Settings.from_yaml().llm.coder == "qwen3-coder-plus"


def test_analysis_config_yaml_values():
    s = Settings.from_yaml()
    assert s.analysis.max_runs == 5
    assert s.analysis.recall_top_k == 6
    assert s.analysis.deep_verify is True
    assert s.analysis.profile_timeout == 15


def test_analysis_config_env_override(monkeypatch):
    monkeypatch.setenv("ANALYSIS_MAX_RUNS", "3")
    monkeypatch.setenv("ANALYSIS_DEEP_VERIFY", "false")
    s = Settings.from_yaml()
    assert s.analysis.max_runs == 3
    assert s.analysis.deep_verify is False


def test_router_coder_role_resolves_model():
    router = LLMRouter()
    llm = router.get_llm("coder")
    assert llm.model_name == "qwen3-coder-flash"
    assert router.coder.model_name == "qwen3-coder-flash"


def test_router_unknown_role_raises():
    with pytest.raises(ValueError, match="Unknown model role"):
        get_router().get_llm("no_such_role")


def test_coder_pricing_entry_exists():
    assert "qwen3-coder-flash" in DEFAULT_PRICING
    p_price, c_price = DEFAULT_PRICING["qwen3-coder-flash"]
    assert p_price > 0 and c_price > 0


def test_call_llm_accepts_role_keyword():
    from src.agents.base import BaseAgent

    params = inspect.signature(BaseAgent._call_llm).parameters
    assert "role" in params
    assert params["role"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["role"].default == ""
