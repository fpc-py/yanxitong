"""Semantic Scholar MCP server — exposes paper search as an MCP tool.

Run standalone over stdio:  python -m src.mcp_servers.semantic_scholar_server
"""

from mcp.server.fastmcp import FastMCP

from src.tools.semantic_scholar import get_ss_client

mcp = FastMCP("semantic-scholar-search")


@mcp.tool()
async def search_papers(query: str, max_results: int | None = None) -> list[dict]:
    """Search Semantic Scholar for papers matching a query.

    Args:
        query: Free-text search query.
        max_results: Maximum number of papers to return (None = configured default; API caps at 100).
    """
    return await get_ss_client().search(query, max_results=max_results)


if __name__ == "__main__":
    mcp.run(transport="stdio")
