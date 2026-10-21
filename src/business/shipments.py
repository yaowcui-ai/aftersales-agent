"""Read shipment facts within the requested order and customer scope."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def get_shipments(
    connection: AsyncConnection, order_id: str, customer_id: str
) -> list[dict[str, Any]]:
    """List an owned order's packages; the caller supplies trusted customer identity."""
    statement = text("""
        SELECT s.shipment_id, s.order_id, s.carrier, s.tracking_number,
               s.status, s.latest_event, s.latest_event_at
        FROM aftersales_agent.shipments AS s
        JOIN aftersales_agent.orders AS o ON s.order_id = o.order_id
        WHERE o.order_id = :order_id AND o.customer_id = :customer_id
        ORDER BY s.shipment_id
    """)
    result = await connection.execute(
        statement,
        {"order_id": order_id, "customer_id": customer_id},
    )
    rows = result.mappings().all()
    return [dict(row) for row in rows]
