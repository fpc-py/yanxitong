"""OpenAlex MCP server — exposes paper search as an MCP tool.

Run standalone over stdio:  python -m src.mcp_servers.openalex_server
"""

from mcp.server.fastmcp import FastMCP

from src.tools.openalex import get_openalex_client

mcp = FastMCP("openalex-search")


@mcp.tool()
async def search_papers(query: str, max_results: int | None = None) -> list[dict]:
    """Search OpenAlex for academic works matching a query.

    Args:
        query: Free-text search query (OpenAlex full-text search).
        max_results: Maximum number of works to return (None = configured default; API caps at 200).
    """
    return await get_openalex_client().search(query, max_results=max_results)


if __name__ == "__main__":
    mcp.run(transport="stdio")
