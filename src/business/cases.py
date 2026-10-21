"""Customer-scoped case creation and matching-request collision recovery."""

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from business.orders import get_order

INSERT_CASE_SQL = text("""
    INSERT INTO aftersales_agent.cases
        (case_id, order_id, case_type, description, status)
    VALUES (:case_id, :order_id, 'logistics_exception', :description, 'open')
""")
INSERT_CASE_REQUEST_SQL = text("""
    INSERT INTO aftersales_agent.case_creation_requests
        (customer_id, request_id, request_payload, case_id)
    VALUES (:customer_id, :request_id, :request_payload, :case_id)
""")
READ_CASE_REQUEST_SQL = text("""
    SELECT r.customer_id, r.request_id, r.request_payload,
           r.created_at AS request_created_at,
           c.case_id, c.order_id, c.case_type, c.description,
           c.status, c.resolution, c.created_at, c.updated_at
    FROM aftersales_agent.case_creation_requests AS r
    JOIN aftersales_agent.cases AS c ON r.case_id = c.case_id
    JOIN aftersales_agent.orders AS o ON c.order_id = o.order_id
    WHERE r.customer_id = :customer_id AND r.request_id = :request_id
      AND o.customer_id = :customer_id
""")


class OrderUnavailableError(Exception):
    """An order is absent or not available to this customer."""


class CaseRequestConflictError(Exception):
    """A customer's request ID has already been used for a different application."""


def validate_case_input(
    *, customer_id: str, request_id: str, order_id: str, description: str
) -> None:
    for name, value, limit in (
        ("customer_id", customer_id, 32),
        ("request_id", request_id, 64),
        ("order_id", order_id, 32),
        ("description", description, 1000),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{name} must contain 1-{limit} characters and cannot be blank.")


def prepare_case_parameters(
    *, customer_id: str, request_id: str, order_id: str, description: str
) -> tuple[dict[str, str], dict[str, str]]:
    validate_case_input(
        customer_id=customer_id,
        request_id=request_id,
        order_id=order_id,
        description=description,
    )
    # case_id identifies the created resource, so it is not part of the intent payload.
    case_id = uuid4().hex
    payload = {
        "order_id": order_id,
        "case_type": "logistics_exception",
        "description": description,
    }
    return (
        {"case_id": case_id, "order_id": order_id, "description": description},
        {
            "customer_id": customer_id,
            "request_id": request_id,
            "request_payload": json.dumps(payload, ensure_ascii=False),
            "case_id": case_id,
        },
    )


async def require_owned_order(connection: AsyncConnection, order_id: str, customer_id: str) -> None:
    if await get_order(connection, order_id, customer_id) is None:
        raise OrderUnavailableError("Order is unavailable for the current customer.")


async def get_case_request(
    connection: AsyncConnection, customer_id: str, request_id: str
) -> dict[str, Any] | None:
    result = await connection.execute(
        READ_CASE_REQUEST_SQL, {"customer_id": customer_id, "request_id": request_id}
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    payload = row["request_payload"]
    if isinstance(payload, str | bytes | bytearray):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("Saved request payload must be a JSON object.")
    case_columns = (
        "case_id",
        "order_id",
        "case_type",
        "description",
        "status",
        "resolution",
        "created_at",
        "updated_at",
    )
    return {
        "customer_id": row["customer_id"],
        "request_id": row["request_id"],
        "request_payload": payload,
        "request_created_at": row["request_created_at"],
        "case": {name: row[name] for name in case_columns},
    }


def is_mysql_duplicate_key(error: IntegrityError) -> bool:
    """Recognize the numeric driver code without parsing/logging private SQL details."""
    arguments = getattr(error.orig, "args", ())
    return bool(arguments) and arguments[0] == 1062


def case_id_for_matching_request(
    existing: dict[str, Any] | None, incoming_payload: dict[str, str]
) -> str | None:
    """Reuse the user's completed exact payload comparison and original-ID return."""
    if existing is None:
        return None
    if existing["request_payload"] != incoming_payload:
        raise CaseRequestConflictError("Request content has changed.")
    return existing["case"]["case_id"]


async def recover_case_request(
    engine: AsyncEngine,
    *,
    customer_id: str,
    request_id: str,
    incoming_payload: dict[str, str],
) -> str | None:
    """Read the winning request after rollback; fresh transaction is a user exercise."""
    async with engine.begin() as connection:
        existing = await get_case_request(connection, customer_id, request_id)
        return case_id_for_matching_request(existing, incoming_payload)


async def create_case_with_request(
    engine: AsyncEngine,
    *,
    customer_id: str,
    request_id: str,
    order_id: str,
    description: str,
) -> str:
    """Create atomically or return the existing matching request's case ID."""
    case_parameters, request_parameters = prepare_case_parameters(
        customer_id=customer_id,
        request_id=request_id,
        order_id=order_id,
        description=description,
    )
    incoming_payload = json.loads(request_parameters["request_payload"])
    request_insert_started = False
    try:
        async with engine.begin() as connection:
            await require_owned_order(connection, order_id, customer_id)
            existing = await get_case_request(connection, customer_id, request_id)
            existing_case_id = case_id_for_matching_request(existing, incoming_payload)
            if existing_case_id is not None:
                return existing_case_id
            await connection.execute(INSERT_CASE_SQL, case_parameters)
            request_insert_started = True
            await connection.execute(INSERT_CASE_REQUEST_SQL, request_parameters)
    except IntegrityError as error:
        # The failed transaction has exited and rolled back before recovery starts.
        if not request_insert_started or not is_mysql_duplicate_key(error):
            raise
        recovered_case_id = await recover_case_request(
            engine,
            customer_id=customer_id,
            request_id=request_id,
            incoming_payload=incoming_payload,
        )
        if recovered_case_id is None:
            # A different unique-key problem must not be reported as a successful retry.
            raise
        return recovered_case_id
    return case_parameters["case_id"]
