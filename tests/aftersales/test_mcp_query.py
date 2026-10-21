import importlib
import json

import pytest
import pytest_asyncio
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


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
                text("""INSERT INTO aftersales_agent.orders VALUES
                    ('DEMO-001', 'learner-001', '199.00', 'shipped', '2026-10-16', NULL),
                    ('DEMO-003', 'learner-002', '399.00', 'paid', '2026-10-17', NULL)""")
            )
        yield engine
    finally:
        await engine.dispose()


def query():
    return importlib.import_module("aftersales.mcp_server").query_order_for_mcp


@pytest.mark.asyncio
async def test_mcp_query_returns_current_customers_order(engine):
    result = json.loads(await query()(engine, "DEMO-001", "learner-001"))
    assert result["found"] is True
    assert result["order"]["customer_id"] == "learner-001"
    assert result["order"]["paid_amount"] == "199.00"


@pytest.mark.asyncio
@pytest.mark.parametrize("order_id", ["DEMO-003", "UNKNOWN"])
async def test_unowned_and_missing_are_indistinguishable(engine, order_id):
    result = json.loads(await query()(engine, order_id, "learner-001"))
    assert result == {"found": False, "order": None}


@pytest.mark.asyncio
async def test_query_failure_is_sanitized_and_not_reported_as_not_found():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        with pytest.raises(ToolError) as error:
            await query()(engine, "PRIVATE_ORDER_MARKER", "PRIVATE_CUSTOMER_MARKER")
        message = str(error.value)
        assert "ORDER_QUERY_FAILED" in message
        assert "SELECT" not in message
        assert "PRIVATE_ORDER_MARKER" not in message
        assert "PRIVATE_CUSTOMER_MARKER" not in message
    finally:
        await engine.dispose()
