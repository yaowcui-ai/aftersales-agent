import importlib
import json

import pytest
import pytest_asyncio
from langchain_core.messages import ToolMessage
from langsmith import tracing_context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def connection():
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
                text("""INSERT INTO aftersales_agent.orders VALUES
                    ('DEMO-001', 'learner-001', '199.00', 'shipped', '2026-10-16', NULL),
                    ('DEMO-003', 'learner-002', '399.00', 'paid', '2026-10-17', NULL)""")
            )
            yield connection
    finally:
        await engine.dispose()


def build_tool(connection, customer_id):
    return importlib.import_module("aftersales.tools").build_order_tool(connection, customer_id)


@pytest.mark.asyncio
async def test_tool_keeps_customer_hidden_and_preserves_result_association(connection):
    tool = build_tool(connection, "learner-001")
    assert list(tool.get_input_schema().model_json_schema()["properties"]) == ["order_id"]
    with tracing_context(enabled=False):
        result = await tool.ainvoke(
            {
                "name": "get_order",
                "args": {"order_id": "DEMO-001"},
                "id": "call-own",
                "type": "tool_call",
            }
        )
    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "call-own"
    data = json.loads(result.content)
    assert data["found"] is True
    assert data["order"]["customer_id"] == "learner-001"
    assert data["order"]["paid_amount"] == "199.00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"order_id": "DEMO-003"},
        {"order_id": "UNKNOWN"},
        {"order_id": "DEMO-003", "customer_id": "learner-002"},
        {"order_id": "DEMO-003' OR 1=1 --"},
    ],
)
async def test_unavailable_orders_and_extra_identity_do_not_expose_facts(connection, arguments):
    tool = build_tool(connection, "learner-001")
    with tracing_context(enabled=False):
        result = await tool.ainvoke(arguments)
    assert json.loads(result) == {"found": False, "order": None}


@pytest.mark.asyncio
async def test_separate_customer_context_can_read_its_own_order(connection):
    tool = build_tool(connection, "learner-002")
    with tracing_context(enabled=False):
        result = await tool.ainvoke({"order_id": "DEMO-003"})
    assert json.loads(result)["order"]["customer_id"] == "learner-002"
