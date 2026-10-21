import importlib
import json
import os

import pytest
import pytest_asyncio
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


def server_module():
    return importlib.import_module("aftersales.mcp_server")


@pytest_asyncio.fixture
async def engine():
    instance = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with instance.begin() as connection:
            statements = [
                "ATTACH DATABASE ':memory:' AS aftersales_agent",
                """CREATE TABLE aftersales_agent.orders (
                    order_id TEXT PRIMARY KEY, customer_id TEXT, paid_amount TEXT,
                    status TEXT, created_at TEXT, delivered_at TEXT)""",
                """CREATE TABLE aftersales_agent.cases (
                    case_id TEXT PRIMARY KEY, order_id TEXT, case_type TEXT, description TEXT,
                    status TEXT, resolution TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
                """CREATE TABLE aftersales_agent.case_creation_requests (
                    customer_id TEXT, request_id TEXT, request_payload TEXT, case_id TEXT UNIQUE,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(customer_id, request_id))""",
                """INSERT INTO aftersales_agent.orders VALUES
                    ('DEMO-001', 'learner-001', '199.00', 'shipped', '2026-10-16', NULL),
                    ('DEMO-002', 'learner-001', '299.00', 'delivered', '2026-10-14', '2026-10-18'),
                    ('DEMO-003', 'learner-002', '399.00', 'paid', '2026-10-17', NULL)""",
            ]
            for statement in statements:
                await connection.execute(text(statement))
        yield instance
    finally:
        await instance.dispose()


def write_helper():
    module = server_module()
    assert hasattr(module, "create_case_for_mcp"), "MCP case helper is missing"
    return module.create_case_for_mcp


@pytest.mark.asyncio
async def test_default_server_only_exposes_read_tools(engine):
    server = server_module().build_mcp_server(engine, "learner-001")
    assert {tool.name for tool in await server.list_tools()} == {"get_order", "get_shipments"}


@pytest.mark.asyncio
async def test_write_schema_only_exposes_order_and_description(engine):
    server = server_module().build_mcp_server(engine, "learner-001", case_request_id="REQ-MCP-001")
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == {"get_order", "get_shipments", "create_case"}
    schema = tools["create_case"].inputSchema
    assert set(schema["properties"]) == {"order_id", "description"}
    assert set(schema["required"]) == {"order_id", "description"}


@pytest.mark.asyncio
async def test_helper_creates_then_reuses_the_saved_request(engine):
    first = json.loads(
        await write_helper()(engine, "learner-001", "REQ-MCP-001", "DEMO-001", "Issue")
    )
    second = json.loads(
        await write_helper()(engine, "learner-001", "REQ-MCP-001", "DEMO-001", "Issue")
    )
    assert first == second
    assert first["case_ready"] is True
    assert first["customer_id"] == "learner-001"
    assert first["request_id"] == "REQ-MCP-001"
    assert first["case"]["status"] == "open"
    async with engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT COUNT(*) FROM aftersales_agent.cases"))
        ).scalar_one() == 1


@pytest.mark.asyncio
async def test_changed_application_is_a_specific_error(engine):
    await write_helper()(engine, "learner-001", "REQ-MCP-001", "DEMO-001", "Issue")
    with pytest.raises(ToolError, match="CASE_REQUEST_CONFLICT"):
        await write_helper()(engine, "learner-001", "REQ-MCP-001", "DEMO-002", "Issue")


@pytest.mark.asyncio
@pytest.mark.parametrize("order_id", ["DEMO-003", "MISSING"])
async def test_other_customer_and_missing_order_share_unavailable_error(engine, order_id):
    with pytest.raises(ToolError) as error:
        await write_helper()(engine, "learner-001", "REQ-MCP-001", order_id, "Issue")
    assert str(error.value) == "ORDER_NOT_FOUND"
    async with engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT COUNT(*) FROM aftersales_agent.cases"))
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_invalid_input_is_not_written(engine):
    with pytest.raises(ToolError, match="CASE_INPUT_INVALID"):
        await write_helper()(engine, "learner-001", "REQ-MCP-001", "DEMO-001", " ")


@pytest.mark.asyncio
async def test_database_failure_does_not_echo_private_sql_or_inputs():
    instance = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        with pytest.raises(ToolError) as error:
            await write_helper()(
                instance,
                "PRIVATE_CUSTOMER",
                "PRIVATE_REQUEST",
                "PRIVATE_ORDER",
                "PRIVATE_DESCRIPTION",
            )
        assert "CASE_OPERATION_FAILED" in str(error.value)
        assert "PRIVATE_" not in str(error.value)
        assert "SELECT" not in str(error.value)
    finally:
        await instance.dispose()


def test_child_context_is_pinned_and_not_part_of_command_or_parent_environment(monkeypatch):
    client = importlib.import_module("aftersales.mcp_client")
    monkeypatch.setenv("AFTERSALES_CASE_REQUEST_ID", "parent-request")
    args = dict(
        host="127.0.0.1",
        port=3306,
        username="root",
        password="PRIVATE_PASSWORD",
        customer_id="learner-001",
    )
    read = client.build_order_server_parameters(**args)
    assert read.env.get("AFTERSALES_CASE_REQUEST_ID") == ""
    write = client.build_order_server_parameters(**args, case_request_id="REQ-MCP-001")
    assert write.env["AFTERSALES_CASE_REQUEST_ID"] == "REQ-MCP-001"
    assert "REQ-MCP-001" not in " ".join(write.args)
    assert "PRIVATE_PASSWORD" not in " ".join(write.args)
    assert os.environ["AFTERSALES_CASE_REQUEST_ID"] == "parent-request"


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", [" ", "x" * 65])
async def test_invalid_write_context_is_rejected_at_server_construction(engine, request_id):
    with pytest.raises(ValueError):
        server_module().build_mcp_server(engine, "learner-001", case_request_id=request_id)
