import asyncio
import importlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import SecretStr


def api():
    return importlib.import_module("service.aftersales")


def conversation():
    return importlib.import_module("aftersales.conversation")


def test_swagger_example_is_a_real_case_request_instead_of_string_placeholder():
    instance = FastAPI()
    instance.include_router(api().router)
    schema = instance.openapi()["components"]["schemas"]["TaskMessageInput"]
    example = schema["example"]
    assert set(example) == {"message"}
    assert "DEMO-001" in example["message"]
    assert "工单" in example["message"]
    assert api().TaskMessageInput.model_validate(example).message == example["message"]


def setup_app(saver, monkeypatch, *, mode="proposal"):
    module = conversation()
    events = []

    @asynccontextmanager
    async def session(**kwargs):
        assert kwargs["customer_id"] == "learner-001"
        assert not kwargs.get("case_request_id")
        events.append("open")
        try:
            yield object()
        finally:
            events.append("close")

    @tool
    async def get_order(order_id: str):
        """Read an order in the current demo customer's scope."""
        assert events[-1] == "model"
        events.append("tool")
        return {"found": True, "order": {"order_id": order_id, "status": "shipped"}}

    async def load(_):
        return [get_order]

    async def model(messages):
        assert "open" in events and "close" not in events
        events.append("model")
        if mode == "failure":
            raise RuntimeError("PRIVATE_PASSWORD SELECT PRIVATE_CUSTOMER")
        if mode == "proposal":
            await asyncio.sleep(0)  # allow a concurrent HTTP request to contend
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "create_case",
                        "id": "http-proposal-call",
                        "args": {"order_id": "DEMO-001", "description": "派送暂缓"},
                    }
                ],
            )
        if isinstance(messages[-1], HumanMessage) and "查询" in messages[-1].content:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_order",
                        "id": "http-read-call",
                        "args": {"order_id": "DEMO-001"},
                    }
                ],
            )
        return AIMessage(content="DEMO-001 已发货。")

    def bound(tools):
        assert {item.name for item in tools} == {"get_order", "create_case"}
        return RunnableLambda(model)

    monkeypatch.setattr(module, "order_mcp_session", session)
    monkeypatch.setattr(module, "load_order_mcp_tools", load)
    monkeypatch.setattr(module, "get_bound_model", bound)
    monkeypatch.setattr(
        api(),
        "get_mcp_connection",
        lambda: {
            "host": "unused",
            "port": 3306,
            "username": "unused",
            "password": "unused",
        },
    )
    instance = FastAPI()
    instance.include_router(api().router)
    instance.state.aftersales_checkpointer = saver
    return instance, events


@pytest.mark.asyncio
async def test_post_proposal_persists_interrupt_then_get_reads_without_execution(
    tmp_path, monkeypatch
):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "case.sqlite")) as saver:
        app, events = setup_app(saver, monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/aftersales/tasks/http-new/messages", json={"message": "登记工单"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["pending_case"]["status"] == "waiting_approval"
            assert data["pending_case"]["customer_id"] == "learner-001"
            assert len(data["pending_case"]["request_id"]) == 32
            assert data["approval_decision"] is data["case_result"] is data["final_answer"] is None
            assert data["next_nodes"] == ["case_approval"]
            assert data["message_count"] == 2
            before = list(events)
            state = await client.get("/aftersales/tasks/http-new")
            blocked = await client.post(
                "/aftersales/tasks/http-new/messages", json={"message": "我同意"}
            )
            assert state.json() == data
            assert blocked.status_code == 409
            assert events == before == ["open", "model", "close"]


@pytest.mark.asyncio
async def test_read_tool_lives_inside_session_and_followup_keeps_history(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "read.sqlite")) as saver:
        app, events = setup_app(saver, monkeypatch, mode="read")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(
                "/aftersales/tasks/read/messages", json={"message": "查询 DEMO-001"}
            )
            assert first.status_code == 200
            assert first.json()["final_answer"] == "DEMO-001 已发货。"
            assert first.json()["message_count"] == 4
            assert first.json()["next_nodes"] == []
            assert events == ["open", "model", "tool", "model", "close"]
            events.clear()
            second = await client.post("/aftersales/tasks/read/messages", json={"message": "谢谢"})
            assert second.status_code == 200
            assert second.json()["message_count"] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"message": ""},
        {"message": "   "},
        {"message": 123},
        {"message": "x" * 4001},
        {"message": "登记", "customer_id": "learner-002"},
        {"message": "登记", "request_id": "spoof"},
        {"message": "登记", "approved": True},
    ],
)
async def test_invalid_body_cannot_run_agent(tmp_path, monkeypatch, body):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "body.sqlite")) as saver:
        app, events = setup_app(saver, monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/aftersales/tasks/input/messages", json=body)
        assert response.status_code == 422
        assert not events


@pytest.mark.asyncio
async def test_concurrent_same_task_proposals_run_once(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "race.sqlite")) as saver:
        app, events = setup_app(saver, monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            results = await asyncio.gather(
                *[
                    client.post("/aftersales/tasks/race/messages", json={"message": "登记工单"})
                    for _ in range(2)
                ]
            )
        assert sorted(r.status_code for r in results) == [200, 409]
        assert events == ["open", "model", "close"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["created", "rejected"])
async def test_terminal_application_requires_new_task(tmp_path, monkeypatch, status):
    from aftersales.approvals import build_case_proposal_tool
    from aftersales.checkpoints import build_checkpoint_config
    from aftersales.graph import build_aftersales_graph

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "ended.sqlite")) as saver:
        app, events = setup_app(saver, monkeypatch)
        graph = build_aftersales_graph(
            RunnableLambda(lambda _: None),
            [build_case_proposal_tool()],
            checkpointer=saver,
            customer_id="learner-001",
        )
        await graph.aupdate_state(
            build_checkpoint_config("learner-001", "ended"),
            {
                "pending_case": {"status": status},
                "messages": [],
            },
            as_node="reject_case",
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/aftersales/tasks/ended/messages", json={"message": "再登记"}
            )
        assert response.status_code == 409
        assert not events


@pytest.mark.asyncio
async def test_failed_run_is_sanitized_and_not_silently_resumed(tmp_path, monkeypatch, caplog):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "failed.sqlite")) as saver:
        app, events = setup_app(saver, monkeypatch, mode="failure")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/aftersales/tasks/failure/messages", json={"message": "你好"}
            )
            repeated = await client.post(
                "/aftersales/tasks/failure/messages", json={"message": "重试"}
            )
        assert response.status_code == 503
        assert repeated.status_code == 409
        assert events == ["open", "model", "close"]
        assert "PRIVATE" not in response.text + caplog.text


@pytest.mark.asyncio
async def test_main_service_post_requires_bearer_before_running(monkeypatch):
    from service import service

    runner = AsyncMock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr(api(), "submit_task_message", runner)
    monkeypatch.setattr(service.settings, "AUTH_SECRET", SecretStr("test-only"))
    monkeypatch.setattr(service.app.state, "aftersales_checkpointer", object(), raising=False)
    async with AsyncClient(
        transport=ASGITransport(app=service.app), base_url="http://test"
    ) as client:
        denied = await client.post("/aftersales/tasks/auth/messages", json={"message": "你好"})
    assert denied.status_code == 401
    runner.assert_not_awaited()
