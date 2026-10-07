"""MCP servers exposing literature data sources as tools.

Each module in this package wraps one external data source behind a FastMCP
server (``python -m src.mcp_servers.<name>_server`` runs it standalone over
stdio). The agent layer talks to them through ``src.tools.mcp_runtime``, so
adding a source means adding one server module plus a registry entry — no
agent code changes.
"""
