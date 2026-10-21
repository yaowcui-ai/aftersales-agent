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
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langsmith import tracing_context

from aftersales.checkpoints import build_checkpoint_config
from aftersales.graph import build_aftersales_graph

ROOT = Path(__file__).resolve().parents[2]


def reader():
    return importlib.import_module("aftersales.checkpoints").read_checkpoint_history


def launcher(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "aftersales_history_launcher", ROOT / "scripts/run_aftersales_agent.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def write_history(saver):
    @tool
    async def get_order(order_id: str) -> str:
        """Read the test customer's order."""
        return json.dumps({"found": True, "order": {"order_id": order_id}})

    async def respond(messages):
        if isinstance(messages[-1], HumanMessage):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_order",
                        "args": {"order_id": "DEMO-001"},
                        "id": "saved-call-001",
                        "type": "tool_call",
                    }
                ],
            )
        assert isinstance(messages[-1], ToolMessage)
        return AIMessage(content="已查到 DEMO-001。")

    config = build_checkpoint_config("learner-001", "history-001")
    with tracing_context(enabled=False):
        graph = build_aftersales_graph(RunnableLambda(respond), [get_order], checkpointer=saver)
        await graph.ainvoke(
            {"messages": [HumanMessage(content="请查 DEMO-001。")]},
            config=config,
            durability="sync",
        )
    return config


@pytest.mark.asyncio
async def test_read_history_after_writer_closes_preserves_messages_and_call_ids(tmp_path):
    read = reader()
    database = str(tmp_path / "history.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        config = await write_history(saver)
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        messages = await read(saver, config)
        assert [message.type for message in messages] == ["human", "ai", "tool", "ai"]
        assert messages[0].text == "请查 DEMO-001。"
        assert messages[1].tool_calls[0]["id"] == messages[2].tool_call_id == "saved-call-001"
        assert json.loads(messages[2].text)["order"]["order_id"] == "DEMO-001"
        assert messages[3].text == "已查到 DEMO-001。"
        # A read must not append a question, invoke a node or create another checkpoint.
        before = [item.config async for item in saver.alist(config)]
        assert await read(saver, config) == messages
        after = [item.config async for item in saver.alist(config)]
        assert after == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("customer_id", "thread_id"),
    [("learner-002", "history-001"), ("learner-001", "different-task")],
)
async def test_other_customer_or_task_does_not_return_saved_history(
    tmp_path, customer_id, thread_id
):
    read = reader()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "scoped.sqlite")) as saver:
        await write_history(saver)
        assert await read(saver, build_checkpoint_config(customer_id, thread_id)) is None


@pytest.mark.asyncio
async def test_history_mode_does_not_invoke_model_or_mcp_or_business_db(
    monkeypatch, tmp_path, capsys
):
    module = launcher(monkeypatch)
    database = str(tmp_path / "launcher.sqlite")
    async with AsyncSqliteSaver.from_conn_string(database) as saver:
        await write_history(saver)

    closed = []

    @asynccontextmanager
    async def open_saver(**kwargs):
        assert kwargs == dict(host="127.0.0.1", port=3306, username="root", password="PRIVATE")
        async with AsyncSqliteSaver.from_conn_string(database) as saver:
            try:
                yield saver
            finally:
                closed.append(True)

    monkeypatch.setattr(module, "aftersales_checkpointer", open_saver)
    for name in (
        "get_bound_model",
        "order_mcp_session",
        "load_order_mcp_tools",
        "run_graph_with_tools",
        "run_local_agent",
        "create_async_engine",
    ):
        monkeypatch.setattr(module, name, Mock(side_effect=AssertionError(name + " must not run")))
    args = SimpleNamespace(
        host="127.0.0.1",
        port=3306,
        user="root",
        customer_id="learner-001",
        thread_id="history-001",
        history=True,
        message=None,
        tool_transport="mcp",
    )
    await module.run_agent(args, "PRIVATE")
    output = capsys.readouterr().out
    assert "CHECKPOINT_THREAD_ID: aftersales:learner-001:history-001" in output
    assert "HISTORY_FOUND: True" in output
    assert "HISTORY_MESSAGE_COUNT: 4" in output
    entries = [
        json.loads(line.partition(": ")[2])
        for line in output.splitlines()
        if line.startswith("HISTORY_MESSAGE: ")
    ]
    assert [entry["type"] for entry in entries] == ["human", "ai", "tool", "ai"]
    assert entries[1]["tool_calls"][0]["id"] == entries[2]["tool_call_id"]
    assert "PRIVATE" not in output
    assert closed == [True]


@pytest.mark.parametrize(
    "extra",
    [["--history"], ["--history", "--message", "hello", "--thread-id", "task"], []],
)
def test_invalid_history_cli_stops_before_credentials_or_connection(monkeypatch, extra):
    module = launcher(monkeypatch)
    monkeypatch.setattr(module, "get_mysql_password", Mock(side_effect=AssertionError("password")))
    monkeypatch.setattr(module, "run_agent", Mock(side_effect=AssertionError("connection")))
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_aftersales_agent.py", "--user", "root", "--customer-id", "learner-001", *extra],
    )
    with pytest.raises(SystemExit) as result:
        module.main()
    assert result.value.code == 2
