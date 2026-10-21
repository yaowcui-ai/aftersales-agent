"""Read order facts within the current customer's ownership scope."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def get_order(
    connection: AsyncConnection, order_id: str, customer_id: str
) -> dict[str, Any] | None:
    """Read one owned order; the caller must supply a trusted customer identity."""
    statement = text("""
        SELECT order_id, customer_id, paid_amount, status,
               created_at, delivered_at
        FROM aftersales_agent.orders
        WHERE order_id = :order_id AND customer_id = :customer_id
    """)
    result = await connection.execute(
        statement,
        {"order_id": order_id, "customer_id": customer_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None
