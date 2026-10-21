import importlib
import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from langsmith import tracing_context

from aftersales.approvals import (
    AftersalesApprovalError,
    CaseApprovalState,
    build_case_proposal_tool,
    prepare_case_proposal,
    request_case_approval,
)
from aftersales.checkpoints import build_checkpoint_config
from aftersales.graph import build_aftersales_graph

ROOT = Path(__file__).resolve().parents[2]


def recovery():
    return importlib.import_module("aftersales.recovery")


def launcher(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "aftersales_rejection_launcher", ROOT / "scripts/run_aftersales_agent.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def write_pending(saver, *, mixed=False):
    config = build_checkpoint_config("learner-001", "reject-001")
    calls = [
        {
            "name": "create_case",
            "id": "reject-call-001",
            "args": {"order_id": "DEMO-001", "description": "派送暂缓。"},
        }
    ]
    if mixed:
        calls.insert(
            0, {"name": "get_order", "id": "read-call-001", "args": {"order_id": "DEMO-001"}}
        )
    # Write using the previous lesson's topology, then reopen using the new graph.
    builder = StateGraph(CaseApprovalState)
    builder.add_node("model", lambda _: {"messages": [AIMessage(content="", tool_calls=calls)]})
    builder.add_node("prepare_case", lambda state: prepare_case_proposal(state, "learner-001"))
    builder.add_node("case_approval", request_case_approval)
    builder.add_edge(START, "model")
    builder.add_edge("model", "prepare_case")
    builder.add_edge("prepare_case", "case_approval")
    builder.add_edge("case_approval", END)
    graph = builder.compile(checkpointer=saver)
    with tracing_context(enabled=False):
        await graph.ainvoke(
            {"messages": [HumanMessage(content="登记物流问题。")]},
            config=config,
            durability="sync",
        )
    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("case_approval",)
    return config, snapshot.values["pending_case"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
async def test_resume_old_checkpoint_from_new_connection_records_rejection(tmp_path, mixed):
    module = recovery()
    database = str(tmp_path / "reject.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        config, pending = await write_pending(saver, mixed=mixed)
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        with tracing_context(enabled=False):
            snapshot = await module.reject_saved_case(saver, config, "learner-001")
        assert snapshot.next == ()
        assert snapshot.values["pending_case"] == {**pending, "status": "rejected"}
        assert snapshot.values["approval_decision"] is False
        results = [m for m in snapshot.values["messages"] if isinstance(m, ToolMessage)]
        assert len(results) == (2 if mixed else 1)
        assert results[-1].tool_call_id == pending["tool_call_id"]
        assert results[-1].status == "error"
        assert json.loads(results[-1].text) == {"case_ready": False, "error": "CASE_REJECTED"}
        if mixed:
            assert results[0].tool_call_id == "read-call-001"
            assert json.loads(results[0].text) == {"error": "TOOL_BATCH_CANCELLED"}
        assert [m.type for m in snapshot.values["messages"]][:2] == ["human", "ai"]
        before = [item.config async for item in saver.alist(config)]
        with pytest.raises(AftersalesApprovalError):
            await module.reject_saved_case(saver, config, "learner-001")
        assert [item.config async for item in saver.alist(config)] == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "other_customer", "other_task", "wrong_owner"])
async def test_invalid_saved_scope_stops_before_user_core(tmp_path, monkeypatch, mode):
    module = recovery()
    core = Mock(side_effect=AssertionError("resume must not run"))
    monkeypatch.setattr(module, "resume_case_rejection", core)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "guards.sqlite")) as saver:
        config, _ = await write_pending(saver)
        customer = "learner-001"
        if mode == "other_customer":
            config = build_checkpoint_config("learner-002", "reject-001")
            customer = "learner-002"
        elif mode == "other_task":
            config = build_checkpoint_config(customer, "other-task")
        elif mode == "missing":
            config = build_checkpoint_config(customer, "missing")
        else:
            customer = "learner-002"
        with pytest.raises(AftersalesApprovalError):
            await module.reject_saved_case(saver, config, customer)
    core.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["not_interrupted", "changed_order", "decision_exists"])
async def test_changed_or_non_pending_state_is_not_resumed(tmp_path, monkeypatch, change):
    module = recovery()
    core = Mock(side_effect=AssertionError("resume must not run"))
    monkeypatch.setattr(module, "resume_case_rejection", core)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "changed.sqlite")) as saver:
        config, pending = await write_pending(saver)
        graph = build_aftersales_graph(
            RunnableLambda(lambda _: pytest.fail("model must not run")),
            [build_case_proposal_tool()],
            checkpointer=saver,
            customer_id="learner-001",
        )
        if change == "changed_order":
            await graph.aupdate_state(config, {"pending_case": {**pending, "order_id": "DEMO-002"}})
        elif change == "decision_exists":
            await graph.aupdate_state(config, {"approval_decision": False})
        else:
            await graph.aupdate_state(config, {"approval_decision": None}, as_node="reject_case")
        before = [item.config async for item in saver.alist(config)]
        with pytest.raises(AftersalesApprovalError):
            await module.reject_saved_case(saver, config, "learner-001")
        assert [item.config async for item in saver.alist(config)] == before
    core.assert_not_called()


@pytest.mark.asyncio
async def test_true_resume_is_refused_before_any_case_tool_can_run(tmp_path):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "approve.sqlite")) as saver:
        config, pending = await write_pending(saver)
        graph = build_aftersales_graph(
            RunnableLambda(lambda _: pytest.fail("model must not run")),
            [build_case_proposal_tool()],
            checkpointer=saver,
            customer_id="learner-001",
        )
        with tracing_context(enabled=False), pytest.raises(AftersalesApprovalError):
            await graph.ainvoke(Command(resume=True), config=config, durability="sync")
        snapshot = await graph.aget_state(config)
        assert snapshot.values["pending_case"] == pending


@pytest.mark.asyncio
async def test_cli_reject_does_not_start_model_mcp_or_business_db(tmp_path, monkeypatch, capsys):
    cli = launcher(monkeypatch)
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
        "run_graph_with_tools",
        "run_local_agent",
        "create_async_engine",
    ]:
        monkeypatch.setattr(cli, name, Mock(side_effect=AssertionError(name)))
    args = SimpleNamespace(
        host="127.0.0.1",
        port=3306,
        user="root",
        customer_id="learner-001",
        thread_id="reject-001",
        history=False,
        approval_status=False,
        reject_case=True,
        request_approval=False,
        message=None,
        tool_transport="local",
    )
    with tracing_context(enabled=False):
        await cli.run_agent(args, "PRIVATE")
    output = capsys.readouterr().out
    assert "REJECTION_RECORDED: True" in output
    assert "APPROVAL_DECISION: false" in output
    assert "CHECKPOINT_NEXT: []" in output
    assert "CHECKPOINT_MESSAGE_COUNT: 3" in output
    assert pending["request_id"] in output
    assert '"status": "rejected"' in output
    assert "PRIVATE" not in output


@pytest.mark.parametrize(
    "extra",
    [
        ["--reject-case"],
        ["--reject-case", "--message", "拒绝", "--thread-id", "task"],
        ["--reject-case", "--approval-status", "--thread-id", "task"],
        ["--reject-case", "--request-approval", "--thread-id", "task", "--tool-transport", "mcp"],
    ],
)
def test_invalid_rejection_cli_stops_before_credentials(monkeypatch, extra):
    cli = launcher(monkeypatch)
    monkeypatch.setattr(cli, "get_mysql_password", Mock(side_effect=AssertionError("password")))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_aftersales_agent.py",
            "--user",
            "root",
            "--customer-id",
            "learner-001",
            *extra,
        ],
    )
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 2


def test_rejection_cli_accepts_existing_customer_and_thread(monkeypatch):
    cli = launcher(monkeypatch)
    captured = []

    async def capture(args, password):
        captured.append((args.reject_case, args.thread_id, password))

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
            "--reject-case",
        ],
    )
    assert cli.main() == 0
    assert captured == [(True, "task", "PRIVATE")]
