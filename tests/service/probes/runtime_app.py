"""Network + real stdio MCP probe, without models or databases."""

import asyncio
import json
import os
import sys
from pathlib import Path


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    data = {"status": "ok", "pid": os.getpid()}
    if scope["path"] == "/mcp":
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(Path(__file__).with_name("runtime_mcp.py"))],
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("ping", {})
                data.update(
                    tools=[tool.name for tool in tools.tools],
                    mcp_ok=not result.isError and json.loads(result.content[0].text)["ok"],
                    loop=type(asyncio.get_running_loop()).__name__,
                )
    body = json.dumps(data).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
