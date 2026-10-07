"""Data-source MCP registry.

Single place mapping a source name to its FastMCP server module. Adding a new
literature source = add one ``*_server.py`` module + one entry here (+ config),
nothing else in the agent layer changes.
"""

from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

# Source name -> dotted module path (importable, must expose ``mcp: FastMCP``).
SOURCE_MODULES: dict[str, str] = {
    "arxiv": "src.mcp_servers.arxiv_server",
    "semantic_scholar": "src.mcp_servers.semantic_scholar_server",
    "openalex": "src.mcp_servers.openalex_server",
}

# Tool implemented by every source server.
SEARCH_TOOL = "search_papers"

# Default source set used by the retriever agent, in priority order.
DEFAULT_SOURCES: tuple[str, ...] = ("arxiv", "semantic_scholar", "openalex")

_instances: dict[str, FastMCP] = {}


def get_server_module(source: str) -> str:
    """Return the dotted module path for ``source`` (raises ``KeyError`` if unknown)."""
    return SOURCE_MODULES[source]


def get_server_instance(source: str) -> FastMCP:
    """Import (once) and return the FastMCP instance for ``source``.

    Instances are cached; callers, not this registry, own lifecycle.
    """
    if source not in SOURCE_MODULES:
        raise KeyError(f"Unknown MCP source: {source!r}. Known: {sorted(SOURCE_MODULES)}")
    if source not in _instances:
        import importlib

        module = importlib.import_module(SOURCE_MODULES[source])
        _instances[source] = module.mcp
    return _instances[source]
