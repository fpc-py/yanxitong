"""arXiv MCP server — exposes paper search as an MCP tool.

Run standalone over stdio:  python -m src.mcp_servers.arxiv_server
"""

from mcp.server.fastmcp import FastMCP

from src.tools.arxiv import get_arxiv_client

mcp = FastMCP("arxiv-search")


@mcp.tool()
async def search_papers(query: str, max_results: int | None = None) -> list[dict]:
    """Search arXiv for papers matching a query.

    Args:
        query: Free-text search query (arXiv field prefixes like ``ti:`` are honored).
        max_results: Maximum number of papers to return (None = configured default).
    """
    return await get_arxiv_client().search(query, max_results=max_results)


if __name__ == "__main__":
    mcp.run(transport="stdio")
