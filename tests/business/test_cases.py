import importlib
import json
import re

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def module():
    assert importlib.util.find_spec("business.cases") is not None, "Case module is missing"
    return importlib.import_module("business.cases")


def inputs(**overrides):
    values = dict(
        customer_id="learner-001",
        request_id="REQ-001",
        order_id="DEMO-001",
        description=" 物流异常：请核实收件信息。 ",
    )
    values.update(overrides)
    return values


def test_prepared_records_share_generated_case_id_and_preserve_original_payload():
    cases, requests = module().prepare_case_parameters(**inputs())
    assert re.fullmatch(r"[0-9a-f]{32}", cases["case_id"])
    assert requests["case_id"] == cases["case_id"]
    assert requests["customer_id"] == "learner-001"
    assert requests["request_id"] == "REQ-001"
    payload = json.loads(requests["request_payload"])
    assert payload == {
        "order_id": "DEMO-001",
        "case_type": "logistics_exception",
        "description": inputs()["description"],
    }
    assert cases["order_id"] == payload["order_id"]
    assert cases["description"] == payload["description"]
    assert "case_id" not in payload


@pytest.mark.parametrize(
    "overrides",
    [
        {"customer_id": ""},
        {"customer_id": "c" * 33},
        {"request_id": " "},
        {"request_id": "r" * 65},
        {"order_id": "o" * 33},
        {"description": " "},
        {"description": "x" * 1001},
    ],
)
def test_invalid_case_inputs_are_rejected(overrides):
    with pytest.raises(ValueError):
        module().prepare_case_parameters(**inputs(**overrides))


@pytest_asyncio.fixture
async def connection():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("ATTACH DATABASE ':memory:' AS aftersales_agent"))
            await connection.execute(
                text("""CREATE TABLE aftersales_agent.orders (
                order_id TEXT PRIMARY KEY, customer_id TEXT, paid_amount TEXT,
                status TEXT, created_at TEXT, delivered_at TEXT)""")
            )
            await connection.execute(
                text("""CREATE TABLE aftersales_agent.cases (
                case_id TEXT PRIMARY KEY, order_id TEXT, case_type TEXT, description TEXT,
                status TEXT, resolution TEXT, created_at TEXT, updated_at TEXT)""")
            )
            await connection.execute(
                text("""CREATE TABLE aftersales_agent.case_creation_requests (
                customer_id TEXT, request_id TEXT, request_payload TEXT, case_id TEXT UNIQUE,
                created_at TEXT, PRIMARY KEY (customer_id, request_id))""")
            )
            await connection.execute(
                text("""INSERT INTO aftersales_agent.orders VALUES
                ('DEMO-001', 'learner-001', '199.00', 'shipped', '2026-10-16', NULL),
                ('DEMO-003', 'learner-002', '399.00', 'paid', '2026-10-17', NULL)""")
            )
            for customer_id, order_id, case_id, request_id in [
                ("learner-001", "DEMO-001", "CASE-ONE", "REQ-001"),
                ("learner-002", "DEMO-003", "CASE-TWO", "REQ-001"),
                ("learner-002", "DEMO-003", "CASE-PRIVATE", "REQ-PRIVATE"),
            ]:
                payload = dict(
                    order_id=order_id, case_type="logistics_exception", description="test"
                )
                await connection.execute(
                    text("""INSERT INTO aftersales_agent.cases VALUES
                    (:case_id, :order_id, 'logistics_exception', 'test',
                     'open', NULL, '2026-10-20', '2026-10-20')"""),
                    dict(case_id=case_id, order_id=order_id),
                )
                await connection.execute(
                    text("""INSERT INTO aftersales_agent.case_creation_requests
                    VALUES (:customer_id, :request_id, :payload, :case_id, '2026-10-20')"""),
                    dict(
                        customer_id=customer_id,
                        request_id=request_id,
                        payload=json.dumps(payload),
                        case_id=case_id,
                    ),
                )
            yield connection
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("customer_id", "request_id", "expected_case"),
    [
        ("learner-001", "REQ-001", "CASE-ONE"),
        ("learner-002", "REQ-001", "CASE-TWO"),
        ("learner-001", "REQ-PRIVATE", None),
        ("learner-001", "UNKNOWN", None),
    ],
)
async def test_case_request_reads_only_the_current_customers_linked_case(
    connection, customer_id, request_id, expected_case
):
    result = await module().get_case_request(connection, customer_id, request_id)
    if expected_case is None:
        assert result is None
    else:
        assert result["customer_id"] == customer_id
        assert result["request_id"] == request_id
        assert result["case"]["case_id"] == expected_case
        assert result["case"]["status"] == "open"
        assert isinstance(result["request_payload"], dict)
        assert result["request_payload"]["order_id"] == result["case"]["order_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("order_id", ["DEMO-001", "DEMO-003", "UNKNOWN"])
async def test_owned_order_validation(connection, order_id):
    backend = module()
    if order_id == "DEMO-001":
        await backend.require_owned_order(connection, order_id, "learner-001")
    else:
        with pytest.raises(backend.OrderUnavailableError):
            await backend.require_owned_order(connection, order_id, "learner-001")
