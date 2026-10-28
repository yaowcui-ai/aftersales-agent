"""Simulated aftersales transactions, independent of model instructions.

Refunds require a paid/shipped/delivered order and cannot exceed money paid.
Reships require shipped/delivered orders, remaining order quantity and stock.
All writes require an immutable, persisted approval; no external payment occurs.
"""

import hashlib
import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import LargeBinary, cast, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from aftersales_delivery.db import (
    actions,
    approval_reviews,
    cases,
    events,
    inventory,
    order_policies,
    orders,
    refunds,
    reships,
    shipments,
    task_index,
    tickets,
    user_profiles,
)
from aftersales_delivery.policies import risk, violation


class BusinessError(Exception):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def _now():
    return datetime.now(UTC).isoformat()


def _text(value, name, limit=80):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise BusinessError(f"INVALID_{name.upper()}")
    return value.strip()


def _money(cents):
    return f"{cents // 100}.{cents % 100:02d}" if cents is not None else None


def _cents(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise BusinessError("INVALID_AMOUNT")
    raw = str(value)
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", raw) or len(raw) > 16:
        raise BusinessError("INVALID_AMOUNT")
    try:
        amount = Decimal(raw) * 100
        if amount <= 0 or amount > 2_000_000_000:
            raise BusinessError("INVALID_AMOUNT")
        return int(amount)
    except InvalidOperation as exc:
        raise BusinessError("INVALID_AMOUNT") from exc


def _action(row):
    data = dict(row)
    data["amount"] = _money(data.pop("amount_cents"))
    return data


def _order(row):
    data = dict(row)
    data["paid_amount"] = _money(data.pop("paid_cents"))
    data["refunded_amount"] = _money(data.pop("refunded_cents"))
    return data


def _eligible(order, kind, amount, quantity, stock=None, policy=None, receipt=False):
    code = violation(order, policy, kind, amount, receipt=receipt)
    if code:
        raise BusinessError(code)
    if kind in {"refund", "return_refund", "cancel_order"}:
        if kind == "cancel_order" and order["status"] != "paid":
            raise BusinessError("CANCEL_INELIGIBLE")
        if order["status"] not in {"paid", "shipped", "delivered"}:
            raise BusinessError("REFUND_INELIGIBLE")
        if amount > order["paid_cents"] - order["refunded_cents"]:
            raise BusinessError("REFUND_LIMIT")
    elif kind == "reship":
        if (
            order["status"] not in {"shipped", "delivered"}
            or order["refunded_cents"] == order["paid_cents"]
        ):
            raise BusinessError("RESHIP_INELIGIBLE")
        if quantity > order["quantity"] - order["reshipped_quantity"]:
            raise BusinessError("RESHIP_LIMIT")
        if stock is None or stock["available"] < quantity:
            raise BusinessError("OUT_OF_STOCK")


class Store:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    @asynccontextmanager
    async def _write(self):
        # SQLite lacks row locks: BEGIN IMMEDIATE serializes writers across processes.
        async with self.engine.connect() as conn:
            if conn.dialect.name == "sqlite":
                await conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                await conn.begin()
            try:
                yield conn
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def _order_row(self, conn, customer, order, lock=False):
        query = select(orders).where(orders.c.order_id == order)
        row = (await conn.execute(query.with_for_update() if lock else query)).mappings().first()
        # Explicit Python equality also prevents MySQL case-insensitive collations
        # from treating a differently cased customer/order identifier as authorized.
        if not row or row["customer_id"] != customer or row["order_id"] != order:
            raise BusinessError("ORDER_NOT_FOUND")
        return row

    async def _action_row(self, conn, action_id, lock=False):
        query = select(actions).where(actions.c.action_id == action_id)
        row = (await conn.execute(query.with_for_update() if lock else query)).mappings().first()
        if row is None or row["action_id"] != action_id:
            raise BusinessError("ACTION_NOT_FOUND")
        return row

    async def _event(self, conn, action_id, kind, actor, details):
        await conn.execute(
            insert(events).values(
                event_id=str(uuid4()),
                action_id=action_id,
                event_type=kind,
                actor=actor,
                details=details,
                created_at=_now(),
            )
        )

    async def _policy(self, conn, order_id):
        row = (
            (
                await conn.execute(
                    select(order_policies)
                    .where(order_policies.c.order_id == order_id)
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        return row if row and row["order_id"] == order_id else None

    async def _present(self, conn, row):
        data = _action(row)
        ticket = (
            (await conn.execute(select(tickets).where(tickets.c.action_id == row["action_id"])))
            .mappings()
            .first()
        )
        if ticket:
            data.update(
                ticket_id=ticket["ticket_id"],
                risk_level=ticket["risk_level"],
                manual_review=bool(ticket["manual_review"]),
            )
        review = (
            (
                await conn.execute(
                    select(approval_reviews).where(approval_reviews.c.action_id == row["action_id"])
                )
            )
            .mappings()
            .first()
        )
        data["approval_review"] = dict(review) if review else None
        data["return_status"] = row["status"] if row["kind"] == "return_refund" else None
        return data

    async def _ticket_status(self, conn, action_id, status, case_id=None):
        values = dict(status=status, updated_at=_now())
        if case_id is not None:
            values["case_id"] = case_id
        await conn.execute(update(tickets).where(tickets.c.action_id == action_id).values(**values))

    @staticmethod
    def _snapshot(action, sku):
        fields = (
            "action_id",
            "customer_id",
            "thread_id",
            "kind",
            "order_id",
            "description",
            "amount_cents",
            "quantity",
            "version",
            "created_at",
        )
        return {**{name: action[name] for name in fields}, "sku": sku}

    @staticmethod
    def _digest(snapshot):
        return hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    async def _verify_approval(self, conn, action):
        review = (
            (
                await conn.execute(
                    select(approval_reviews).where(
                        approval_reviews.c.action_id == action["action_id"]
                    )
                )
            )
            .mappings()
            .first()
        )
        if not review or review["decision"] != "approved" or review["actor"] != action["approver"]:
            raise BusinessError("APPROVAL_SNAPSHOT_MISMATCH")
        order = await self._order_row(conn, action["customer_id"], action["order_id"], lock=True)
        snapshot = self._snapshot(action, order["sku"])
        if (
            review["object_id"] != action["order_id"]
            or review["version"] != action["version"]
            or self._digest(snapshot) != review["snapshot_hash"]
            or self._digest(review["snapshot"]) != review["snapshot_hash"]
        ):
            raise BusinessError("APPROVAL_SNAPSHOT_MISMATCH")

    async def list_orders(self, customer, limit=20, offset=0):
        """Count and page only the exact trusted customer's orders; never write."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise BusinessError("INVALID_LIMIT")
        if type(offset) is not int or not 0 <= offset <= 2_000_000_000:
            raise BusinessError("INVALID_OFFSET")
        # Validate without normalizing a trusted identity into another account.
        _text(customer, "customer")
        async with self.engine.connect() as conn:
            # Retain the indexed equality and reject case/trailing-space aliases.
            owner = (orders.c.customer_id == customer) & (
                cast(orders.c.customer_id, LargeBinary) == customer.encode("utf-8")
            )
            total = await conn.scalar(select(func.count()).select_from(orders).where(owner))
            rows = (
                (
                    await conn.execute(
                        select(orders)
                        .where(owner)
                        .order_by(orders.c.order_id)
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .mappings()
                .all()
            )
        has_more = offset + len(rows) < total
        return {
            "total": total,
            "orders": [_order(row) for row in rows],
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "next_offset": offset + len(rows) if has_more else None,
        }

    async def get_order(self, customer, order):
        async with self.engine.connect() as conn:
            data = _order(await self._order_row(conn, customer, order))
            policy = (
                (
                    await conn.execute(
                        select(order_policies).where(order_policies.c.order_id == order)
                    )
                )
                .mappings()
                .first()
            )
            if policy and policy["order_id"] == order:
                data["policy"] = dict(policy)
            return data

    async def get_shipments(self, customer, order):
        async with self.engine.connect() as conn:
            await self._order_row(conn, customer, order)
            rows = (
                await conn.execute(
                    select(shipments)
                    .where(shipments.c.order_id == order)
                    .order_by(shipments.c.updated_at, shipments.c.shipment_id)
                )
            ).mappings()
            return [dict(row) for row in rows]

    async def get_inventory(self, sku):
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(select(inventory).where(inventory.c.sku == sku)))
                .mappings()
                .first()
            )
            if row is None or row["sku"] != sku:
                raise BusinessError("INVENTORY_NOT_FOUND")
            return dict(row)

    async def get_case(self, customer, case):
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(select(cases).where(cases.c.case_id == case)))
                .mappings()
                .first()
            )
            if (
                row is None
                or row["case_id"] != case
                or (customer is not None and row["customer_id"] != customer)
            ):
                raise BusinessError("CASE_NOT_FOUND")
            return dict(row)

    async def list_cases(self, customer_or_none):
        async with self.engine.connect() as conn:
            query = select(cases).order_by(cases.c.created_at, cases.c.case_id)
            if customer_or_none is not None:
                query = query.where(cases.c.customer_id == customer_or_none)
            rows = (await conn.execute(query)).mappings()
            return [
                dict(r)
                for r in rows
                if customer_or_none is None or r["customer_id"] == customer_or_none
            ]

    async def prepare(self, customer, thread, kind, order, description, amount=None, quantity=1):
        _text(customer, "customer")
        thread = _text(thread, "thread", 120)
        order = _text(order, "order")
        description = _text(description, "description", 4000)
        if not isinstance(kind, str) or kind not in {
            "logistics",
            "refund",
            "reship",
            "return_refund",
            "cancel_order",
        }:
            raise BusinessError("INVALID_KIND")
        if type(quantity) is not int or quantity <= 0 or quantity > 1000:
            raise BusinessError("INVALID_QUANTITY")
        cents = _cents(amount) if kind in {"refund", "return_refund"} else None
        if kind not in {"refund", "return_refund"} and amount is not None:
            raise BusinessError("INVALID_AMOUNT")
        if kind != "reship" and quantity != 1:
            raise BusinessError("INVALID_QUANTITY")
        payload = dict(
            customer_id=customer,
            thread_id=thread,
            kind=kind,
            order_id=order,
            description=description,
            amount_cents=cents,
            quantity=quantity,
        )
        try:
            async with self._write() as conn:
                # Lock the order first; the next consistent read starts after that
                # lock was acquired. Never gap-lock a missing action: different-order
                # prepares would deadlock on MySQL. The unique key resolves that race.
                row = await self._order_row(conn, customer, order, lock=True)
                previous = (
                    (
                        await conn.execute(
                            select(actions).where(
                                actions.c.customer_id == customer, actions.c.thread_id == thread
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if previous:
                    return await self._same_request(conn, previous, payload)
                if kind == "cancel_order":
                    cents = row["paid_cents"] - row["refunded_cents"]
                    if cents <= 0:
                        raise BusinessError("REFUND_LIMIT")
                    payload["amount_cents"] = cents
                stock = None
                if kind == "reship":
                    stock = (
                        (
                            await conn.execute(
                                select(inventory)
                                .where(inventory.c.sku == row["sku"])
                                .with_for_update()
                            )
                        )
                        .mappings()
                        .first()
                    )
                policy = await self._policy(conn, order)
                _eligible(row, kind, cents, quantity, stock, policy=policy)
                risk_level, manual_review = risk(policy, cents)
                action_id = str(uuid4())
                await conn.execute(
                    insert(actions).values(
                        **payload,
                        action_id=action_id,
                        version=1,
                        status="waiting_approval",
                        result=None,
                        approver=None,
                        created_at=_now(),
                    )
                )
                await self._event(
                    conn, action_id, "prepare", customer, {"kind": kind, "order_id": order}
                )
                await conn.execute(
                    insert(tickets).values(
                        ticket_id=action_id,
                        action_id=action_id,
                        customer_id=customer,
                        order_id=order,
                        kind=kind,
                        description=description,
                        status="waiting_approval",
                        risk_level=risk_level,
                        manual_review=int(manual_review),
                        created_at=_now(),
                        updated_at=_now(),
                    )
                )
                return await self._present(conn, await self._action_row(conn, action_id))
        except IntegrityError:
            # A different process may have inserted the same unique request.
            async with self.engine.connect() as conn:
                previous = (
                    (
                        await conn.execute(
                            select(actions).where(
                                actions.c.customer_id == customer, actions.c.thread_id == thread
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if previous is None:
                    raise
                return await self._same_request(conn, previous, payload)

    async def _same_request(self, conn, previous, payload):
        if any(
            previous[k] != v
            for k, v in payload.items()
            if not (k == "amount_cents" and payload["kind"] == "cancel_order")
        ):
            raise BusinessError("REQUEST_CONFLICT")
        return await self._present(conn, previous)

    async def get_action(self, action_id):
        async with self.engine.connect() as conn:
            return await self._present(conn, await self._action_row(conn, action_id))

    async def list_actions(self, customer_id_or_none):
        async with self.engine.connect() as conn:
            query = select(actions).order_by(actions.c.created_at, actions.c.action_id)
            if customer_id_or_none is not None:
                query = query.where(actions.c.customer_id == customer_id_or_none)
            rows = (await conn.execute(query)).mappings()
            return [
                await self._present(conn, r)
                for r in rows
                if customer_id_or_none is None or r["customer_id"] == customer_id_or_none
            ]

    async def list_pending(self):
        async with self.engine.connect() as conn:
            rows = (
                await conn.execute(
                    select(actions)
                    .where(actions.c.status == "waiting_approval")
                    .order_by(actions.c.created_at, actions.c.action_id)
                )
            ).mappings()
            return [await self._present(conn, row) for row in rows]

    async def decide(self, action_id, approved, version, actor, reason=""):
        if type(approved) is not bool:
            raise BusinessError("INVALID_DECISION")
        actor = _text(actor, "actor")
        if not isinstance(reason, str) or len(reason) > 4000:
            raise BusinessError("INVALID_REASON")
        reason = reason.strip()
        if type(version) is not int:
            raise BusinessError("VERSION_CONFLICT")
        async with self._write() as conn:
            row = await self._action_row(conn, action_id, lock=True)
            if version != row["version"]:
                raise BusinessError("VERSION_CONFLICT")
            if row["status"] != "waiting_approval":
                previous = (
                    (
                        await conn.execute(
                            select(approval_reviews).where(
                                approval_reviews.c.action_id == action_id
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if (
                    not previous
                    or previous["decision"] != ("approved" if approved else "rejected")
                    or row["approver"] != actor
                    or previous["reason"] != reason
                    or row["status"] == "cancelled"
                ):
                    raise BusinessError("DECISION_CONFLICT")
                return await self._present(conn, row)
            order = await self._order_row(conn, row["customer_id"], row["order_id"], lock=True)
            snapshot = self._snapshot(row, order["sku"])
            await conn.execute(
                insert(approval_reviews).values(
                    action_id=action_id,
                    object_type="order",
                    object_id=row["order_id"],
                    version=version,
                    decision="approved" if approved else "rejected",
                    actor=actor,
                    reason=reason,
                    snapshot=snapshot,
                    snapshot_hash=self._digest(snapshot),
                    created_at=_now(),
                )
            )
            await conn.execute(
                update(actions)
                .where(actions.c.action_id == action_id)
                .values(status="approved" if approved else "rejected", approver=actor)
            )
            await self._event(
                conn,
                action_id,
                "decision",
                actor,
                {
                    "approved": approved,
                    "version": version,
                    "reason": reason,
                    "snapshot_hash": self._digest(snapshot),
                    "object_id": row["order_id"],
                },
            )
            await self._ticket_status(conn, action_id, "approved" if approved else "rejected")
            return await self._present(conn, await self._action_row(conn, action_id))

    async def execute(self, action_id):
        failure = None
        async with self._write() as conn:
            action = await self._action_row(conn, action_id, lock=True)
            if action["status"] in {"completed", "awaiting_return"}:
                return await self._present(conn, action)
            if action["status"] not in {"approved", "failed"} or not action["approver"]:
                raise BusinessError("NOT_APPROVED")
            try:
                # Business rejection rolls back every partial business effect while
                # retaining the immutable approval and a durable failed result.
                async with conn.begin_nested():
                    await self._verify_approval(conn, action)
                    result = await self._perform(conn, action)
            except BusinessError as exc:
                failure = exc
                result = {"error": exc.code, "message": str(exc)}
            await conn.execute(
                update(actions)
                .where(actions.c.action_id == action_id)
                .values(
                    status="failed"
                    if failure
                    else ("awaiting_return" if action["kind"] == "return_refund" else "completed"),
                    result=result,
                )
            )
            await self._ticket_status(
                conn,
                action_id,
                "failed" if failure else result["case_status"],
                result.get("case_id"),
            )
            await self._event(conn, action_id, "execute", action["approver"], result)
            snapshot = await self._present(conn, await self._action_row(conn, action_id))
        if failure:
            raise failure
        return snapshot

    async def _perform(self, conn, action, receipt=False):
        order = await self._order_row(conn, action["customer_id"], action["order_id"], lock=True)
        kind, amount, quantity = action["kind"], action["amount_cents"], action["quantity"]
        stock = None
        if kind == "reship":
            stock = (
                (
                    await conn.execute(
                        select(inventory).where(inventory.c.sku == order["sku"]).with_for_update()
                    )
                )
                .mappings()
                .first()
            )
        policy = await self._policy(conn, order["order_id"])
        _eligible(order, kind, amount, quantity, stock, policy=policy, receipt=receipt)
        if receipt:
            case = (
                (
                    await conn.execute(
                        select(cases)
                        .where(cases.c.action_id == action["action_id"])
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if (
                not case
                or case["customer_id"] != action["customer_id"]
                or case["order_id"] != action["order_id"]
                or case["kind"] != "return_refund"
                or case["status"] != "awaiting_return"
            ):
                raise BusinessError("RETURN_NOT_AUTHORIZED")
            case_id = case["case_id"]
        else:
            case_id = str(uuid4())
        now = _now()
        result = {
            "case_id": case_id,
            "kind": kind,
            "order_id": order["order_id"],
            "simulated": True,
        }
        if kind in {"refund", "cancel_order"} or (kind == "return_refund" and receipt):
            changed = await conn.execute(
                update(orders)
                .where(
                    orders.c.order_id == order["order_id"],
                    orders.c.refunded_cents + amount <= orders.c.paid_cents,
                )
                .values(refunded_cents=orders.c.refunded_cents + amount)
            )
            if changed.rowcount != 1:
                raise BusinessError("REFUND_LIMIT")
            refund_id = str(uuid4())
            await conn.execute(
                insert(refunds).values(
                    refund_id=refund_id,
                    action_id=action["action_id"],
                    order_id=order["order_id"],
                    amount_cents=amount,
                    created_at=now,
                )
            )
            result.update(refund_id=refund_id, amount=_money(amount))
            if kind == "cancel_order":
                await conn.execute(
                    update(orders)
                    .where(orders.c.order_id == order["order_id"])
                    .values(status="cancelled")
                )
        elif kind == "reship":
            changed = await conn.execute(
                update(inventory)
                .where(inventory.c.sku == order["sku"], inventory.c.available >= quantity)
                .values(available=inventory.c.available - quantity)
            )
            if changed.rowcount != 1:
                raise BusinessError("OUT_OF_STOCK")
            changed = await conn.execute(
                update(orders)
                .where(
                    orders.c.order_id == order["order_id"],
                    orders.c.reshipped_quantity + quantity <= orders.c.quantity,
                )
                .values(reshipped_quantity=orders.c.reshipped_quantity + quantity)
            )
            if changed.rowcount != 1:
                raise BusinessError("RESHIP_LIMIT")
            reship_id = str(uuid4())
            await conn.execute(
                insert(reships).values(
                    reship_id=reship_id,
                    action_id=action["action_id"],
                    order_id=order["order_id"],
                    sku=order["sku"],
                    quantity=quantity,
                    created_at=now,
                )
            )
            result.update(reship_id=reship_id, sku=order["sku"], quantity=quantity)
        status = (
            "resolved"
            if kind in {"refund", "cancel_order"} or receipt
            else "awaiting_return"
            if kind == "return_refund"
            else "open"
        )
        if receipt:
            await conn.execute(
                update(cases)
                .where(cases.c.case_id == case_id)
                .values(status=status, resolution="模拟退货收货及退款已完成", updated_at=now)
            )
        else:
            await conn.execute(
                insert(cases).values(
                    case_id=case_id,
                    action_id=action["action_id"],
                    customer_id=action["customer_id"],
                    order_id=order["order_id"],
                    kind=kind,
                    description=action["description"],
                    status=status,
                    resolution="模拟退款已完成" if status == "resolved" else None,
                    created_at=now,
                    updated_at=now,
                )
            )
        result["case_status"] = status
        return result

    async def set_case_status(self, case_id, status, resolution, actor):
        actor = _text(actor, "actor")
        transitions = {
            "open": {"open", "processing", "in_progress", "resolved", "closed"},
            "processing": {"processing", "in_progress", "resolved", "closed"},
            "in_progress": {"in_progress", "processing", "resolved", "closed"},
            "resolved": {"resolved", "closed"},
            "closed": {"closed"},
            "awaiting_return": {"awaiting_return"},
        }
        if status not in transitions:
            raise BusinessError("INVALID_STATUS")
        if status in {"resolved", "closed"}:
            resolution = _text(resolution, "resolution", 4000)
        elif resolution is not None:
            resolution = _text(resolution, "resolution", 4000)
        async with self._write() as conn:
            # All writers lock action before case. Receipt/cancellation already use
            # this order; reversing it here can deadlock on MySQL FK checks.
            owner = (
                (await conn.execute(select(cases).where(cases.c.case_id == case_id)))
                .mappings()
                .first()
            )
            if owner is None or owner["case_id"] != case_id:
                raise BusinessError("CASE_NOT_FOUND")
            await self._action_row(conn, owner["action_id"], lock=True)
            row = (
                (
                    await conn.execute(
                        select(cases).where(cases.c.case_id == case_id).with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise BusinessError("CASE_NOT_FOUND")
            if status not in transitions[row["status"]]:
                raise BusinessError("INVALID_TRANSITION")
            await conn.execute(
                update(cases)
                .where(cases.c.case_id == case_id)
                .values(status=status, resolution=resolution, updated_at=_now())
            )
            await self._event(
                conn,
                row["action_id"],
                "case_update",
                actor,
                {"case_id": case_id, "status": status, "resolution": resolution},
            )
            await self._ticket_status(conn, row["action_id"], status, case_id)
            return dict(
                (await conn.execute(select(cases).where(cases.c.case_id == case_id)))
                .mappings()
                .one()
            )

    async def cancel_action(self, customer, action_id):
        _text(customer, "customer")
        async with self._write() as conn:
            row = await self._action_row(conn, action_id, lock=True)
            if row["customer_id"] != customer:
                raise BusinessError("ACTION_NOT_FOUND")
            if row["status"] == "cancelled":
                return await self._present(conn, row)
            if row["status"] not in {"waiting_approval", "approved", "failed", "awaiting_return"}:
                raise BusinessError("ACTION_NOT_CANCELLABLE")
            await conn.execute(
                update(actions).where(actions.c.action_id == action_id).values(status="cancelled")
            )
            await self._ticket_status(conn, action_id, "cancelled")
            await conn.execute(
                update(cases)
                .where(cases.c.action_id == action_id)
                .values(status="closed", resolution="客户取消售后申请", updated_at=_now())
            )
            await self._event(conn, action_id, "cancel", customer, {"status": "cancelled"})
            return await self._present(conn, await self._action_row(conn, action_id))

    async def receive_return(self, action_id, actor):
        actor = _text(actor, "actor")
        async with self._write() as conn:
            row = await self._action_row(conn, action_id, lock=True)
            if row["kind"] != "return_refund":
                raise BusinessError("RETURN_NOT_AUTHORIZED")
            if row["status"] == "completed":
                return await self._present(conn, row)
            if row["status"] != "awaiting_return" or not row["approver"]:
                raise BusinessError("RETURN_NOT_AUTHORIZED")
            await self._verify_approval(conn, row)
            result = await self._perform(conn, row, receipt=True)
            result["received_by"] = actor
            result["received_at"] = _now()
            await conn.execute(
                update(actions)
                .where(actions.c.action_id == action_id)
                .values(status="completed", result=result)
            )
            await self._ticket_status(conn, action_id, "resolved", result["case_id"])
            await self._event(conn, action_id, "return_receive", actor, result)
            return await self._present(conn, await self._action_row(conn, action_id))

    async def list_tickets(self, customer_or_none):
        async with self.engine.connect() as conn:
            query = select(tickets).order_by(tickets.c.created_at, tickets.c.ticket_id)
            if customer_or_none is not None:
                query = query.where(tickets.c.customer_id == customer_or_none)
            rows = (await conn.execute(query)).mappings().all()
            return [
                {**dict(row), "manual_review": bool(row["manual_review"])}
                for row in rows
                if customer_or_none is None or row["customer_id"] == customer_or_none
            ]

    async def get_ticket(self, customer_or_none, ticket_id):
        async with self.engine.connect() as conn:
            row = (
                (await conn.execute(select(tickets).where(tickets.c.ticket_id == ticket_id)))
                .mappings()
                .first()
            )
            if (
                not row
                or row["ticket_id"] != ticket_id
                or (customer_or_none is not None and row["customer_id"] != customer_or_none)
            ):
                raise BusinessError("TICKET_NOT_FOUND")
            return {**dict(row), "manual_review": bool(row["manual_review"])}

    async def sync_profile(self, customer_id, display_name, role):
        _text(customer_id, "customer")
        display_name = _text(display_name, "display_name", 100)
        if role not in {"customer", "approver", "admin"}:
            raise BusinessError("INVALID_ROLE")
        async with self._write() as conn:
            row = (
                (
                    await conn.execute(
                        select(user_profiles)
                        .where(user_profiles.c.customer_id == customer_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row and row["customer_id"] != customer_id:
                raise BusinessError("PROFILE_CONFLICT")
            if row:
                await conn.execute(
                    update(user_profiles)
                    .where(user_profiles.c.customer_id == customer_id)
                    .values(display_name=display_name, role=role)
                )
            else:
                await conn.execute(
                    insert(user_profiles).values(
                        customer_id=customer_id,
                        display_name=display_name,
                        role=role,
                        created_at=_now(),
                    )
                )
        return await self.get_profile(customer_id)

    async def get_profile(self, customer_id):
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(user_profiles).where(user_profiles.c.customer_id == customer_id)
                    )
                )
                .mappings()
                .first()
            )
            if not row or row["customer_id"] != customer_id:
                raise BusinessError("PROFILE_NOT_FOUND")
            return dict(row)

    async def overview(self):
        async with self.engine.connect() as conn:
            counts = {}
            for name, table in (
                ("orders", orders),
                ("tickets", tickets),
                ("actions", actions),
                ("refunds", refunds),
                ("reships", reships),
            ):
                counts[name] = await conn.scalar(select(func.count()).select_from(table))
            for status in (
                "waiting_approval",
                "awaiting_return",
                "failed",
                "completed",
                "cancelled",
            ):
                counts[status] = await conn.scalar(
                    select(func.count()).select_from(actions).where(actions.c.status == status)
                )
            rows = (
                (
                    await conn.execute(
                        select(tickets)
                        .where((tickets.c.manual_review == 1) | (tickets.c.status == "failed"))
                        .order_by(tickets.c.created_at.desc())
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )
            tasks = (
                (
                    await conn.execute(
                        select(task_index).order_by(task_index.c.updated_at.desc()).limit(100)
                    )
                )
                .mappings()
                .all()
            )
            return {
                "counts": counts,
                "alerts": [dict(r) for r in rows],
                "tasks": [dict(r) for r in tasks],
                "simulated": True,
            }

    async def get_events(self, action_id=None):
        async with self.engine.connect() as conn:
            query = select(events).order_by(events.c.sequence)
            if action_id is not None:
                query = query.where(events.c.action_id == action_id)
            return [dict(row) for row in (await conn.execute(query)).mappings()]
