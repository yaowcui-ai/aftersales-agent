import json

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_real_mcp_protocol_context_isolation_and_approval_gate(tmp_path):
    from aftersales_delivery.business import Store
    from aftersales_delivery.config import Config
    from aftersales_delivery.db import initialize
    from aftersales_delivery.mcp_server import create_app
    from aftersales_delivery.security import issue_token

    config = Config(f"sqlite+aiosqlite:///{tmp_path / 'mcp.sqlite'}", "key" * 16, {})
    engine = create_async_engine(config.db_url)
    await initialize(engine)
    store = Store(engine)
    action = await store.prepare(
        "learner-001", "mcp-approval", "refund", "DEMO-004", "退款", "10.00"
    )
    app = create_app(config)

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth
        )

    try:
        async with app.router.lifespan_context(app):
            for owner in ["learner-001", "learner-002"]:
                token = issue_token(
                    config.signing_key,
                    "service",
                    owner,
                    "customer",
                    purpose="mcp",
                    action_id=action["action_id"],
                )
                async with streamablehttp_client(
                    "http://test/mcp",
                    headers={"Authorization": "Bearer " + token},
                    httpx_client_factory=factory,
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        assert {t.name for t in tools.tools} == {
                            "list_orders",
                            "get_order",
                            "get_shipments",
                            "get_inventory",
                            "get_case",
                            "execute_action",
                        }
                        order = await session.call_tool("get_order", {"order_id": "DEMO-001"})
                        assert not order.isError
                        assert json.loads(order.content[0].text)["found"] is (
                            owner == "learner-001"
                        )
                        wrong = await session.call_tool(
                            "execute_action", {"action_id": "not-bound"}
                        )
                        assert wrong.isError
                        unapproved = await session.call_tool(
                            "execute_action", {"action_id": action["action_id"]}
                        )
                        assert unapproved.isError
            assert await store.list_cases(None) == []
    finally:
        await engine.dispose()
