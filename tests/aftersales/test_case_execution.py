import importlib
import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextContent
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

from aftersales.approvals import (
    AftersalesApprovalError,
    CaseApprovalState,
    prepare_case_proposal,
    request_case_approval,
)
from aftersales.checkpoints import build_checkpoint_config
from aftersales.mcp_server import build_mcp_server

ROOT = Path(__file__).resolve().parents[2]


def execution():
    return importlib.import_module("aftersales.execution")


def recovery():
    return importlib.import_module("aftersales.recovery")


@pytest_asyncio.fixture
async def business_engine(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def attach(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(
            "ATTACH DATABASE ? AS aftersales_agent", (str(tmp_path / "business.sqlite"),)
        )
        cursor.close()

    async with engine.begin() as connection:
        for statement in [
            "CREATE TABLE aftersales_agent.orders (order_id TEXT PRIMARY KEY, customer_id TEXT, "
            "paid_amount TEXT, status TEXT, created_at TEXT, delivered_at TEXT)",
            "CREATE TABLE aftersales_agent.cases (case_id TEXT PRIMARY KEY, order_id TEXT "
            "REFERENCES orders(order_id), case_type TEXT, description TEXT, status TEXT, "
            "resolution TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TEXT DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE aftersales_agent.case_creation_requests (customer_id TEXT, request_id TEXT, "
            "request_payload TEXT, case_id TEXT UNIQUE REFERENCES cases(case_id), "
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(customer_id, request_id))",
            "INSERT INTO aftersales_agent.orders VALUES "
            "('DEMO-001', 'learner-001', '199.00', 'shipped', '2026-10-16', NULL)",
        ]:
            await connection.execute(text(statement))
    try:
        yield engine
    finally:
        await engine.dispose()


def install_memory_mcp(monkeypatch, engine):
    module = execution()
    opened = []

    @asynccontextmanager
    async def open_session(**kwargs):
        opened.append(kwargs)
        server = build_mcp_server(
            engine, kwargs["customer_id"], case_request_id=kwargs["case_request_id"]
        )
        async with create_connected_server_and_client_session(server) as session:
            yield session

    monkeypatch.setattr(module, "order_mcp_session", open_session)
    executor = module.build_case_executor(
        host="127.0.0.1", port=3306, username="root", password="PRIVATE", customer_id="learner-001"
    )
    return executor, opened


async def write_pending(saver, *, mixed=False):
    config = build_checkpoint_config("learner-001", "approve-002")
    calls = [
        {
            "name": "create_case",
            "id": "approved-call-002",
            "args": {
                "order_id": "DEMO-001",
                "description": "收件信息待核实，派送暂缓",
            },
        }
    ]
    if mixed:
        calls.insert(
            0, {"name": "get_order", "id": "read-call-002", "args": {"order_id": "DEMO-001"}}
        )
    builder = StateGraph(CaseApprovalState)
    builder.add_node("model", lambda _: {"messages": [AIMessage(content="", tool_calls=calls)]})
    builder.add_node("prepare_case", lambda s: prepare_case_proposal(s, "learner-001"))
    builder.add_node("case_approval", request_case_approval)
    builder.add_edge(START, "model")
    builder.add_edge("model", "prepare_case")
    builder.add_edge("prepare_case", "case_approval")
    builder.add_edge("case_approval", END)
    graph = builder.compile(checkpointer=saver)
    with tracing_context(enabled=False):
        await graph.ainvoke(
            {"messages": [HumanMessage(content="登记工单。")]}, config=config, durability="sync"
        )
    return config, (await graph.aget_state(config)).values["pending_case"]


async def counts(engine):
    async with engine.connect() as connection:
        return tuple(
            [
                (
                    await connection.execute(text(f"SELECT COUNT(*) FROM aftersales_agent.{table}"))
                ).scalar_one()
                for table in ["cases", "case_creation_requests"]
            ]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
async def test_approve_old_checkpoint_with_real_mcp_and_matching_business_record(
    tmp_path, monkeypatch, business_engine, mixed
):
    module = execution()
    executor, opened = install_memory_mcp(monkeypatch, business_engine)
    database = str(tmp_path / "checkpoint.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        config, pending = await write_pending(saver, mixed=mixed)
    assert await counts(business_engine) == (0, 0)
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        with tracing_context(enabled=False):
            snapshot = await recovery().approve_saved_case(saver, config, "learner-001", executor)
        assert snapshot.next == ()
        assert snapshot.values["approval_decision"] is True
        record = snapshot.values["case_result"]
        module.validate_case_result(record, pending)
        assert record["request_id"] == pending["request_id"]
        assert record["case"]["status"] == "open"
        assert snapshot.values["pending_case"] == {**pending, "status": "created"}
        results = [m for m in snapshot.values["messages"] if isinstance(m, ToolMessage)]
        assert len(results) == (2 if mixed else 1)
        assert results[-1].tool_call_id == pending["tool_call_id"]
        assert results[-1].status == "success"
        assert json.loads(results[-1].text) == record
        assert opened[0]["customer_id"] == "learner-001"
        assert opened[0]["case_request_id"] == pending["request_id"]
        before = [item.config async for item in saver.alist(config)]
        with tracing_context(enabled=False):
            repeated = await recovery().approve_saved_case(saver, config, "learner-001", executor)
        assert repeated.values == snapshot.values
        assert [item.config async for item in saver.alist(config)] == before
        assert len(opened) == 1
    assert await counts(business_engine) == (1, 1)


@pytest.mark.asyncio
async def test_retry_after_commit_and_lost_response_keeps_one_business_case(
    tmp_path, monkeypatch, business_engine
):
    module = execution()
    executor, opened = install_memory_mcp(monkeypatch, business_engine)
    actual_call = module.invoke_approved_case

    async def lost_response(session, pending):
        await actual_call(session, pending)
        raise OSError("PRIVATE: simulated connection loss after commit")

    monkeypatch.setattr(module, "invoke_approved_case", lost_response)
    database = str(tmp_path / "retry.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        config, pending = await write_pending(saver)
        with tracing_context(enabled=False), pytest.raises((OSError, ExceptionGroup)):
            await recovery().approve_saved_case(saver, config, "learner-001", executor)
    assert await counts(business_engine) == (1, 1)
    monkeypatch.setattr(module, "invoke_approved_case", actual_call)
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        with tracing_context(enabled=False):
            result = await recovery().approve_saved_case(saver, config, "learner-001", executor)
        assert result.next == ()
        assert result.values["case_result"]["request_id"] == pending["request_id"]
        assert len(opened) == 2
        assert {item["case_request_id"] for item in opened} == {pending["request_id"]}
    assert await counts(business_engine) == (1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [None, False, "true", 1])
async def test_unapproved_execution_never_opens_mcp(monkeypatch, decision):
    module = execution()
    connection = Mock(side_effect=AssertionError("MCP must not start"))
    monkeypatch.setattr(module, "order_mcp_session", connection)
    executor = module.build_case_executor(
        host="local", port=3306, username="root", password="PRIVATE", customer_id="learner-001"
    )
    with pytest.raises(AftersalesApprovalError):
        await executor({"messages": [], "approval_decision": decision})
    connection.assert_not_called()


@pytest.mark.asyncio
async def test_execution_requires_an_already_saved_application(monkeypatch):
    module = execution()
    connection = Mock(side_effect=AssertionError("MCP must not start"))
    monkeypatch.setattr(module, "order_mcp_session", connection)
    executor = module.build_case_executor(
        host="local", port=3306, username="root", password="PRIVATE", customer_id="learner-001"
    )
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "create_case",
                "id": "unsaved-call",
                "args": {"order_id": "DEMO-001", "description": "Issue"},
            }
        ],
    )
    with pytest.raises(AftersalesApprovalError):
        await executor({"messages": [message], "approval_decision": True})
    connection.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["wrong_customer", "wrong_task", "changed_payload", "rejected"])
async def test_invalid_saved_application_never_calls_executor(tmp_path, mode):
    from langchain_core.runnables import RunnableLambda
    from langgraph.types import Command

    from aftersales.approvals import build_case_proposal_tool
    from aftersales.graph import build_aftersales_graph

    executor = Mock(side_effect=AssertionError("executor must not run"))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "scope.sqlite")) as saver:
        config, pending = await write_pending(saver)
        customer = "learner-001"
        graph = build_aftersales_graph(
            RunnableLambda(lambda _: None),
            [build_case_proposal_tool()],
            checkpointer=saver,
            customer_id=customer,
        )
        if mode == "wrong_customer":
            customer = "learner-002"
        elif mode == "wrong_task":
            config = build_checkpoint_config(customer, "missing-task")
        elif mode == "changed_payload":
            await graph.aupdate_state(
                config, {"pending_case": {**pending, "description": "changed"}}
            )
        else:
            with tracing_context(enabled=False):
                await graph.ainvoke(Command(resume=False), config=config, durability="sync")
        with pytest.raises(AftersalesApprovalError):
            await recovery().approve_saved_case(saver, config, customer, executor)
    executor.assert_not_called()


def saved_record():
    pending = {
        "customer_id": "learner-001",
        "request_id": "SAVED-REQUEST",
        "order_id": "DEMO-001",
        "description": "Issue",
    }
    record = {
        "case_ready": True,
        "customer_id": pending["customer_id"],
        "request_id": pending["request_id"],
        "request_payload": {
            "order_id": pending["order_id"],
            "description": pending["description"],
            "case_type": "logistics_exception",
        },
        "case": {
            "case_id": "REAL-CASE",
            "order_id": pending["order_id"],
            "description": pending["description"],
            "case_type": "logistics_exception",
            "status": "open",
        },
    }
    return pending, record


@pytest.mark.parametrize(
    "field", ["case_ready", "customer_id", "request_id", "request_payload", "case"]
)
def test_success_requires_matching_confirmed_application(field):
    module = execution()
    pending, record = saved_record()
    invalid = deepcopy(record)
    invalid[field] = "UNTRUSTED"
    with pytest.raises(module.AftersalesCaseExecutionError):
        module.validate_case_result(invalid, pending)
    assert record[field] != "UNTRUSTED"


@pytest.mark.parametrize("field", ["case_id", "order_id", "description", "case_type", "status"])
def test_case_resource_fields_are_verified(field):
    module = execution()
    pending, record = saved_record()
    record["case"][field] = "" if field == "case_id" else "UNTRUSTED"
    with pytest.raises(module.AftersalesCaseExecutionError):
        module.validate_case_result(record, pending)


@pytest.mark.parametrize(
    "result",
    [
        CallToolResult(
            isError=True, content=[TextContent(type="text", text="CASE_REQUEST_CONFLICT PRIVATE")]
        ),
        CallToolResult(content=[TextContent(type="text", text="not-json PRIVATE")]),
        CallToolResult(content=[]),
    ],
)
def test_error_or_malformed_mcp_result_never_becomes_success(result):
    module = execution()
    pending, _ = saved_record()
    with pytest.raises(module.AftersalesCaseExecutionError) as error:
        module.parse_case_result(result, pending)
    assert "PRIVATE" not in str(error.value)


def launcher(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "case_execution_launcher", ROOT / "scripts/run_aftersales_agent.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_approval_cli_runs_without_model_or_direct_business_connection(
    tmp_path, monkeypatch, business_engine, capsys
):
    cli = launcher(monkeypatch)
    install_memory_mcp(monkeypatch, business_engine)
    database = str(tmp_path / "cli.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        _, pending = await write_pending(saver)

    @asynccontextmanager
    async def open_saver(**kwargs):
        async with AsyncSqliteSaver.from_conn_string(database) as saver:
            yield saver

    monkeypatch.setattr(cli, "aftersales_checkpointer", open_saver)
    for name in [
        "get_bound_model",
        "order_mcp_session",
        "load_order_mcp_tools",
        "run_local_agent",
        "create_async_engine",
    ]:
        monkeypatch.setattr(cli, name, Mock(side_effect=AssertionError(name)))
    args = SimpleNamespace(
        host="127.0.0.1",
        port=3306,
        user="root",
        customer_id="learner-001",
        thread_id="approve-002",
        history=False,
        approval_status=False,
        reject_case=False,
        approve_case=True,
        request_approval=False,
        message=None,
        tool_transport="local",
    )
    with tracing_context(enabled=False):
        await cli.run_agent(args, "PRIVATE")
    output = capsys.readouterr().out
    assert "CASE_EXECUTION_SAVED: True" in output
    assert "APPROVAL_DECISION: true" in output
    assert '"status": "created"' in output
    assert "CHECKPOINT_NEXT: []" in output
    assert pending["request_id"] in output
    assert "PRIVATE" not in output


@pytest.mark.parametrize(
    "extra",
    [
        ["--approve-case"],
        ["--approve-case", "--reject-case", "--thread-id", "task"],
        ["--approve-case", "--message", "yes", "--thread-id", "task"],
        ["--approve-case", "--request-approval", "--thread-id", "task", "--tool-transport", "mcp"],
    ],
)
def test_invalid_approval_cli_stops_before_credentials(monkeypatch, extra):
    cli = launcher(monkeypatch)
    monkeypatch.setattr(cli, "get_mysql_password", Mock(side_effect=AssertionError("password")))
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_aftersales_agent.py", "--user", "root", "--customer-id", "learner-001", *extra],
    )
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2


def test_valid_approval_cli_is_accepted(monkeypatch):
    cli = launcher(monkeypatch)
    seen = []

    async def capture(args, password):
        seen.append((args.approve_case, args.thread_id, password))

    monkeypatch.setattr(cli, "run_agent", capture)
    monkeypatch.setattr(cli, "get_mysql_password", lambda: "PRIVATE")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_aftersales_agent.py",
            "--user",
            "root",
            "--customer-id",
            "learner-001",
            "--thread-id",
            "task",
            "--approve-case",
        ],
    )
    assert cli.main() == 0
    assert seen == [(True, "task", "PRIVATE")]
