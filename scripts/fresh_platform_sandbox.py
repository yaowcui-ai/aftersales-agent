"""Append a fresh demo batch without deleting history or resetting financial ledgers."""
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlalchemy import insert, select, func
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from aftersales_delivery.config import get_config
from aftersales_delivery.db import orders, inventory, order_policies, shipments


async def main():
    config = get_config()
    url = make_url(config.db_url)
    assert os.getenv("DELIVERY_CONTAINER") == "1" and os.getenv("DELIVERY_PLATFORM_ENABLED") == "1"
    assert url.host == "mysql" and url.database == "aftersales_delivery"
    batch = datetime.now(UTC).strftime("%Y%m%d") + "-" + uuid4().hex[:6]
    sku = "FRESH-" + batch
    engine = create_async_engine(config.db_url)
    now, added = datetime.now(UTC), []
    try:
        async with engine.begin() as conn:
            before = await conn.scalar(select(func.count()).select_from(orders))
            await conn.execute(insert(inventory).values(sku=sku, available=20))
            for suffix, status in (("PAID", "paid"), ("SHIPPED", "shipped"),
                    ("DELIVERED", "delivered"), ("EXPIRED", "paid"), ("RISK", "paid"),
                    ("CANCELLED", "cancelled"), ("OTHER", "paid")):
                oid = "UP-" + batch + "-" + suffix
                await conn.execute(insert(orders).values(order_id=oid,
                    customer_id="learner-002" if suffix == "OTHER" else "learner-001",
                    sku=sku, quantity=2, paid_cents=19900, refunded_cents=0,
                    reshipped_quantity=0, status=status))
                await conn.execute(insert(order_policies).values(order_id=oid,
                    aftersales_until=(now + timedelta(days=-1 if suffix == "EXPIRED" else 30)).isoformat(),
                    risk_level="high" if suffix == "RISK" else "low", manual_review_threshold_cents=50000))
                if status in {"shipped", "delivered"}:
                    await conn.execute(insert(shipments).values(shipment_id="SHIP-"+oid, order_id=oid,
                        carrier="沙箱快递", tracking_number="TRACK-"+oid,
                        status="exception" if status == "shipped" else "delivered",
                        description="沙箱物流异常待核实" if status == "shipped" else "沙箱已签收",
                        updated_at=now.isoformat()))
                added.append(oid)
            after = await conn.scalar(select(func.count()).select_from(orders))
            assert after - before == 7
        print(json.dumps({"batch": batch, "orders_added": added,
                          "old_records_preserved": True, "financial_ledgers_reset": False}, ensure_ascii=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
