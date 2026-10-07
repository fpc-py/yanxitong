"""MCP runtime — invoke data-source MCP servers from agent code.

Transport (``settings.mcp.transport``):

* ``memory`` (default) — in-process ``ClientSession`` over MCP memory streams
  (``mcp.shared.memory``). No subprocess, so it is safe on Windows.
* ``stdio`` — spawns ``python -m src.mcp_servers.<name>_server`` per call.
  Intended for protocol-fidelity demos; memory remains the runtime default.

Failures are contained: a source that errors, times out, or returns no usable
payload yields ``[]`` so one broken source degrades a multi-source search
instead of failing it.
"""

import asyncio
import json
import logging
import sys
from typing import Any, Optional

from src.core.config import get_settings
from src.mcp_servers.registry import get_server_instance, get_server_module

logger = logging.getLogger(__name__)


class MCPToolError(RuntimeError):
    """Raised when a tool call reports an error result."""


def _extract_papers(result: Any) -> list[dict]:
    """Pull the paper list out of a ``CallToolResult``.

    FastMCP wraps non-dict returns in ``structuredContent = {"result": [...]}``;
    text content blocks carry either one JSON object per paper (list returns)
    or a single serialized payload.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        inner = structured.get("result", structured)
        if isinstance(inner, list):
            return [item for item in inner if isinstance(item, dict)]
        if isinstance(inner, dict):
            return [inner]

    papers = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(data, list):
            papers.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict):
            inner = data.get("result")
            if isinstance(inner, list):
                papers.extend(item for item in inner if isinstance(item, dict))
            else:
                papers.append(data)
    return papers


async def _call_via_memory(source: str, tool: str, args: dict) -> list[dict]:
    from mcp.shared.memory import create_connected_server_and_client_session

    server = get_server_instance(source)
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(tool, args)
    if getattr(result, "isError", False):
        raise MCPToolError(f"{source}.{tool} returned an error result")
    return _extract_papers(result)


async def _call_via_stdio(source: str, tool: str, args: dict) -> list[dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", get_server_module(source)],
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
    if getattr(result, "isError", False):
        raise MCPToolError(f"{source}.{tool} returned an error result")
    return _extract_papers(result)


async def call_tool(
    source: str,
    tool: str,
    args: dict,
    timeout: Optional[float] = None,
) -> list[dict]:
    """Call ``tool`` on the MCP server for ``source``.

    Returns the list of paper dicts (possibly empty). Any failure — unknown
    source, transport error, timeout, error result — is logged and swallowed
    as ``[]`` so callers can fan out across sources without try/except.
    """
    settings = get_settings()
    timeout = timeout or settings.mcp.server_timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            if settings.mcp.transport == "stdio":
                return await _call_via_stdio(source, tool, args)
            return await _call_via_memory(source, tool, args)
    except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001 - degrade, never raise
        logger.warning("MCP call %s.%s failed (%s); degrading to empty result", source, tool, e)
        return []
