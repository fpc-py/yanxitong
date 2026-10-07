"""Unit tests for data-source MCP servers over the in-memory transport (no network)."""

import importlib

import pytest

from src.mcp_servers.registry import DEFAULT_SOURCES, SEARCH_TOOL, get_server_module
from src.tools import mcp_runtime

PAPER = {
    "title": "A Paper About Widgets",
    "authors": ["Alice"],
    "year": 2024,
    "abstract": "We study widgets.",
    "url": "https://arxiv.org/abs/2401.00001",
    "source": "arxiv",
    "doi": "",
    "arxiv_id": "2401.00001",
    "key_findings": [],
    "methods": [],
    "datasets": [],
    "metrics": {},
    "text": "A Paper About Widgets. We study widgets.",
}

SOURCES = [
    ("arxiv", "src.mcp_servers.arxiv_server", "get_arxiv_client"),
    ("semantic_scholar", "src.mcp_servers.semantic_scholar_server", "get_ss_client"),
    ("openalex", "src.mcp_servers.openalex_server", "get_openalex_client"),
]


class _FakeClient:
    def __init__(self, papers):
        self.papers = papers
        self.calls = []

    async def search(self, query, max_results=None):
        self.calls.append({"query": query, "max_results": max_results})
        return self.papers


@pytest.mark.parametrize("source,module_path,getter", SOURCES)
async def test_server_tool_roundtrip(source, module_path, getter, monkeypatch):
    module = importlib.import_module(module_path)
    fake = _FakeClient([dict(PAPER)])
    monkeypatch.setattr(module, getter, lambda: fake)

    result = await mcp_runtime.call_tool(source, SEARCH_TOOL, {"query": "widgets", "max_results": 3})

    assert result == [PAPER]
    assert fake.calls == [{"query": "widgets", "max_results": 3}]


@pytest.mark.parametrize("source,module_path,getter", SOURCES)
async def test_server_tool_error_degrades_to_empty(source, module_path, getter, monkeypatch):
    module = importlib.import_module(module_path)

    class _BrokenClient:
        async def search(self, query, max_results=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(module, getter, lambda: _BrokenClient())

    assert await mcp_runtime.call_tool(source, SEARCH_TOOL, {"query": "widgets"}) == []


def test_registry_maps_every_default_source():
    for source in DEFAULT_SOURCES:
        assert get_server_module(source).startswith("src.mcp_servers.")


async def test_unknown_source_degrades_to_empty():
    assert await mcp_runtime.call_tool("no-such-source", SEARCH_TOOL, {"query": "x"}) == []


class _Block:
    def __init__(self, text):
        self.text = text


class _Result:
    def __init__(self, structured=None, content=None, isError=False):
        self.structuredContent = structured
        self.content = content or []
        self.isError = isError


def test_extract_papers_from_wrapped_structured_content():
    result = _Result(structured={"result": [PAPER]})
    assert mcp_runtime._extract_papers(result) == [PAPER]


def test_extract_papers_from_json_text_blocks():
    import json

    result = _Result(content=[_Block(json.dumps(PAPER))])
    assert mcp_runtime._extract_papers(result) == [PAPER]


def test_extract_papers_from_text_list_payload():
    import json

    result = _Result(content=[_Block(json.dumps([PAPER, PAPER]))])
    assert mcp_runtime._extract_papers(result) == [PAPER, PAPER]


def test_extract_papers_ignores_non_json_blocks():
    result = _Result(content=[_Block("not json"), _Block("")])
    assert mcp_runtime._extract_papers(result) == []
