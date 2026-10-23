import json

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
def listing_config(tmp_path):
    from aftersales_delivery.config import Config

    return Config(f"sqlite+aiosqlite:///{tmp_path / 'orders.sqlite'}", "key" * 16, {})


@pytest.mark.asyncio
async def test_order_listing_is_paginated_exact_customer_scoped_and_read_only(listing_config):
    from aftersales_delivery.business import BusinessError, Store
    from aftersales_delivery.db import initialize, metadata, orders

    engine = create_async_engine(listing_config.db_url)
    try:
        await initialize(engine)
        async with engine.begin() as conn:
            # A differently cased identity must not become visible on MySQL either.
            await conn.execute(insert(orders).values(
                order_id="PRIVATE-UPPER", customer_id="LEARNER-001", sku="SKU-001",
                quantity=1, paid_cents=500, refunded_cents=0, reshipped_quantity=0, status="paid"))
            await conn.execute(insert(orders).values(
                order_id="PRIVATE-SPACE", customer_id="learner-001 ", sku="SKU-001",
                quantity=1, paid_cents=500, refunded_cents=0, reshipped_quantity=0, status="paid"))
            before = {table.name: await conn.scalar(select(func.count()).select_from(table))
                      for table in metadata.sorted_tables}
        store = Store(engine)
        first = await store.list_orders("learner-001", limit=2)
        assert first["total"] == 3
        assert [row["order_id"] for row in first["orders"]] == ["DEMO-001", "DEMO-002"]
        assert first["orders"][0]["paid_amount"] == "199.00"
        assert first["has_more"] and first["next_offset"] == 2
        last = await store.list_orders("learner-001", limit=2, offset=2)
        assert [row["order_id"] for row in last["orders"]] == ["DEMO-004"]
        assert last["total"] == 3 and not last["has_more"] and last["next_offset"] is None
        other = await store.list_orders("learner-002")
        assert other["total"] == 1
        assert other["orders"][0]["order_id"] == "DEMO-003"
        for owner, order_id in (("LEARNER-001", "PRIVATE-UPPER"),
                                ("learner-001 ", "PRIVATE-SPACE")):
            exact = await store.list_orders(owner)
            assert exact["total"] == 1
            assert [row["order_id"] for row in exact["orders"]] == [order_id]
            assert exact["orders"][0]["customer_id"] == owner
        empty = await store.list_orders("no-orders")
        assert empty["total"] == 0 and empty["orders"] == [] and not empty["has_more"]
        beyond = await store.list_orders("learner-001", offset=100)
        assert beyond["total"] == 3 and beyond["orders"] == [] and not beyond["has_more"]
        for arguments in ({"limit": 0}, {"limit": 101}, {"offset": -1},
                          {"limit": True}, {"offset": "0"}):
            with pytest.raises(BusinessError):
                await store.list_orders("learner-001", **arguments)
        async with engine.connect() as conn:
            after = {table.name: await conn.scalar(select(func.count()).select_from(table))
                     for table in metadata.sorted_tables}
        assert after == before
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_orders_mcp_uses_trusted_identity_and_pagination(listing_config):
    from aftersales_delivery.db import initialize
    from aftersales_delivery.mcp_server import create_app
    from aftersales_delivery.security import issue_token

    engine = create_async_engine(listing_config.db_url)
    await initialize(engine)
    app = create_app(listing_config)

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 headers=headers, timeout=timeout, auth=auth)

    try:
        async with app.router.lifespan_context(app):
            for owner, expected in (("learner-001", 3), ("learner-002", 1), ("empty", 0)):
                token = issue_token(listing_config.signing_key, "service", owner,
                                    "customer", purpose="mcp")
                async with streamablehttp_client("http://test/mcp",
                    headers={"Authorization": "Bearer " + token},
                    httpx_client_factory=factory) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tool = next(t for t in (await session.list_tools()).tools
                                    if t.name == "list_orders")
                        assert "customer_id" not in tool.inputSchema.get("properties", {})
                        result = await session.call_tool("list_orders", {"limit": 1})
                        assert not result.isError
                        payload = json.loads(result.content[0].text)
                        assert payload["total"] == expected
                        assert len(payload["orders"]) == min(1, expected)
                        assert all(row["customer_id"] == owner for row in payload["orders"])
                        invalid = await session.call_tool("list_orders", {"limit": 101})
                        assert invalid.isError
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_order_count_question_routes_through_read_tool_without_proposal(listing_config):
    from aftersales_delivery.agent import build_graph, graph_config
    from aftersales_delivery.api import Runtime
    from aftersales_delivery.business import Store
    from aftersales_delivery.db import initialize
    from aftersales_delivery.transport import READ_TOOLS

    assert "list_orders" in READ_TOOLS
    engine = create_async_engine(listing_config.db_url)
    await initialize(engine)
    count = 0

    async def model(messages, tools):
        nonlocal count
        count += 1
        tool = next(t for t in tools if t.name == "list_orders")
        assert "customer_id" not in tool.args
        if count == 1:
            return AIMessage(content="", tool_calls=[
                {"id": "orders-1", "name": "list_orders", "args": {}}])
        result = json.loads(messages[-1].content)
        assert result["total"] == 3 and len(result["orders"]) == 3
        return AIMessage(content="您当前共有3个订单：DEMO-001、DEMO-002、DEMO-004。")

    async def forbidden(*args):
        raise AssertionError("Listing must not execute business writes or request approval")

    try:
        async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
            store = Store(engine)
            runtime = Runtime(listing_config, store, saver, local_tools=True)
            graph = build_graph(store, saver, "learner-001", "order-count", model,
                lambda name, args: runtime.read("learner-001", name, args), forbidden, forbidden)
            result = await graph.ainvoke({"messages": [{"role": "user", "content": "我现在有几个订单。"}],
                "turn_calls": 0}, graph_config("learner-001", "order-count"))
            assert result["status"] == "answered" and result.get("proposal") is None
            assert "3个订单" in result["final_answer"] and count == 2
    finally:
        await engine.dispose()
