import importlib
import json
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import InMemorySaver
from langsmith import tracing_context
from mcp.shared.memory import create_connected_server_and_client_session
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from aftersales.checkpoints import build_checkpoint_config, read_checkpoint_history
from aftersales.graph import build_aftersales_graph
from aftersales.mcp_client import load_order_mcp_tools
from aftersales.mcp_server import build_mcp_server


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("ATTACH DATABASE ':memory:' AS aftersales_agent"))
            await connection.execute(
                text("""CREATE TABLE aftersales_agent.orders (
                order_id TEXT PRIMARY KEY, customer_id TEXT, paid_amount TEXT,
                status TEXT, created_at TEXT, delivered_at TEXT)""")
            )
            await connection.execute(
                text("""CREATE TABLE aftersales_agent.shipments (
                shipment_id TEXT PRIMARY KEY, order_id TEXT, carrier TEXT,
                tracking_number TEXT, status TEXT, latest_event TEXT, latest_event_at TEXT)""")
            )
            await connection.execute(
                text("""INSERT INTO aftersales_agent.orders VALUES
                ('DEMO-001', 'learner-001', '199.00', 'shipped', '2026-10-16', NULL),
                ('DEMO-003', 'learner-002', '399.00', 'paid', '2026-10-17', NULL),
                ('DEMO-004', 'learner-001', '99.00', 'paid', '2026-10-18', NULL)""")
            )
            await connection.execute(
                text("""INSERT INTO aftersales_agent.shipments VALUES
                ('SHIP-002', 'DEMO-001', '模拟快递', 'TRACK-002', 'exception',
                    '模拟：收件信息待核实，派送暂缓', '2026-10-19 15:50:29'),
                ('SHIP-001', 'DEMO-001', '模拟快递', 'TRACK-001', 'in_transit',
                    '模拟：包裹已到达广州分拨中心', '2026-10-19 14:20:29'),
                ('SHIP-004', 'DEMO-003', '模拟快递', 'TRACK-004', 'pending',
                    '模拟：等待揽收', '2026-10-19 16:20:29')""")
            )
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def shipment_tool(engine, transport, customer_id="learner-001"):
    if transport == "local":
        factory = getattr(importlib.import_module("aftersales.tools"), "build_shipment_tool", None)
        assert callable(factory), "The local shipment Tool is missing"
        async with engine.connect() as connection:
            yield factory(connection, customer_id)
    else:
        async with create_connected_server_and_client_session(
            build_mcp_server(engine, customer_id)
        ) as session:
            listed = await session.list_tools()
            raw = next(tool for tool in listed.tools if tool.name == "get_shipments")
            assert list(raw.inputSchema["properties"]) == ["order_id"]
            tools = await load_order_mcp_tools(session)
            assert {tool.name for tool in tools} == {"get_order", "get_shipments"}
            yield next(tool for tool in tools if tool.name == "get_shipments")


async def call(tool, order_id, **extra):
    with tracing_context(enabled=False):
        return await tool.ainvoke(
            {
                "name": "get_shipments",
                "args": {"order_id": order_id, **extra},
                "id": "call-shipment-test",
                "type": "tool_call",
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["local", "mcp"])
async def test_tools_hide_customer_and_return_each_owned_package(engine, transport):
    async with shipment_tool(engine, transport) as tool:
        assert list(tool.args) == ["order_id"]
        provider_schema = convert_to_openai_tool(tool)["function"]["parameters"]
        assert list(provider_schema["properties"]) == ["order_id"]
        result = await call(tool, "DEMO-001")
        assert isinstance(result, ToolMessage)
        assert result.tool_call_id == "call-shipment-test"
        data = json.loads(result.text)
        assert [row["shipment_id"] for row in data["shipments"]] == ["SHIP-001", "SHIP-002"]
        assert [row["status"] for row in data["shipments"]] == ["in_transit", "exception"]
        assert all(row["order_id"] == "DEMO-001" for row in data["shipments"])


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["local", "mcp"])
@pytest.mark.parametrize("order_id", ["DEMO-003", "UNKNOWN", "DEMO-004"])
async def test_unowned_missing_and_no_shipments_share_empty_result(engine, transport, order_id):
    async with shipment_tool(engine, transport) as tool:
        result = await call(tool, order_id)
        assert result.status == "success"
        assert json.loads(result.text) == {"shipments": []}


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["local", "mcp"])
async def test_separate_customer_context_can_read_its_own_shipments(engine, transport):
    async with shipment_tool(engine, transport, "learner-002") as tool:
        result = await call(tool, "DEMO-003")
        assert [row["shipment_id"] for row in json.loads(result.text)["shipments"]] == ["SHIP-004"]


@pytest.mark.asyncio
async def test_extra_local_identity_argument_cannot_change_customer(engine):
    async with shipment_tool(engine, "local") as tool:
        result = await call(tool, "DEMO-003", customer_id="learner-002")
        assert json.loads(result.text) == {"shipments": []}


@pytest.mark.asyncio
async def test_mcp_query_error_is_not_an_empty_success_or_private_sql():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with shipment_tool(engine, "mcp") as tool:
            result = await call(tool, "PRIVATE_ORDER_MARKER")
            assert result.status == "error"
            assert "SHIPMENT_QUERY_FAILED" in result.text
            assert "PRIVATE_ORDER_MARKER" not in result.text
            assert "SELECT" not in result.text
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_existing_graph_executes_both_mcp_tools_and_saves_associated_results(engine):
    saver = InMemorySaver()
    config = build_checkpoint_config("learner-001", "two-tools")
    async with create_connected_server_and_client_session(
        build_mcp_server(engine, "learner-001")
    ) as session:
        tools = await load_order_mcp_tools(session)
        assert {tool.name for tool in tools} == {"get_order", "get_shipments"}

        async def respond(messages):
            if isinstance(messages[-1], HumanMessage):
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": name,
                            "args": {"order_id": "DEMO-001"},
                            "id": "call-" + name,
                            "type": "tool_call",
                        }
                        for name in ("get_order", "get_shipments")
                    ],
                )
            results = messages[-2:]
            assert all(isinstance(result, ToolMessage) for result in results)
            assert [result.tool_call_id for result in results] == [
                "call-get_order",
                "call-get_shipments",
            ]
            assert json.loads(results[0].text)["order"]["paid_amount"] == "199.00"
            assert len(json.loads(results[1].text)["shipments"]) == 2
            return AIMessage(content="OFFLINE_BOTH_TOOLS_OK")

        with tracing_context(enabled=False):
            graph = build_aftersales_graph(RunnableLambda(respond), tools, checkpointer=saver)
            await graph.ainvoke(
                {"messages": [HumanMessage(content="查订单和物流")]},
                config=config,
                durability="sync",
            )
    history = await read_checkpoint_history(saver, config)
    assert [message.type for message in history] == ["human", "ai", "tool", "tool", "ai"]
    assert history[-1].text == "OFFLINE_BOTH_TOOLS_OK"
