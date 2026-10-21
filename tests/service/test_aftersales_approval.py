import asyncio
import importlib
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langsmith import tracing_context
from mcp.types import CallToolResult, TextContent
from pydantic import SecretStr

from aftersales.approvals import build_case_proposal_tool
from aftersales.checkpoints import build_checkpoint_config
from aftersales.graph import build_aftersales_graph


def api():
    return importlib.import_module("service.aftersales")


def test_approval_openapi_exposes_only_decision_and_inherits_service_auth():
    from service import app

    spec = app.openapi()
    endpoint = spec["paths"]["/aftersales/tasks/{thread_id}/approval"]["post"]
    assert endpoint["security"] == [{"HTTPBearer": []}]
    assert [p["name"] for p in endpoint["parameters"]] == ["thread_id"]
    body = spec["components"]["schemas"]["TaskApprovalInput"]
    assert body["required"] == ["approved"]
    assert set(body["properties"]) == {"approved"}
    assert body["properties"]["approved"]["type"] == "boolean"
    assert body["additionalProperties"] is False
    assert body["example"] == {"approved": True}


async def seed(saver, *, customer="learner-001", thread="approval-http", proposal=True):
    async def model(_):
        if not proposal:
            return AIMessage(content="请提供订单号。")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "create_case",
                    "id": "http-approval-call",
                    "args": {"order_id": "DEMO-001", "description": "派送暂缓"},
                }
            ],
        )

    graph = build_aftersales_graph(
        RunnableLambda(model),
        [build_case_proposal_tool()],
        checkpointer=saver,
        customer_id=customer,
    )
    config = build_checkpoint_config(customer, thread)
    with tracing_context(enabled=False):
        await graph.ainvoke(
            {
                "messages": [HumanMessage(content="请登记工单。")],
            },
            config=config,
            durability="sync",
        )
    return await graph.aget_state(config)


def setup_app(saver, monkeypatch, *, fail_after_commit=False, reply_mode="ok"):
    execution = importlib.import_module("aftersales.execution")
    events = []
    records = {}
    failed = False

    @asynccontextmanager
    async def session(**kwargs):
        nonlocal failed
        events.append(
            {
                "event": "open",
                "customer": kwargs["customer_id"],
                "request": kwargs["case_request_id"],
            }
        )
        assert kwargs["customer_id"] == "learner-001"

        async def call_tool(name, arguments):
            nonlocal failed
            assert name == "create_case"
            assert set(arguments) == {"order_id", "description"}
            events.append({"event": "call", "arguments": arguments})
            key = (kwargs["customer_id"], kwargs["case_request_id"])
            payload = {**arguments, "case_type": "logistics_exception"}
            record = records.setdefault(
                key,
                {
                    "case_ready": True,
                    "customer_id": key[0],
                    "request_id": key[1],
                    "request_payload": payload,
                    "case": {"case_id": "HTTP-CASE-001", **payload, "status": "open"},
                },
            )
            if fail_after_commit and not failed:
                failed = True
                raise RuntimeError("PRIVATE_CONNECTION_DETAILS")
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(record))])

        try:
            yield type("Session", (), {"call_tool": staticmethod(call_tool)})()
        finally:
            events.append({"event": "close"})

    monkeypatch.setattr(execution, "order_mcp_session", session)
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
    app = FastAPI()
    app.include_router(api().router)
    app.state.aftersales_checkpointer = saver
    app.state.reply_model_calls = 0

    async def reply(messages):
        app.state.reply_model_calls += 1
        record = json.loads(messages[-1].content)
        assert record["case"]["case_id"] == "HTTP-CASE-001"
        await asyncio.sleep(0)
        if reply_mode == "fail_once" and app.state.reply_model_calls == 1:
            raise RuntimeError("PRIVATE_REPLY_CONNECTION")
        if reply_mode == "invalid":
            return AIMessage(content="已处理。")
        return AIMessage(content="订单 DEMO-001 工单 HTTP-CASE-001 已登记，待处理。")

    monkeypatch.setattr(api(), "get_case_reply_model", lambda: RunnableLambda(reply))
    return app, events, records


@pytest.mark.asyncio
async def test_approve_uses_saved_request_replays_and_refuses_opposite_decision(
    tmp_path, monkeypatch
):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "approve.sqlite")) as saver:
        original = await seed(saver)
        app, events, records = setup_app(saver, monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/aftersales/tasks/approval-http/approval?customer_id=learner-002",
                json={"approved": True},
                headers={"X-Customer-ID": "learner-002"},
            )
            assert response.status_code == 200
            d = response.json()
            assert d["approval_decision"] is True
            assert d["pending_case"] == {**original.values["pending_case"], "status": "created"}
            assert d["next_nodes"] == []
            assert "HTTP-CASE-001" in d["final_answer"]
            assert d["message_count"] == 4
            assert d["case_result"]["case"]["status"] == "open"
            assert events[0]["request"] == original.values["pending_case"]["request_id"]
            repeated = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": True}
            )
            opposite = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": False}
            )
            assert repeated.json() == d
            assert opposite.status_code == 409
            assert len(records) == 1
            assert [item["event"] for item in events] == ["open", "call", "close"]
            assert app.state.reply_model_calls == 1


@pytest.mark.asyncio
async def test_reject_and_replay_do_not_open_mcp_or_create_executor(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "reject.sqlite")) as saver:
        original = await seed(saver)
        app, events, records = setup_app(saver, monkeypatch)

        def forbidden():
            raise AssertionError("Rejection must not load tool credentials")

        monkeypatch.setattr(api(), "get_mcp_connection", forbidden)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": False}
            )
            again = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": False}
            )
            opposite = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": True}
            )
        assert r.status_code == again.status_code == 200
        assert r.json() == again.json()
        assert r.json()["pending_case"] == {**original.values["pending_case"], "status": "rejected"}
        assert r.json()["approval_decision"] is False
        assert r.json()["case_result"] is None
        assert r.json()["next_nodes"] == []
        assert opposite.status_code == 409
        assert not events and not records
        assert app.state.reply_model_calls == 0


@pytest.mark.asyncio
async def test_concurrent_same_decision_invokes_mcp_once(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "race.sqlite")) as saver:
        await seed(saver)
        app, events, records = setup_app(saver, monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            results = await asyncio.gather(
                *[
                    client.post("/aftersales/tasks/approval-http/approval", json={"approved": True})
                    for _ in range(2)
                ]
            )
        assert all(r.status_code == 200 for r in results)
        assert results[0].json() == results[1].json()
        assert sum(e["event"] == "call" for e in events) == len(records) == 1
        assert app.state.reply_model_calls == 1


@pytest.mark.asyncio
async def test_retry_after_commit_reuses_request_and_cannot_reject(tmp_path, monkeypatch, caplog):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "retry.sqlite")) as saver:
        original = await seed(saver)
        app, events, records = setup_app(saver, monkeypatch, fail_after_commit=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            failed = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": True}
            )
            assert failed.status_code == 503
            assert "PRIVATE" not in failed.text + caplog.text
            state = await client.get("/aftersales/tasks/approval-http")
            assert state.json()["approval_decision"] is True
            assert state.json()["case_result"] is None
            assert state.json()["next_nodes"] == ["execute_case"]
            refused = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": False}
            )
            assert refused.status_code == 409
            retry = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": True}
            )
        assert retry.status_code == 200
        assert len(records) == 1
        assert [e["request"] for e in events if e["event"] == "open"] == [
            original.values["pending_case"]["request_id"]
        ] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"approved": "true"},
        {"approved": "false"},
        {"approved": 1},
        {"approved": 0},
        {"approved": None},
        {"approved": []},
        {"approved": True, "customer_id": "learner-002"},
        {"approved": True, "request_id": "changed"},
        {"approved": True, "order_id": "DEMO-003"},
        {"approved": True, "description": "changed"},
    ],
)
async def test_invalid_decision_does_not_run_recovery(tmp_path, monkeypatch, body):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "invalid.sqlite")) as saver:
        await seed(saver)
        app, events, records = setup_app(saver, monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post("/aftersales/tasks/approval-http/approval", json=body)
        assert r.status_code == 422
        assert not events and not records


@pytest.mark.asyncio
async def test_missing_other_customer_and_no_proposal_task(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "scope.sqlite")) as saver:
        await seed(saver, customer="learner-002", thread="other-task")
        await seed(saver, thread="read-task", proposal=False)
        app, events, _ = setup_app(saver, monkeypatch)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            missing = await client.post(
                "/aftersales/tasks/missing/approval", json={"approved": True}
            )
            other = await client.post(
                "/aftersales/tasks/other-task/approval?customer_id=learner-002",
                json={"approved": True},
            )
            read = await client.post(
                "/aftersales/tasks/read-task/approval", json={"approved": True}
            )
        assert missing.status_code == other.status_code == 404
        assert missing.json() == other.json()
        assert read.status_code == 409
        assert not events


@pytest.mark.asyncio
async def test_service_approval_inherits_bearer(monkeypatch):
    from service import service

    runner = AsyncMock(side_effect=AssertionError("Must not run without auth"))
    monkeypatch.setattr(api(), "submit_case_workflow", runner)
    monkeypatch.setattr(service.settings, "AUTH_SECRET", SecretStr("test-only"))
    monkeypatch.setattr(service.app.state, "aftersales_checkpointer", object(), raising=False)
    async with AsyncClient(
        transport=ASGITransport(app=service.app), base_url="http://test"
    ) as client:
        r = await client.post("/aftersales/tasks/private/approval", json={"approved": True})
    assert r.status_code == 401
    runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_retry_after_reply_failure_never_recreates_case(
    tmp_path, monkeypatch, caplog
):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "reply-retry.sqlite")) as saver:
        await seed(saver)
        app, events, records = setup_app(saver, monkeypatch, reply_mode="fail_once")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            failed = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": True}
            )
            assert failed.status_code == 503
            assert "PRIVATE" not in failed.text + caplog.text
            saved = (await client.get("/aftersales/tasks/approval-http")).json()
            assert saved["approval_decision"] is True
            assert saved["pending_case"]["status"] == "created"
            assert saved["case_result"]["case"]["case_id"] == "HTTP-CASE-001"
            assert saved["next_nodes"] == ["case_reply"]
            before = list(events)

            def forbidden():
                raise AssertionError("Reply recovery must skip writer configuration")

            monkeypatch.setattr(api(), "get_mcp_connection", forbidden)
            refused = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": False}
            )
            assert refused.status_code == 409
            retried = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": True}
            )
            assert retried.status_code == 200
            final = retried.json()
            assert final["case_result"] == saved["case_result"]
            assert final["pending_case"] == saved["pending_case"]
            assert "HTTP-CASE-001" in final["final_answer"]
            assert final["next_nodes"] == []
            assert events == before
            assert len(records) == 1
            assert app.state.reply_model_calls == 2


@pytest.mark.asyncio
async def test_direct_reply_can_recover_combined_approval_failure(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "direct-reply.sqlite")) as saver:
        await seed(saver)
        app, events, records = setup_app(saver, monkeypatch, reply_mode="fail_once")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            failed = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": True}
            )
            assert failed.status_code == 503
            recovered = await client.post("/aftersales/tasks/approval-http/reply")
            again = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": True}
            )
            assert recovered.status_code == again.status_code == 200
            assert recovered.json() == again.json()
            assert app.state.reply_model_calls == 2
            assert len(records) == sum(e["event"] == "call" for e in events) == 1


@pytest.mark.asyncio
async def test_invalid_reply_in_combined_approval_is_503_after_case_creation(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "invalid-reply.sqlite")) as saver:
        await seed(saver)
        app, events, records = setup_app(saver, monkeypatch, reply_mode="invalid")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": True}
            )
            assert r.status_code == 503
            state = (await client.get("/aftersales/tasks/approval-http")).json()
            assert state["case_result"]["case"]["case_id"] == "HTTP-CASE-001"
            assert state["final_answer"] is None
            assert state["next_nodes"] == ["case_reply"]
            assert len(records) == sum(e["event"] == "call" for e in events) == 1


@pytest.mark.asyncio
async def test_legacy_created_task_gets_reply_without_creating_case_again(tmp_path, monkeypatch):
    from aftersales.decisions import submit_case_decision

    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "legacy.sqlite")) as saver:
        await seed(saver)
        app, events, records = setup_app(saver, monkeypatch)
        legacy = await submit_case_decision(
            saver,
            "learner-001",
            "approval-http",
            True,
            lambda: api().build_case_executor(
                **api().get_mcp_connection(), customer_id="learner-001"
            ),
        )
        assert legacy.values.get("case_reply") is None
        before = list(events)

        def forbidden():
            raise AssertionError("Legacy case must not be executed again")

        monkeypatch.setattr(api(), "get_mcp_connection", forbidden)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                "/aftersales/tasks/approval-http/approval", json={"approved": True}
            )
        assert r.status_code == 200
        assert r.json()["case_result"] == legacy.values["case_result"]
        assert "HTTP-CASE-001" in r.json()["final_answer"]
        assert events == before and len(records) == 1
        assert app.state.reply_model_calls == 1
