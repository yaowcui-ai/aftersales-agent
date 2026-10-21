import importlib
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine


@pytest_asyncio.fixture
async def connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("ATTACH DATABASE ':memory:' AS aftersales_agent"))
            await connection.execute(
                text("""
                    CREATE TABLE aftersales_agent.orders (
                        order_id TEXT PRIMARY KEY,
                        customer_id TEXT NOT NULL,
                        paid_amount NUMERIC NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        delivered_at TEXT
                    )
                """)
            )
            await connection.execute(
                text("""
                    INSERT INTO aftersales_agent.orders VALUES
                        ('DEMO-001', 'learner-001', 199.00, 'shipped', '2026-10-16', NULL),
                        ('DEMO-003', 'learner-002', 399.00, 'paid', '2026-10-17', NULL)
                """)
            )
            yield connection
    finally:
        await engine.dispose()


def order_query():
    assert importlib.util.find_spec("business") is not None, "Reusable business module is missing"
    assert importlib.util.find_spec("business.orders") is not None, "Order query module is missing"
    return importlib.import_module("business.orders").get_order


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("order_id", "customer_id"), [("DEMO-001", "learner-001"), ("DEMO-003", "learner-002")]
)
async def test_customer_can_read_their_own_order(connection, order_id, customer_id):
    order = await order_query()(connection, order_id, customer_id)
    assert isinstance(order, dict)
    assert order["order_id"] == order_id
    assert order["customer_id"] == customer_id
    assert order["delivered_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("order_id", "customer_id"),
    [
        ("DEMO-003", "learner-001"),
        ("UNKNOWN", "learner-001"),
        ("DEMO-003' OR 1=1 --", "learner-001"),
        ("DEMO-003", "learner-001' OR 1=1 --"),
        ("demo-001", "learner-001"),
        ("DEMO-001", "LEARNER-001"),
    ],
)
async def test_unowned_missing_or_invalid_identifiers_do_not_expose_orders(
    connection, order_id, customer_id
):
    assert await order_query()(connection, order_id, customer_id) is None
