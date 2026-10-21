"""Short-lived, customer-scoped MCP sessions over real HTTP."""

import json
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from aftersales_delivery.business import BusinessError
from aftersales_delivery.security import issue_token

READ_TOOLS = {"get_order", "get_shipments", "get_inventory", "get_case"}


def http_client(headers=None, timeout=None, auth=None):
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=True,
        trust_env=False,
    )


async def call_tool(config, customer_id, name, arguments, *, action_id=None):
    if name not in READ_TOOLS and not (name == "execute_action" and action_id):
        raise ValueError("TOOL_NOT_ALLOWED")
    token = issue_token(
        config.signing_key,
        "agent-service",
        customer_id,
        "customer",
        purpose="mcp",
        action_id=action_id,
        ttl=90,
    )
    async with streamablehttp_client(
        config.mcp_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timedelta(seconds=20),
        sse_read_timeout=timedelta(seconds=45),
        httpx_client_factory=http_client,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            if result.isError:
                text = " ".join(block.text for block in result.content if block.type == "text")
                safe = text.split(":")[-1].strip()
                if safe.isupper() and len(safe) < 80:
                    raise BusinessError(safe)
                raise RuntimeError("MCP_TOOL_FAILED")
            texts = [block.text for block in result.content if block.type == "text"]
            if not texts:
                raise RuntimeError("MCP_RESULT_EMPTY")
            return json.loads(texts[0])
