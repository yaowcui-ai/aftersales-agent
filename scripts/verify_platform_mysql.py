"""Real isolated Docker MySQL and HTTP MCP checks; no model or payment calls."""
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlalchemy import func, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from aftersales_delivery.business import BusinessError, Store
from aftersales_delivery.config import get_config
from aftersales_delivery.db import orders, inventory, order_policies, refunds, reships
from aftersales_delivery.transport import call_tool


async def main():
    config = get_config()
    url = make_url(config.db_url)
    assert os.getenv("DELIVERY_CONTAINER") == "1" and url.host == "mysql"
    assert url.database == "aftersales_delivery"
    engine = create_async_engine(config.db_url)
    store = Store(engine)
    prefix = "VERIFY-" + uuid4().hex[:12]
    checks = []

    async def fixture(suffix, status="paid", sku=None, available=3):
        oid, sku = prefix + "-" + suffix, sku or prefix + "-SKU-" + suffix
        async with engine.begin() as conn:
            if not await conn.scalar(select(inventory.c.sku).where(inventory.c.sku == sku)):
                await conn.execute(insert(inventory).values(sku=sku, available=available))
            await conn.execute(insert(orders).values(order_id=oid, customer_id="learner-001", sku=sku,
                quantity=2, paid_cents=20000, refunded_cents=0, reshipped_quantity=0, status=status))
            await conn.execute(insert(order_policies).values(order_id=oid,
                aftersales_until=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
                risk_level="low", manual_review_threshold_cents=50000))
        return oid

    async def prepare(oid, suffix, kind="refund", amount="10.00"):
        action = await store.prepare("learner-001", prefix + suffix, kind, oid, "沙箱MySQL故障验证", amount)
        await store.decide(action["action_id"], True, 1, "reviewer", reason="runtime verification")
        return action

    try:
        oid = await fixture("REPLAY")
        action = await prepare(oid, "replay")
        first, second = await asyncio.gather(store.execute(action["action_id"]), store.execute(action["action_id"]))
        assert first["result"] == second["result"]
        async with engine.connect() as conn:
            assert await conn.scalar(select(func.count()).select_from(refunds).where(refunds.c.action_id == action["action_id"])) == 1
        checks.append({"name": "same_action_concurrent_replay", "passed": True, "action_id": action["action_id"]})

        oid = await fixture("LIMIT")
        a, b = await prepare(oid, "limit-a", amount="150.00"), await prepare(oid, "limit-b", amount="150.00")
        outcomes = await asyncio.gather(store.execute(a["action_id"]), store.execute(b["action_id"]), return_exceptions=True)
        assert sum(isinstance(x, dict) for x in outcomes) == 1
        assert (await store.get_order("learner-001", oid))["refunded_amount"] == "150.00"
        checks.append({"name": "cross_action_refund_limit_race", "passed": True, "order_id": oid})

        sku = prefix + "-SHARED"
        one = await fixture("STOCK-A", "shipped", sku, 1)
        two = await fixture("STOCK-B", "shipped", sku, 1)
        a = await prepare(one, "stock-a", "reship", None)
        b = await prepare(two, "stock-b", "reship", None)
        outcomes = await asyncio.gather(store.execute(a["action_id"]), store.execute(b["action_id"]), return_exceptions=True)
        assert sum(isinstance(x, dict) for x in outcomes) == 1
        assert (await store.get_inventory(sku))["available"] == 0
        checks.append({"name": "shared_stock_race_no_oversell", "passed": True, "sku": sku})

        oid = await fixture("RETURN", "shipped")
        action = await prepare(oid, "return", "return_refund")
        allowed = await store.execute(action["action_id"])
        assert allowed["status"] == "awaiting_return"
        assert (await store.get_order("learner-001", oid))["refunded_amount"] == "0.00"
        received = await store.receive_return(action["action_id"], "reviewer")
        repeat = await store.receive_return(action["action_id"], "reviewer")
        assert received["result"] == repeat["result"]
        assert (await store.get_order("learner-001", oid))["refunded_amount"] == "10.00"
        checks.append({"name": "return_receipt_exactly_once", "passed": True, "action_id": action["action_id"]})

        oid = await fixture("LOST")
        action = await prepare(oid, "lost")
        committed = await store.execute(action["action_id"])
        # Deliberately discard the committed response and reconnect the pool.
        await engine.dispose()
        replay = await Store(engine).execute(action["action_id"])
        assert committed["result"] == replay["result"]
        checks.append({"name": "commit_response_loss_pool_reconnect", "passed": True, "action_id": action["action_id"]})

        actual = await call_tool(config, "learner-001", "get_order", {"order_id": oid})
        denied = await call_tool(config, "learner-002", "get_order", {"order_id": oid})
        assert actual["found"] and not denied["found"]
        listing = await call_tool(config, "learner-001", "list_orders", {"limit": 2})
        assert all(row["customer_id"] == "learner-001" for row in listing["orders"])
        checks.append({"name": "independent_http_mcp_customer_isolation", "passed": True})

        pending_oid = await fixture("GATE")
        pending = await store.prepare("learner-001", prefix + "gate", "refund", pending_oid, "gate", "1.00")
        for args in ({}, {"action_id": pending["action_id"]}):
            try:
                await call_tool(config, "learner-001", "execute_action", {"action_id": pending["action_id"]}, **args)
            except (BusinessError, ValueError):
                pass
            else:
                raise AssertionError("MCP execution bypassed approval/scope")
        assert (await store.get_order("learner-001", pending_oid))["refunded_amount"] == "0.00"
        checks.append({"name": "mcp_scope_and_approval_enforced", "passed": True})
        result = {"run_id": prefix, "time_utc": datetime.now(UTC).isoformat(), "backend": "mysql",
                  "transport": "real Streamable HTTP", "model_calls": 0,
                  "checks": checks, "passed": len(checks), "total": len(checks), "success": True}
        Path("/app/output/platform-mysql.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
