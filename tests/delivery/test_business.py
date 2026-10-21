"""Real SQLite transactions exercise the same Core statements used on MySQL."""

import asyncio
import importlib.util

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import create_async_engine


def test_delivery_business_module_exists():
    assert importlib.util.find_spec("aftersales_delivery.business") is not None


@pytest_asyncio.fixture
async def store(tmp_path):
    from aftersales_delivery.business import Store
    from aftersales_delivery.db import initialize

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}", connect_args={"timeout": 20}
    )
    await initialize(engine)
    yield Store(engine)
    await engine.dispose()


async def expect_error(code, operation):
    from aftersales_delivery.business import BusinessError

    with pytest.raises(BusinessError) as error:
        await operation
    assert error.value.code == code


async def approved(
    store,
    thread="refund-1",
    kind="refund",
    order="DEMO-001",
    amount="100.00",
    customer="learner-001",
):
    action = await store.prepare(customer, thread, kind, order, "模拟售后申请", amount=amount)
    await store.decide(action["action_id"], True, 1, "reviewer")
    return action["action_id"]


@pytest.mark.asyncio
async def test_seed_is_non_destructive_and_money_is_exact(store):
    from aftersales_delivery.db import initialize, orders

    order = await store.get_order("learner-001", "DEMO-001")
    assert (order["paid_amount"], order["refunded_amount"], order["sku"], order["quantity"]) == (
        "199.00",
        "0.00",
        "SKU-001",
        1,
    )
    async with store.engine.begin() as conn:
        await conn.execute(
            update(orders).where(orders.c.order_id == "DEMO-001").values(status="cancelled")
        )
    await initialize(store.engine)
    assert (await store.get_order("learner-001", "DEMO-001"))["status"] == "cancelled"
    assert len(await store.get_shipments("learner-001", "DEMO-001")) == 2
    assert (await store.get_inventory("SKU-001"))["available"] == 5


@pytest.mark.asyncio
async def test_customer_scope_hides_orders_shipments_and_cases(store):
    await expect_error("ORDER_NOT_FOUND", store.get_order("learner-002", "DEMO-001"))
    await expect_error("ORDER_NOT_FOUND", store.get_shipments("learner-002", "DEMO-001"))
    await expect_error(
        "ORDER_NOT_FOUND", store.prepare("learner-002", "t", "refund", "DEMO-001", "x", "1.00")
    )
    aid = await approved(store, kind="logistics", amount=None)
    result = await store.execute(aid)
    cid = result["result"]["case_id"]
    await expect_error("CASE_NOT_FOUND", store.get_case("learner-002", cid))
    assert await store.list_cases("learner-002") == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "amount", ["0", "-1", "199.01", "NaN", "Infinity", "1.001", True, {}, "1e2"]
)
async def test_invalid_refund_amount_cannot_create_action(store, amount):
    await expect_error(
        "INVALID_AMOUNT" if amount != "199.01" else "REFUND_LIMIT",
        store.prepare("learner-001", "invalid", "refund", "DEMO-001", "x", amount),
    )
    assert await store.list_pending() == []


@pytest.mark.asyncio
async def test_prepare_idempotency_immutable_payload_and_version(store):
    a = await store.prepare("learner-001", "same", "refund", "DEMO-001", "x", "10.00")
    assert await store.prepare("learner-001", "same", "refund", "DEMO-001", "x", "10") == a
    assert a["status"] == "waiting_approval" and a["version"] == 1
    assert a["amount"] == "10.00" and a["approver"] is None
    await expect_error(
        "REQUEST_CONFLICT",
        store.prepare("learner-001", "same", "refund", "DEMO-001", "changed", "10"),
    )
    await expect_error("VERSION_CONFLICT", store.decide(a["action_id"], True, 2, "reviewer"))
    await expect_error("NOT_APPROVED", store.execute(a["action_id"]))
    assert len(await store.list_pending()) == 1


@pytest.mark.asyncio
async def test_decision_binding_and_rejection_are_permanent(store):
    a = await store.prepare("learner-001", "reject", "logistics", "DEMO-001", "x")
    aid = a["action_id"]
    rejected = await store.decide(aid, False, 1, "reviewer")
    assert rejected == await store.decide(aid, False, 1, "reviewer")
    await expect_error("DECISION_CONFLICT", store.decide(aid, True, 1, "reviewer"))
    await expect_error("DECISION_CONFLICT", store.decide(aid, False, 1, "other"))
    await expect_error("NOT_APPROVED", store.execute(aid))
    assert await store.list_pending() == []
    assert await store.list_cases(None) == []


@pytest.mark.asyncio
async def test_refund_commit_response_loss_replay_is_one_business_effect(store):
    from aftersales_delivery.db import refunds

    aid = await approved(store, amount="0.10")
    done = await store.execute(aid)
    assert done["status"] == "completed" and done["result"]["refund_id"]
    assert (await store.get_order("learner-001", "DEMO-001"))["refunded_amount"] == "0.10"
    from aftersales_delivery.business import Store

    restarted = Store(store.engine)
    assert await restarted.execute(aid) == done
    assert await restarted.decide(aid, True, 1, "reviewer") == done
    async with store.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(refunds)) == 1
    assert len(await store.list_cases(None)) == 1
    assert [e["event_type"] for e in await store.get_events(aid)] == [
        "prepare",
        "decision",
        "execute",
    ]


@pytest.mark.asyncio
async def test_two_preapproved_refunds_cannot_exceed_paid_amount(store):
    first = await approved(store, "r1", amount="150")
    second = await approved(store, "r2", amount="150")
    from aftersales_delivery.business import BusinessError, Store

    results = await asyncio.gather(
        Store(store.engine).execute(first),
        Store(store.engine).execute(second),
        return_exceptions=True,
    )
    assert sum(isinstance(x, dict) and x["status"] == "completed" for x in results) == 1
    assert sum(isinstance(x, BusinessError) and x.code == "REFUND_LIMIT" for x in results) == 1
    assert (await store.get_order("learner-001", "DEMO-001"))["refunded_amount"] == "150.00"
    failed = next(a for a in await store.list_actions(None) if a["status"] == "failed")
    assert failed["approver"] == "reviewer" and failed["result"]["error"] == "REFUND_LIMIT"


@pytest.mark.asyncio
async def test_same_action_concurrent_execution_creates_one_case(store):
    aid = await approved(store, kind="logistics", amount=None)
    results = await asyncio.gather(*(store.execute(aid) for _ in range(5)))
    assert all(r == results[0] for r in results)
    assert len(await store.list_cases(None)) == 1


@pytest.mark.asyncio
async def test_stock_competition_is_atomic_between_orders(store):
    from aftersales_delivery.db import inventory

    async with store.engine.begin() as conn:
        await conn.execute(
            update(inventory).where(inventory.c.sku == "SKU-001").values(available=1)
        )
    a = await approved(store, "s1", "reship", "DEMO-001", None)
    b = await approved(store, "s2", "reship", "DEMO-002", None)
    from aftersales_delivery.business import BusinessError

    results = await asyncio.gather(store.execute(a), store.execute(b), return_exceptions=True)
    assert sum(isinstance(x, dict) for x in results) == 1
    assert sum(isinstance(x, BusinessError) and x.code == "OUT_OF_STOCK" for x in results) == 1
    assert (await store.get_inventory("SKU-001"))["available"] == 0
    assert len(await store.list_cases(None)) == 1


@pytest.mark.asyncio
async def test_reship_requires_shipped_order_and_quantity_limit(store):
    await expect_error(
        "RESHIP_INELIGIBLE", store.prepare("learner-001", "paid", "reship", "DEMO-004", "x")
    )
    await expect_error(
        "RESHIP_LIMIT",
        store.prepare("learner-001", "too-many", "reship", "DEMO-001", "x", quantity=2),
    )
    await expect_error(
        "INVALID_QUANTITY",
        store.prepare("learner-001", "bool", "reship", "DEMO-001", "x", quantity=True),
    )
    aid = await approved(store, kind="reship", amount=None)
    await store.execute(aid)
    await expect_error(
        "RESHIP_LIMIT", store.prepare("learner-001", "again", "reship", "DEMO-001", "x")
    )


@pytest.mark.asyncio
async def test_execution_revalidates_order_state_and_records_failure(store):
    from aftersales_delivery.db import orders

    aid = await approved(store)
    async with store.engine.begin() as conn:
        await conn.execute(
            update(orders).where(orders.c.order_id == "DEMO-001").values(status="cancelled")
        )
    await expect_error("REFUND_INELIGIBLE", store.execute(aid))
    assert (await store.get_action(aid))["status"] == "failed"
    assert await store.list_cases(None) == []


@pytest.mark.asyncio
async def test_case_status_resolution_and_audit(store):
    aid = await approved(store, kind="logistics", amount=None)
    cid = (await store.execute(aid))["result"]["case_id"]
    await expect_error(
        "INVALID_RESOLUTION", store.set_case_status(cid, "resolved", " ", "reviewer")
    )
    case = await store.set_case_status(cid, "resolved", "地址已核实", "reviewer")
    assert case["status"] == "resolved" and case["resolution"] == "地址已核实"
    await expect_error("INVALID_TRANSITION", store.set_case_status(cid, "open", None, "reviewer"))
    assert (await store.get_events(aid))[-1]["event_type"] == "case_update"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes, code",
    [
        ({"kind": []}, "INVALID_KIND"),
        ({"kind": {}}, "INVALID_KIND"),
        ({"description": None}, "INVALID_DESCRIPTION"),
        ({"quantity": 1.5}, "INVALID_QUANTITY"),
        ({"kind": "logistics", "amount": "1.00"}, "INVALID_AMOUNT"),
        ({"thread": "x" * 121}, "INVALID_THREAD"),
    ],
)
async def test_prepare_validates_untrusted_input(store, changes, code):
    payload = dict(
        customer="learner-001",
        thread="validation",
        kind="refund",
        order="DEMO-001",
        description="x",
        amount="1.00",
    )
    payload.update(changes)
    await expect_error(code, store.prepare(**payload))


@pytest.mark.asyncio
async def test_prepare_concurrent_requests_share_one_uuid(store):
    from aftersales_delivery.business import Store

    calls = [
        Store(store.engine).prepare(
            "learner-001", "parallel-prepare", "refund", "DEMO-001", "x", "10"
        )
        for _ in range(5)
    ]
    results = await asyncio.gather(*calls)
    assert len({r["action_id"] for r in results}) == 1
    assert len(await store.get_events()) == 1


@pytest.mark.asyncio
async def test_failed_stock_action_retains_approval_and_can_resume_after_restock(store):
    from aftersales_delivery.db import inventory

    aid = await approved(store, kind="reship", amount=None)
    async with store.engine.begin() as conn:
        await conn.execute(
            update(inventory).where(inventory.c.sku == "SKU-001").values(available=0)
        )
    await expect_error("OUT_OF_STOCK", store.execute(aid))
    failed = await store.get_action(aid)
    assert failed["status"] == "failed" and failed["approver"] == "reviewer"
    async with store.engine.begin() as conn:
        await conn.execute(
            update(inventory).where(inventory.c.sku == "SKU-001").values(available=1)
        )
    done = await store.execute(aid)
    assert done["status"] == "completed" and done["result"]["reship_id"]
    assert len(await store.list_cases(None)) == 1
    assert (await store.get_inventory("SKU-001"))["available"] == 0


@pytest.mark.asyncio
async def test_audit_sequence_preserves_causality_when_clocks_tie(store, monkeypatch):
    from uuid import UUID

    import aftersales_delivery.business as business

    monkeypatch.setattr(business, "_now", lambda: "2026-10-20T00:00:00+00:00")
    # UUID order deliberately opposes execution order, reproducing Windows' tied
    # clock values deterministically rather than relying on a flaky timing race.
    ids = iter(UUID(int=value) for value in range(100, 0, -1))
    monkeypatch.setattr(business, "uuid4", lambda: next(ids))
    aid = await approved(store)
    await store.execute(aid)
    events = await store.get_events(aid)
    assert [event["event_type"] for event in events] == ["prepare", "decision", "execute"]
    assert len({event["created_at"] for event in events}) == 1
    assert [event["sequence"] for event in events] == sorted(event["sequence"] for event in events)
