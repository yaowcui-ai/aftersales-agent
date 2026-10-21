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
                        order_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL
                    )
                """)
            )
            await connection.execute(
                text("""
                    CREATE TABLE aftersales_agent.shipments (
                        shipment_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                        carrier TEXT, tracking_number TEXT, status TEXT,
                        latest_event TEXT, latest_event_at TEXT
                    )
                """)
            )
            await connection.execute(
                text("""
                    INSERT INTO aftersales_agent.orders VALUES
                        ('DEMO-001', 'learner-001'), ('DEMO-002', 'learner-001'),
                        ('DEMO-003', 'learner-002'), ('DEMO-004', 'learner-001')
                """)
            )
            await connection.execute(
                text("""
                    INSERT INTO aftersales_agent.shipments VALUES
                        ('SHIP-002', 'DEMO-001', 'DemoExpress', 'TRACK-002',
                            'exception', 'Hold', '2026-10-19'),
                        ('SHIP-001', 'DEMO-001', 'DemoExpress', 'TRACK-001',
                            'in_transit', 'In transit', '2026-10-19'),
                        ('SHIP-003', 'DEMO-002', 'DemoExpress', 'TRACK-003',
                            'delivered', 'Delivered', '2026-10-18'),
                        ('SHIP-004', 'DEMO-003', 'DemoExpress', 'TRACK-004',
                            'pending', 'Pending pickup', '2026-10-19')
                """)
            )
            yield connection
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("order_id", "customer_id", "expected_ids"),
    [
        ("DEMO-001", "learner-001", ["SHIP-001", "SHIP-002"]),
        ("DEMO-002", "learner-001", ["SHIP-003"]),
        ("DEMO-003", "learner-002", ["SHIP-004"]),
        ("DEMO-003", "learner-001", []),
        ("UNKNOWN", "learner-001", []),
        ("DEMO-004", "learner-001", []),
        ("DEMO-001' OR 1=1 --", "learner-001", []),
        ("DEMO-003", "learner-001' OR 1=1 --", []),
        ("demo-001", "learner-001", []),
        ("DEMO-001", "LEARNER-001", []),
    ],
)
async def test_shipments_are_limited_to_the_owned_requested_order(
    connection, order_id, customer_id, expected_ids
):
    assert importlib.util.find_spec("business.shipments") is not None, "Shipment module is missing"
    query = importlib.import_module("business.shipments").get_shipments
    shipments = await query(connection, order_id, customer_id)
    assert isinstance(shipments, list)
    assert all(isinstance(shipment, dict) for shipment in shipments)
    assert [shipment["shipment_id"] for shipment in shipments] == expected_ids
    assert all(shipment["order_id"] == order_id for shipment in shipments)
