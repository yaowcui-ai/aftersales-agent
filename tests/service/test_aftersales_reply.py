import asyncio
import importlib
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langsmith import tracing_context
from pydantic import SecretStr

from aftersales.approvals import build_case_proposal_tool
from aftersales.checkpoints import build_checkpoint_config
from aftersales.graph import build_aftersales_graph


def api():
    return importlib.import_module("service.aftersales")


async def seed(saver, *, customer="learner-001", thread="reply-http", kind="created"):
    payload = {
        "order_id": "DEMO-001",
        "case_type": "logistics_exception",
        "description": "派送暂缓",
    }
    pending = {
        "customer_id": customer,
        "request_id": "HTTP-REPLY-REQUEST",
        "tool_call_id": "http-reply-call",
        "order_id": payload["order_id"],
        "description": payload["description"],
        "status": kind,
    }
    record = {
        "case_ready": True,
        "customer_id": customer,
        "request_id": pending["request_id"],
        "request_payload": payload,
        "case": {"case_id": "HTTP-REPLY-CASE", **payload, "status": "open"},
    }
    messages = [
        HumanMessage(content="请登记工单。"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "create_case",
                    "id": pending["tool_call_id"],
                    "args": {
                        "order_id": pending["order_id"],
                        "description": pending["description"],
                    },
                }
            ],
        ),
    ]
    state = {
        "messages": messages,
        "pending_case": pending,
        "approval_decision": kind == "created",
        "case_result": record if kind == "created" else None,
    }
    node = "execute_case"
    if kind == "created":
        messages.append(
            ToolMessage(content=json.dumps(record), tool_call_id=pending["tool_call_id"])
        )
    elif kind == "waiting_approval":
        state["approval_decision"] = None
        node = "prepare_case"
    elif kind == "rejected":
        messages.append(
            ToolMessage(
                content='{"error":"CASE_REJECTED"}',
                tool_call_id=pending["tool_call_id"],
                status="error",
            )
        )
        node = "reject_case"
    else:
        state.update(pending_case=None, approval_decision=None)
        state["messages"] = [HumanMessage(content="你好"), AIMessage(content="请提供订单号。")]
        node = "model"
    graph = build_aftersales_graph(
        RunnableLambda(lambda _: None),
        [build_case_proposal_tool()],
        checkpointer=saver,
        customer_id=customer,
    )
    config = build_checkpoint_config(customer, thread)
    with tracing_context(enabled=False):
        await graph.aupdate_state(config, state, as_node=node)
    return config, deepcopy(state)


def setup_app(saver, monkeypatch, *, mode="ok"):
    calls = {"factory": 0, "model": 0}

    async def respond(messages):
        calls["model"] += 1
        assert isinstance(messages[-1], ToolMessage)
        assert json.loads(messages[-1].content)["case"]["case_id"] == "HTTP-REPLY-CASE"
        await asyncio.sleep(0)
        if mode == "fail_once" and calls["model"] == 1:
            raise RuntimeError("PRIVATE_MODEL_TOKEN_AND_PAYLOAD")
        if mode == "invalid":
            return AIMessage(content="已完成。")
        return AIMessage(content="订单 DEMO-001 的工单 HTTP-REPLY-CASE 已登记，当前待处理。")

    def factory():
        calls["factory"] += 1
        return RunnableLambda(respond)

    def forbidden():
        raise AssertionError("Final reply must not configure case writing or MCP")

    monkeypatch.setattr(api(), "get_case_reply_model", factory)
    monkeypatch.setattr(api(), "get_mcp_connection", forbidden)
    monkeypatch.setattr(api(), "build_case_executor", forbidden)
    app = FastAPI()
    app.include_router(api().router)
    app.state.aftersales_checkpointer = saver
    return app, calls


@pytest.mark.asyncio
async def test_reply_adds_message_then_get_and_replay_do_not_call_model(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "reply.sqlite")) as saver:
        _, original = await seed(saver)
        app, calls = setup_app(saver, monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/aftersales/tasks/reply-http/reply?customer_id=learner-002",
                headers={"X-Customer-ID": "learner-002"},
            )
            assert r.status_code == 200
            d = r.json()
            assert "HTTP-REPLY-CASE" in d["final_answer"]
            assert d["pending_case"] == original["pending_case"]
            assert d["case_result"] == original["case_result"]
            assert d["approval_decision"] is True
            assert d["message_count"] == 4 and d["next_nodes"] == []
            get = await client.get("/aftersales/tasks/reply-http")
            again = await client.post("/aftersales/tasks/reply-http/reply")
            assert get.json() == again.json() == d
            assert calls == {"factory": 1, "model": 1}


@pytest.mark.asyncio
async def test_concurrent_replies_share_one_saved_answer(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "race.sqlite")) as saver:
        await seed(saver)
        app, calls = setup_app(saver, monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            results = await asyncio.gather(
                *[client.post("/aftersales/tasks/reply-http/reply") for _ in range(2)]
            )
        assert all(r.status_code == 200 for r in results)
        assert results[0].json() == results[1].json()
        assert calls == {"factory": 1, "model": 1}


@pytest.mark.asyncio
async def test_failed_reply_only_retries_reply_and_keeps_case(tmp_path, monkeypatch, caplog):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "failure.sqlite")) as saver:
        _, original = await seed(saver)
        app, calls = setup_app(saver, monkeypatch, mode="fail_once")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            failed = await client.post("/aftersales/tasks/reply-http/reply")
            assert failed.status_code == 503
            assert "PRIVATE" not in failed.text + caplog.text
            state = await client.get("/aftersales/tasks/reply-http")
            assert state.json()["case_result"] == original["case_result"]
            assert state.json()["next_nodes"] == ["case_reply"]
            assert state.json()["final_answer"] is None
            retry = await client.post("/aftersales/tasks/reply-http/reply")
            assert retry.status_code == 200
            assert retry.json()["case_result"] == original["case_result"]
            assert retry.json()["pending_case"] == original["pending_case"]
            assert retry.json()["message_count"] == 4
            assert calls == {"factory": 2, "model": 2}


@pytest.mark.asyncio
async def test_invalid_model_answer_is_not_saved_as_success(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "invalid.sqlite")) as saver:
        await seed(saver)
        app, _ = setup_app(saver, monkeypatch, mode="invalid")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/aftersales/tasks/reply-http/reply")
            state = await client.get("/aftersales/tasks/reply-http")
        assert r.status_code == 503
        assert state.json()["final_answer"] is None
        assert state.json()["next_nodes"] == ["case_reply"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["waiting_approval", "rejected", "read"])
async def test_unready_task_does_not_instantiate_model(tmp_path, monkeypatch, kind):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "unready.sqlite")) as saver:
        await seed(saver, kind=kind)
        app, calls = setup_app(saver, monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/aftersales/tasks/reply-http/reply")
        assert r.status_code == 409
        assert calls == {"factory": 0, "model": 0}


@pytest.mark.asyncio
async def test_missing_and_other_customer_tasks_are_indistinguishable(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "scope.sqlite")) as saver:
        await seed(saver, customer="learner-002", thread="other")
        app, calls = setup_app(saver, monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            missing = await client.post("/aftersales/tasks/missing/reply")
            other = await client.post("/aftersales/tasks/other/reply?customer_id=learner-002")
        assert missing.status_code == other.status_code == 404
        assert missing.json() == other.json()
        assert calls == {"factory": 0, "model": 0}


@pytest.mark.asyncio
async def test_reply_inherits_service_auth_before_recovery(monkeypatch):
    from service import service

    runner = AsyncMock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr(api(), "submit_case_reply", runner)
    monkeypatch.setattr(service.settings, "AUTH_SECRET", SecretStr("test-only"))
    monkeypatch.setattr(service.app.state, "aftersales_checkpointer", object(), raising=False)
    async with AsyncClient(
        transport=ASGITransport(app=service.app), base_url="http://test"
    ) as client:
        r = await client.post("/aftersales/tasks/private/reply")
    assert r.status_code == 401
    runner.assert_not_awaited()


def test_reply_openapi_has_no_body_or_customer_parameter():
    from service import app

    entry = app.openapi()["paths"]["/aftersales/tasks/{thread_id}/reply"]["post"]
    assert entry["security"] == [{"HTTPBearer": []}]
    assert [p["name"] for p in entry["parameters"]] == ["thread_id"]
    assert "requestBody" not in entry
