"""Deterministic platform rules against real transactions, without a model."""

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import create_async_engine

from aftersales_delivery import db
from aftersales_delivery.business import BusinessError, Store


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rules.db'}")
    await db.initialize(engine)
    yield Store(engine)
    await engine.dispose()


async def platform(store):
    assert hasattr(db, "seed_platform"), "platform seeding must be additive"
    await db.seed_platform(store.engine)
    return store


async def error(code, operation):
    with pytest.raises(BusinessError) as exc:
        await operation
    assert exc.value.code == code


async def request(store, kind="return_refund", order="UP-SHIPPED", amount="10.00", thread="r"):
    return await store.prepare("learner-001", thread, kind, order, "包裹商品问题", amount)


@pytest.mark.asyncio
async def test_trusted_identity_is_never_normalized(store):
    await error(
        "ORDER_NOT_FOUND", store.prepare(" learner-001 ", "x", "refund", "DEMO-001", "x", "1")
    )


@pytest.mark.asyncio
async def test_seed_is_additive_and_policies_do_not_change_legacy(store):
    await platform(store)
    a = await request(store, "refund", "UP-PAID")
    await store.decide(a["action_id"], True, 1, "admin", reason="金额核对")
    await store.execute(a["action_id"])
    await db.seed_platform(store.engine)
    assert (await store.get_order("learner-001", "UP-PAID"))["refunded_amount"] == "10.00"
    assert (await store.prepare("learner-001", "legacy", "refund", "DEMO-001", "x", "1"))[
        "status"
    ] == "waiting_approval"


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["UP-SHIPPED", "UP-DELIVERED"])
async def test_shipped_orders_require_return(store, order):
    await platform(store)
    await error("RETURN_REQUIRED", request(store, "refund", order))
    assert await store.list_actions(None) == []


@pytest.mark.asyncio
async def test_return_requires_approval_then_receipt_and_refunds_exactly_once(store):
    await platform(store)
    a = await request(store)
    aid = a["action_id"]
    ticket = await store.get_ticket("learner-001", aid)
    assert ticket["status"] == "waiting_approval"
    await error("RETURN_NOT_AUTHORIZED", store.receive_return(aid, "admin"))
    await store.decide(aid, True, 1, "admin", "同意退货")
    waiting = await store.execute(aid)
    assert waiting["status"] == "awaiting_return"
    assert await store.execute(aid) == waiting
    assert (await store.get_order("learner-001", "UP-SHIPPED"))["refunded_amount"] == "0.00"
    async with store.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(db.refunds)) == 0
    received = await asyncio.gather(*(store.receive_return(aid, "admin") for _ in range(3)))
    assert all(a == received[0] for a in received)
    assert received[0]["status"] == "completed"
    assert (await store.get_order("learner-001", "UP-SHIPPED"))["refunded_amount"] == "10.00"
    assert (await store.get_ticket("learner-001", aid))["status"] == "resolved"
    async with store.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(db.refunds)) == 1
    await error("ACTION_NOT_CANCELLABLE", store.cancel_action("learner-001", aid))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order,kind,amount,code",
    [
        ("UP-EXPIRED", "refund", "1", "AFTERSALES_EXPIRED"),
        ("UP-CANCELLED", "refund", "1", "ORDER_CANCELLED"),
        ("UP-PAID", "return_refund", "1", "RETURN_INELIGIBLE"),
        ("UP-PAID", "refund", "99999", "REFUND_LIMIT"),
        ("UP-PAID", "reship", None, "RESHIP_INELIGIBLE"),
    ],
)
async def test_strict_rule_rejections_leave_no_ticket_or_effect(store, order, kind, amount, code):
    await platform(store)
    await error(code, request(store, kind, order, amount))
    assert await store.list_tickets(None) == []


@pytest.mark.asyncio
async def test_high_risk_is_visible_and_never_self_approved(store):
    await platform(store)
    a = await request(store, "refund", "UP-RISK", "10")
    assert a["manual_review"] is True and a["risk_level"] == "high"
    await error("NOT_APPROVED", store.execute(a["action_id"]))
    assert (await store.get_ticket("learner-001", a["action_id"]))["manual_review"] is True
    await store.decide(a["action_id"], True, 1, "admin", "人工风险核验")
    assert (await store.execute(a["action_id"]))["status"] == "completed"


@pytest.mark.asyncio
async def test_snapshot_tampering_is_rejected_without_funds(store):
    await platform(store)
    a = await request(store, "refund", "UP-PAID")
    aid = a["action_id"]
    await store.decide(aid, True, 1, "admin", "核验")
    async with store.engine.begin() as conn:
        await conn.execute(
            update(db.actions).where(db.actions.c.action_id == aid).values(amount_cents=2000)
        )
    await error("APPROVAL_SNAPSHOT_MISMATCH", store.execute(aid))
    assert (await store.get_order("learner-001", "UP-PAID"))["refunded_amount"] == "0.00"
    reviews = (await store.get_action(aid))["approval_review"]
    assert reviews["reason"] == "核验" and reviews["snapshot"]["amount_cents"] == 1000


@pytest.mark.asyncio
async def test_cancel_is_owner_scoped_and_permanent(store):
    await platform(store)
    a = await request(store)
    aid = a["action_id"]
    await error("ACTION_NOT_FOUND", store.cancel_action("learner-002", aid))
    cancelled = await store.cancel_action("learner-001", aid)
    assert cancelled["status"] == "cancelled"
    assert await store.cancel_action("learner-001", aid) == cancelled
    await error("DECISION_CONFLICT", store.decide(aid, True, 1, "admin"))
    await error("NOT_APPROVED", store.execute(aid))
    await error("TICKET_NOT_FOUND", store.get_ticket("learner-002", aid))
    assert await store.list_tickets("learner-002") == []


@pytest.mark.asyncio
async def test_rules_revalidated_after_approval_and_on_receipt(store):
    await platform(store)
    a = await request(store)
    await store.decide(a["action_id"], True, 1, "admin")
    await store.execute(a["action_id"])
    async with store.engine.begin() as conn:
        await conn.execute(
            update(db.orders).where(db.orders.c.order_id == "UP-SHIPPED").values(status="cancelled")
        )
    await error("ORDER_CANCELLED", store.receive_return(a["action_id"], "admin"))
    assert (await store.get_order("learner-001", "UP-SHIPPED"))["refunded_amount"] == "0.00"


@pytest.mark.asyncio
async def test_cancel_order_requires_approval_and_paid_state(store):
    await platform(store)
    a = await request(store, "cancel_order", "UP-PAID", None)
    assert (await store.get_order("learner-001", "UP-PAID"))["status"] == "paid"
    await store.decide(a["action_id"], True, 1, "admin")
    done = await store.execute(a["action_id"])
    assert done["status"] == "completed"
    order = await store.get_order("learner-001", "UP-PAID")
    assert order["status"] == "cancelled" and order["refunded_amount"] == "199.00"


@pytest.mark.asyncio
async def test_logistics_reship_tickets_and_closed_case_cannot_reopen(store):
    await platform(store)
    a = await request(store, "reship", amount=None)
    await store.decide(a["action_id"], True, 1, "admin")
    case = (await store.execute(a["action_id"]))["result"]["case_id"]
    await store.set_case_status(case, "closed", "补发已送达", "admin")
    await error("INVALID_TRANSITION", store.set_case_status(case, "open", None, "admin"))
    assert (await store.get_ticket("learner-001", a["action_id"]))["status"] == "closed"
    overview = await store.overview()
    assert overview["counts"]["tickets"] == 1


@pytest.mark.asyncio
async def test_cancel_order_request_replay_after_execution_uses_frozen_amount(store):
    await platform(store)
    a = await request(store, "cancel_order", "UP-PAID", None)
    await store.decide(a["action_id"], True, 1, "admin")
    done = await store.execute(a["action_id"])
    assert await request(store, "cancel_order", "UP-PAID", None) == done


@pytest.mark.asyncio
async def test_receipt_uses_its_bound_case_even_if_mutable_result_is_corrupted(store):
    await platform(store)
    a = await request(store)
    await store.decide(a["action_id"], True, 1, "admin")
    waiting = await store.execute(a["action_id"])
    other = await request(store, "logistics", "UP-DELIVERED", None, "other")
    await store.decide(other["action_id"], True, 1, "admin")
    other_case = (await store.execute(other["action_id"]))["result"]["case_id"]
    async with store.engine.begin() as conn:
        corrupt = {**waiting["result"], "case_id": other_case}
        await conn.execute(
            update(db.actions)
            .where(db.actions.c.action_id == a["action_id"])
            .values(result=corrupt)
        )
    done = await store.receive_return(a["action_id"], "admin")
    assert done["result"]["case_id"] == waiting["result"]["case_id"]
    assert (await store.get_case(None, other_case))["status"] == "open"


@pytest.mark.asyncio
async def test_profile_only_stores_nonsensitive_identity(store):
    profile = await store.sync_profile("learner-001", "客户甲", "customer")
    assert profile["display_name"] == "客户甲"
    assert set(profile) == {"customer_id", "display_name", "role", "created_at"}
    assert await store.get_profile("learner-001") == profile


@pytest.mark.asyncio
async def test_approved_return_cancellation_and_receipt_are_serialized(store):
    await platform(store)
    a = await request(store)
    aid = a["action_id"]
    await store.decide(aid, True, 1, "admin")
    await store.execute(aid)
    outcomes = await asyncio.gather(
        store.cancel_action("learner-001", aid),
        store.receive_return(aid, "admin"),
        return_exceptions=True,
    )
    assert sum(isinstance(x, dict) for x in outcomes) == 1
    final = await store.get_action(aid)
    money = (await store.get_order("learner-001", "UP-SHIPPED"))["refunded_amount"]
    assert (final["status"], money) in {("completed", "10.00"), ("cancelled", "0.00")}


@pytest.mark.asyncio
async def test_write_failure_rolls_back_every_business_effect_but_keeps_approval(
    store, monkeypatch
):
    await platform(store)
    a = await request(store, "refund", "UP-PAID")
    await store.decide(a["action_id"], True, 1, "admin")
    original = store._event

    async def fail(conn, action_id, kind, actor, details):
        if kind == "execute":
            raise RuntimeError("injected audit write failure")
        return await original(conn, action_id, kind, actor, details)

    monkeypatch.setattr(store, "_event", fail)
    with pytest.raises(RuntimeError, match="injected"):
        await store.execute(a["action_id"])
    assert (await store.get_order("learner-001", "UP-PAID"))["refunded_amount"] == "0.00"
    assert (await store.get_action(a["action_id"]))["status"] == "approved"
    assert await store.list_cases(None) == []
    async with store.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(db.refunds)) == 0


@pytest.mark.asyncio
async def test_approval_reason_is_immutable_and_rule_window_rechecked(store):
    await platform(store)
    a = await request(store, "refund", "UP-PAID")
    aid = a["action_id"]
    approved = await store.decide(aid, True, 1, "admin", "核验原件")
    assert await store.decide(aid, True, 1, "admin", "核验原件") == approved
    await error("DECISION_CONFLICT", store.decide(aid, True, 1, "admin", "更改原因"))
    async with store.engine.begin() as conn:
        await conn.execute(
            update(db.order_policies)
            .where(db.order_policies.c.order_id == "UP-PAID")
            .values(aftersales_until="2000-01-01T00:00:00+00:00")
        )
    await error("AFTERSALES_EXPIRED", store.execute(aid))
    assert (await store.get_action(aid))["status"] == "failed"


@pytest.mark.asyncio
async def test_high_amount_escalates_even_with_low_order_risk(store):
    await platform(store)
    async with store.engine.begin() as conn:
        await conn.execute(
            update(db.orders).where(db.orders.c.order_id == "UP-PAID").values(paid_cents=100000)
        )
    a = await request(store, "refund", "UP-PAID", "500")
    assert a["risk_level"] == "high" and a["manual_review"] is True
    await error("NOT_APPROVED", store.execute(a["action_id"]))


@pytest.mark.asyncio
async def test_order_read_exposes_strict_policy_and_preserves_legacy_contract(store):
    await platform(store)
    assert "policy" not in await store.get_order("learner-001", "DEMO-001")
    assert (await store.get_order("learner-001", "UP-SHIPPED"))["policy"]["risk_level"] == "low"
