"""Isolated delivery schema; initialization only inserts missing demonstration records."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    insert,
    select,
)
from sqlalchemy.ext.asyncio import AsyncEngine

metadata = MetaData()
orders = Table(
    "orders",
    metadata,
    Column("order_id", String(80), primary_key=True),
    Column("customer_id", String(80), nullable=False, index=True),
    Column("sku", String(80), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("paid_cents", Integer, nullable=False),
    Column("refunded_cents", Integer, nullable=False, default=0),
    Column("reshipped_quantity", Integer, nullable=False, default=0),
    Column("status", String(32), nullable=False),
    CheckConstraint("paid_cents >= 0 AND refunded_cents >= 0 AND refunded_cents <= paid_cents"),
    CheckConstraint("quantity > 0 AND reshipped_quantity >= 0 AND reshipped_quantity <= quantity"),
)
inventory = Table(
    "inventory",
    metadata,
    Column("sku", String(80), primary_key=True),
    Column("available", Integer, nullable=False),
    CheckConstraint("available >= 0"),
)
shipments = Table(
    "shipments",
    metadata,
    Column("shipment_id", String(80), primary_key=True),
    Column("order_id", String(80), ForeignKey("orders.order_id"), nullable=False, index=True),
    Column("carrier", String(80), nullable=False),
    Column("tracking_number", String(100), nullable=False),
    Column("status", String(32), nullable=False),
    Column("description", Text, nullable=False),
    Column("updated_at", String(40), nullable=False),
)
actions = Table(
    "actions",
    metadata,
    Column("action_id", String(36), primary_key=True),
    Column("customer_id", String(80), nullable=False),
    Column("thread_id", String(120), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("order_id", String(80), ForeignKey("orders.order_id"), nullable=False),
    Column("description", Text, nullable=False),
    Column("amount_cents", Integer),
    Column("quantity", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("result", JSON),
    Column("approver", String(80)),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("customer_id", "thread_id", name="uq_action_customer_thread"),
)
cases = Table(
    "cases",
    metadata,
    Column("case_id", String(36), primary_key=True),
    Column("action_id", String(36), ForeignKey("actions.action_id"), nullable=False, unique=True),
    Column("customer_id", String(80), nullable=False, index=True),
    Column("order_id", String(80), ForeignKey("orders.order_id"), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("description", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("resolution", Text),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)
refunds = Table(
    "refunds",
    metadata,
    Column("refund_id", String(36), primary_key=True),
    Column("action_id", String(36), ForeignKey("actions.action_id"), nullable=False, unique=True),
    Column("order_id", String(80), ForeignKey("orders.order_id"), nullable=False),
    Column("amount_cents", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    CheckConstraint("amount_cents > 0"),
)
reships = Table(
    "reships",
    metadata,
    Column("reship_id", String(36), primary_key=True),
    Column("action_id", String(36), ForeignKey("actions.action_id"), nullable=False, unique=True),
    Column("order_id", String(80), ForeignKey("orders.order_id"), nullable=False),
    Column("sku", String(80), nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    CheckConstraint("quantity > 0"),
)
events = Table(
    "events",
    metadata,
    Column(
        "sequence",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("event_id", String(36), nullable=False, unique=True),
    Column("action_id", String(36), ForeignKey("actions.action_id"), nullable=False, index=True),
    Column("event_type", String(32), nullable=False),
    Column("actor", String(80), nullable=False),
    Column("details", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
)


# Upgrade sidecars leave existing delivery tables untouched.
order_policies = Table(
    "order_policies",
    metadata,
    Column("order_id", String(80), ForeignKey("orders.order_id"), primary_key=True),
    Column("aftersales_until", String(40), nullable=False),
    Column("risk_level", String(20), nullable=False, default="low"),
    Column("manual_review_threshold_cents", Integer, nullable=False, default=50000),
    CheckConstraint("manual_review_threshold_cents > 0"),
)
user_profiles = Table(
    "user_profiles",
    metadata,
    Column("customer_id", String(80), primary_key=True),
    Column("display_name", String(100), nullable=False),
    Column("role", String(20), nullable=False),
    Column("created_at", String(40), nullable=False),
)
tickets = Table(
    "tickets",
    metadata,
    Column("ticket_id", String(36), primary_key=True),
    Column("action_id", String(36), ForeignKey("actions.action_id"), nullable=False, unique=True),
    Column("customer_id", String(80), nullable=False, index=True),
    Column("order_id", String(80), ForeignKey("orders.order_id"), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("description", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("risk_level", String(20), nullable=False),
    Column("manual_review", Integer, nullable=False, default=0),
    Column("case_id", String(36)),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)
approval_reviews = Table(
    "approval_reviews",
    metadata,
    Column("action_id", String(36), ForeignKey("actions.action_id"), primary_key=True),
    Column("object_type", String(32), nullable=False),
    Column("object_id", String(80), nullable=False),
    Column("version", Integer, nullable=False),
    Column("decision", String(20), nullable=False),
    Column("actor", String(80), nullable=False),
    Column("reason", Text, nullable=False),
    Column("snapshot", JSON, nullable=False),
    Column("snapshot_hash", String(64), nullable=False),
    Column("created_at", String(40), nullable=False),
)
task_index = Table(
    "task_index",
    metadata,
    Column("task_id", String(120), primary_key=True),
    Column("thread_id", String(64), nullable=False),
    Column("customer_id", String(80), nullable=False, index=True),
    Column("status", String(32), nullable=False),
    Column("action_id", String(36)),
    Column("error_code", String(80)),
    Column("last_message", Text),
    Column("final_answer", Text),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)


async def initialize(engine: AsyncEngine) -> None:
    """Create tables in the engine's selected database and seed absent IDs only.

    Deployment must supply the dedicated aftersales_delivery database. This function
    never creates a database, changes accounts, drops tables, or replaces existing rows.
    """
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        for table, key, records in (
            (
                orders,
                "order_id",
                [
                    dict(
                        order_id="DEMO-001",
                        customer_id="learner-001",
                        sku="SKU-001",
                        quantity=1,
                        paid_cents=19900,
                        refunded_cents=0,
                        reshipped_quantity=0,
                        status="shipped",
                    ),
                    dict(
                        order_id="DEMO-002",
                        customer_id="learner-001",
                        sku="SKU-001",
                        quantity=1,
                        paid_cents=29900,
                        refunded_cents=0,
                        reshipped_quantity=0,
                        status="delivered",
                    ),
                    dict(
                        order_id="DEMO-003",
                        customer_id="learner-002",
                        sku="SKU-002",
                        quantity=1,
                        paid_cents=39900,
                        refunded_cents=0,
                        reshipped_quantity=0,
                        status="paid",
                    ),
                    dict(
                        order_id="DEMO-004",
                        customer_id="learner-001",
                        sku="SKU-002",
                        quantity=1,
                        paid_cents=49900,
                        refunded_cents=0,
                        reshipped_quantity=0,
                        status="paid",
                    ),
                ],
            ),
            (
                inventory,
                "sku",
                [dict(sku="SKU-001", available=5), dict(sku="SKU-002", available=1)],
            ),
            (
                shipments,
                "shipment_id",
                [
                    dict(
                        shipment_id="SHIP-001",
                        order_id="DEMO-001",
                        carrier="模拟快递",
                        tracking_number="TRACK-001",
                        status="in_transit",
                        description="运输中",
                        updated_at="2026-10-18T09:00:00+00:00",
                    ),
                    dict(
                        shipment_id="SHIP-002",
                        order_id="DEMO-001",
                        carrier="模拟快递",
                        tracking_number="TRACK-002",
                        status="exception",
                        description="派送异常，待核实地址",
                        updated_at="2026-10-19T09:00:00+00:00",
                    ),
                ],
            ),
        ):
            for record in records:
                if not (
                    await conn.execute(select(table.c[key]).where(table.c[key] == record[key]))
                ).first():
                    await conn.execute(insert(table).values(**record))


async def seed_platform(engine: AsyncEngine) -> None:
    """Append sandbox records; preserve all existing state on restart."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        records = []
        for suffix, status in (
            ("PAID", "paid"),
            ("SHIPPED", "shipped"),
            ("DELIVERED", "delivered"),
            ("EXPIRED", "paid"),
            ("RISK", "paid"),
            ("CANCELLED", "cancelled"),
            ("OTHER", "paid"),
        ):
            oid = f"UP-{suffix}"
            records.append(
                (
                    orders,
                    "order_id",
                    dict(
                        order_id=oid,
                        customer_id="learner-002" if suffix == "OTHER" else "learner-001",
                        sku="UP-SKU",
                        quantity=2,
                        paid_cents=19900,
                        refunded_cents=0,
                        reshipped_quantity=0,
                        status=status,
                    ),
                )
            )
            records.append(
                (
                    order_policies,
                    "order_id",
                    dict(
                        order_id=oid,
                        aftersales_until=(
                            now + timedelta(days=-1 if suffix == "EXPIRED" else 30)
                        ).isoformat(),
                        risk_level="high" if suffix == "RISK" else "low",
                        manual_review_threshold_cents=50000,
                    ),
                )
            )
            if status in {"shipped", "delivered"}:
                records.append(
                    (
                        shipments,
                        "shipment_id",
                        dict(
                            shipment_id=f"SHIP-{oid}",
                            order_id=oid,
                            carrier="沙箱快递",
                            tracking_number=f"TRACK-{oid}",
                            status="exception" if status == "shipped" else "delivered",
                            description="沙箱物流异常待核实"
                            if status == "shipped"
                            else "沙箱签收记录",
                            updated_at=now.isoformat(),
                        ),
                    )
                )
        records.append((inventory, "sku", dict(sku="UP-SKU", available=20)))
        for customer, name in (("learner-001", "演示客户一"), ("learner-002", "演示客户二")):
            records.append(
                (
                    user_profiles,
                    "customer_id",
                    dict(
                        customer_id=customer,
                        display_name=name,
                        role="customer",
                        created_at=now.isoformat(),
                    ),
                )
            )
        for table, key, record in records:
            if not (
                await conn.execute(select(table.c[key]).where(table.c[key] == record[key]))
            ).first():
                await conn.execute(insert(table).values(**record))
