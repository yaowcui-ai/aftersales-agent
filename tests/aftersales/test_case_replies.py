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
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from langsmith import tracing_context

from aftersales.approvals import (
    AftersalesApprovalError,
    build_case_proposal_tool,
    case_result_messages,
)
from aftersales.checkpoints import build_checkpoint_config
from aftersales.graph import build_aftersales_graph

ROOT = Path(__file__).resolve().parents[2]
REPLY = "已为订单 DEMO-001 登记工单 CASE-SAVED-001，目前待处理。"


def replies():
    return importlib.import_module("aftersales.replies")


def recovery():
    return importlib.import_module("aftersales.recovery")


def responder(response=None):
    module = replies()
    calls = []

    async def respond(messages):
        calls.append(messages)
        return response if response is not None else AIMessage(content=REPLY)

    return module.build_case_responder(
        lambda: RunnableLambda(respond), customer_id="learner-001"
    ), calls


async def write_created(saver, *, case_responder=None):
    config = build_checkpoint_config("learner-001", "case-reply-002")
    writes = []

    async def execute(state):
        pending = state["pending_case"]
        writes.append(pending["request_id"])
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
                "case_id": "CASE-SAVED-001",
                "order_id": pending["order_id"],
                "description": pending["description"],
                "case_type": "logistics_exception",
                "status": "open",
            },
        }
        return {
            "pending_case": {**pending, "status": "created"},
            "approval_decision": True,
            "case_result": record,
            "messages": case_result_messages(state, record, status="success"),
        }

    kwargs = {"case_responder": case_responder} if case_responder is not None else {}
    graph = build_aftersales_graph(
        RunnableLambda(
            lambda _: AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_case",
                        "id": "reply-source-call",
                        "args": {"order_id": "DEMO-001", "description": "派送暂缓"},
                    }
                ],
            )
        ),
        [build_case_proposal_tool()],
        checkpointer=saver,
        customer_id="learner-001",
        case_executor=execute,
        **kwargs,
    )
    with tracing_context(enabled=False):
        await graph.ainvoke(
            {"messages": [HumanMessage(content="帮我登记工单。")]}, config=config, durability="sync"
        )
        await graph.ainvoke(Command(resume=True), config=config, durability="sync")
    return config, await graph.aget_state(config), writes


@pytest.mark.asyncio
async def test_legacy_completed_case_gets_one_persisted_reply_after_reopening(
    tmp_path, monkeypatch
):
    callback, calls = responder()
    database = str(tmp_path / "reply.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        config, before, writes = await write_created(saver)
    assert before.next == ()
    assert len(before.values["messages"]) == 3
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        with tracing_context(enabled=False):
            after = await recovery().reply_to_saved_case(saver, config, "learner-001", callback)
        assert after.next == ()
        assert after.values["case_reply"] == REPLY
        assert len(after.values["messages"]) == 4
        assert after.values["messages"][-1].text == REPLY
        assert after.values["pending_case"] == before.values["pending_case"]
        assert after.values["case_result"] == before.values["case_result"]
        assert len(calls) == len(writes) == 1
        assert isinstance(calls[0][0], SystemMessage)
        assert isinstance(calls[0][-1], ToolMessage)
        assert json.loads(calls[0][-1].text) == before.values["case_result"]
        checkpoints = [item.config async for item in saver.alist(config)]
        repeated = await recovery().reply_to_saved_case(saver, config, "learner-001", callback)
        assert repeated.values == after.values
        assert [item.config async for item in saver.alist(config)] == checkpoints
        assert len(calls) == len(writes) == 1


@pytest.mark.asyncio
async def test_optional_graph_edge_persists_result_then_replies(tmp_path, monkeypatch):
    callback, calls = responder()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "fresh.sqlite")) as saver:
        _, after, writes = await write_created(saver, case_responder=callback)
    assert after.next == ()
    assert after.values["case_reply"] == REPLY
    assert len(calls) == len(writes) == 1


@pytest.mark.asyncio
async def test_failed_reply_resumes_only_reply_node_with_business_result_unchanged(
    tmp_path, monkeypatch
):
    module = replies()
    attempts = []

    async def fail(messages):
        attempts.append("failed")
        raise TimeoutError("simulated model failure")

    callback = module.build_case_responder(lambda: RunnableLambda(fail), customer_id="learner-001")
    database = str(tmp_path / "retry.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        config, before, writes = await write_created(saver)
        with tracing_context(enabled=False), pytest.raises(TimeoutError):
            await recovery().reply_to_saved_case(saver, config, "learner-001", callback)
        graph = build_aftersales_graph(
            RunnableLambda(lambda _: None),
            [build_case_proposal_tool()],
            checkpointer=saver,
            customer_id="learner-001",
            case_responder=callback,
        )
        failed = await graph.aget_state(config)
        assert failed.next == ("case_reply",)
        assert failed.values["case_result"] == before.values["case_result"]
        assert len(failed.values["messages"]) == 3
    successful, calls = responder()
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        with tracing_context(enabled=False):
            after = await recovery().reply_to_saved_case(saver, config, "learner-001", successful)
    assert after.next == ()
    assert after.values["case_reply"] == REPLY
    assert after.values["case_result"] == before.values["case_result"]
    assert len(writes) == len(attempts) == len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "other_customer",
        "other_task",
        "pending",
        "no_result",
        "changed_result",
        "missing_tool_result",
    ],
)
async def test_invalid_saved_case_stops_before_model_or_checkpoint_changes(
    tmp_path, monkeypatch, mode
):
    module = replies()
    factory = Mock(side_effect=AssertionError("model must not initialize"))
    customer = "learner-002" if mode == "other_customer" else "learner-001"
    callback = module.build_case_responder(factory, customer_id=customer)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "guards.sqlite")) as saver:
        config, saved, _ = await write_created(saver)
        graph = build_aftersales_graph(
            RunnableLambda(lambda _: None),
            [build_case_proposal_tool()],
            checkpointer=saver,
            customer_id="learner-001",
        )
        updates = {}
        if mode == "other_task":
            config = build_checkpoint_config(customer, "other-task")
        elif mode == "pending":
            updates = {
                "pending_case": {**saved.values["pending_case"], "status": "waiting_approval"},
                "approval_decision": None,
            }
        elif mode == "no_result":
            updates = {"case_result": None}
        elif mode == "changed_result":
            record = deepcopy(saved.values["case_result"])
            record["case"]["case_id"] = "CHANGED-CASE"
            updates = {"case_result": record}
        elif mode == "missing_tool_result":
            from langchain_core.messages import RemoveMessage

            updates = {"messages": [RemoveMessage(id=saved.values["messages"][-1].id)]}
        if updates:
            await graph.aupdate_state(config, updates, as_node="execute_case")
        checkpoints = [item.config async for item in saver.alist(config)]
        with pytest.raises(AftersalesApprovalError):
            await recovery().reply_to_saved_case(saver, config, customer, callback)
        assert [item.config async for item in saver.alist(config)] == checkpoints
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        AIMessage(content=""),
        AIMessage(content="   "),
        AIMessage(content="done"),
        AIMessage(content="DEMO-001 done"),
        AIMessage(content="CASE-SAVED-001 done"),
        AIMessage(content="", tool_calls=[{"name": "create_case", "id": "new-call", "args": {}}]),
        HumanMessage(content=REPLY),
    ],
)
async def test_invalid_model_reply_is_not_saved(tmp_path, monkeypatch, response):
    callback, calls = responder(response)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "invalid.sqlite")) as saver:
        config, before, writes = await write_created(saver)
        with tracing_context(enabled=False), pytest.raises(AftersalesApprovalError):
            await recovery().reply_to_saved_case(saver, config, "learner-001", callback)
        graph = build_aftersales_graph(
            RunnableLambda(lambda _: None),
            [build_case_proposal_tool()],
            checkpointer=saver,
            customer_id="learner-001",
            case_responder=callback,
        )
        after = await graph.aget_state(config)
        assert after.next == ("case_reply",)
        assert not after.values.get("case_reply")
        assert after.values["case_result"] == before.values["case_result"]
        assert len(after.values["messages"]) == 3
        assert len(writes) == len(calls) == 1


def launcher(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "case_reply_launcher", ROOT / "scripts/run_aftersales_agent.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_reply_cli_uses_plain_model_and_no_business_or_mcp_connection(
    tmp_path, monkeypatch, capsys
):
    cli = launcher(monkeypatch)
    model = RunnableLambda(lambda messages: AIMessage(content=REPLY))
    factory = Mock(return_value=model)
    monkeypatch.setattr(cli, "get_case_reply_model", factory)
    database = str(tmp_path / "cli.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        _, _, writes = await write_created(saver)

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
        "build_case_executor",
    ]:
        monkeypatch.setattr(cli, name, Mock(side_effect=AssertionError(name)))
    args = SimpleNamespace(
        host="127.0.0.1",
        port=3306,
        user="root",
        customer_id="learner-001",
        thread_id="case-reply-002",
        history=False,
        approval_status=False,
        reject_case=False,
        approve_case=False,
        reply_case=True,
        request_approval=False,
        message=None,
        tool_transport="local",
    )
    with tracing_context(enabled=False):
        await cli.run_agent(args, "PRIVATE")
        await cli.run_agent(args, "PRIVATE")
    output = capsys.readouterr().out
    assert "CASE_REPLY_SAVED: True" in output
    assert "FINAL_ANSWER: " + REPLY in output
    assert "CHECKPOINT_MESSAGE_COUNT: 4" in output
    assert "CHECKPOINT_NEXT: []" in output
    assert "PRIVATE" not in output
    assert len(writes) == factory.call_count == 1


@pytest.mark.parametrize(
    "extra",
    [
        ["--reply-case"],
        ["--reply-case", "--message", "hi", "--thread-id", "task"],
        ["--reply-case", "--approve-case", "--thread-id", "task"],
        ["--reply-case", "--request-approval", "--thread-id", "task", "--tool-transport", "mcp"],
    ],
)
def test_invalid_reply_cli_stops_before_credentials(monkeypatch, extra):
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


def test_valid_reply_cli_is_accepted(monkeypatch):
    cli = launcher(monkeypatch)
    seen = []

    async def capture(args, password):
        seen.append((args.reply_case, args.thread_id, password))

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
            "--reply-case",
        ],
    )
    assert cli.main() == 0
    assert seen == [(True, "task", "PRIVATE")]
