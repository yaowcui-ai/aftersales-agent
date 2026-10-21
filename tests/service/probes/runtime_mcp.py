"""Real MCP handshake/ping server with no access to project data."""

from mcp.server.fastmcp import FastMCP

server = FastMCP("runtime-probe")


@server.tool()
def ping() -> dict[str, bool]:
    """Verify stdio subprocess transport."""
    return {"ok": True}


if __name__ == "__main__":
    server.run(transport="stdio")
