import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langsmith import tracing_context

from aftersales import checkpoints
from aftersales.approvals import build_case_proposal_tool
from aftersales.graph import build_aftersales_graph

ROOT = Path(__file__).resolve().parents[2]


def launcher(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "aftersales_approval_status_launcher", ROOT / "scripts/run_aftersales_agent.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def write_pending(saver):
    config = checkpoints.build_checkpoint_config("learner-001", "approval-status-001")
    response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "create_case",
                "args": {"order_id": "DEMO-001", "description": "派送暂缓。"},
                "id": "approval-call-001",
                "type": "tool_call",
            }
        ],
    )
    with tracing_context(enabled=False):
        graph = build_aftersales_graph(
            RunnableLambda(lambda _: response),
            [build_case_proposal_tool()],
            checkpointer=saver,
            customer_id="learner-001",
        )
        await graph.ainvoke(
            {"messages": [HumanMessage(content="请登记物流异常。")]},
            config=config,
            durability="sync",
        )
        snapshot = await graph.aget_state(config)
    assert snapshot.next == ("case_approval",)
    return config, snapshot.values["pending_case"]


@pytest.mark.asyncio
async def test_new_connection_reads_exact_pending_request_without_writing(tmp_path):
    read = checkpoints.read_checkpoint_approval
    database = str(tmp_path / "pending.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        config, pending = await write_pending(saver)
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        before = [item.config async for item in saver.alist(config)]
        assert await read(saver, config) == {
            "pending_case": pending,
            "approval_decision": None,
        }
        assert await read(saver, config) == await read(saver, config)
        assert [item.config async for item in saver.alist(config)] == before
        for customer, thread in [
            ("learner-002", "approval-status-001"),
            ("learner-001", "other-task"),
        ]:
            assert await read(saver, checkpoints.build_checkpoint_config(customer, thread)) is None


@pytest.mark.asyncio
async def test_status_mode_never_starts_model_mcp_or_business_connection(
    monkeypatch, tmp_path, capsys
):
    module = launcher(monkeypatch)
    database = str(tmp_path / "cli.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        _, pending = await write_pending(saver)

    @asynccontextmanager
    async def open_saver(**kwargs):
        async with AsyncSqliteSaver.from_conn_string(database) as saver:
            yield saver

    monkeypatch.setattr(module, "aftersales_checkpointer", open_saver)
    for name in [
        "get_bound_model",
        "order_mcp_session",
        "load_order_mcp_tools",
        "run_graph_with_tools",
        "run_local_agent",
        "create_async_engine",
    ]:
        monkeypatch.setattr(module, name, Mock(side_effect=AssertionError(name)))
    args = SimpleNamespace(
        host="127.0.0.1",
        port=3306,
        user="root",
        customer_id="learner-001",
        thread_id="approval-status-001",
        history=False,
        approval_status=True,
        request_approval=False,
        message=None,
        tool_transport="local",
    )
    await module.run_agent(args, "PRIVATE")
    output = capsys.readouterr().out
    assert "CHECKPOINT_FOUND: True" in output
    assert "APPROVAL_DECISION: null" in output
    stored = next(
        line.partition(": ")[2] for line in output.splitlines() if line.startswith("PENDING_CASE: ")
    )
    assert json.loads(stored) == pending
    assert "PRIVATE" not in output


@pytest.mark.parametrize(
    "extra",
    [
        ["--approval-status"],
        ["--approval-status", "--history", "--thread-id", "task"],
        ["--approval-status", "--message", "hello", "--thread-id", "task"],
        [
            "--approval-status",
            "--request-approval",
            "--thread-id",
            "task",
            "--tool-transport",
            "mcp",
        ],
    ],
)
def test_invalid_status_mode_stops_before_credentials(monkeypatch, extra):
    module = launcher(monkeypatch)
    monkeypatch.setattr(module, "get_mysql_password", Mock(side_effect=AssertionError("password")))
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
        module.main()
    assert result.value.code == 2


def test_valid_status_mode_is_accepted(monkeypatch):
    module = launcher(monkeypatch)
    captured = []

    async def capture(args, password):
        captured.append((args.approval_status, args.thread_id, password))

    monkeypatch.setattr(module, "get_mysql_password", lambda: "PRIVATE")
    monkeypatch.setattr(module, "run_agent", capture)
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
            "--approval-status",
        ],
    )
    assert module.main() == 0
    assert captured == [(True, "task", "PRIVATE")]
