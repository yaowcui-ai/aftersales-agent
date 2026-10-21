import importlib
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import SecretStr

from aftersales.approvals import CaseApprovalState, request_case_approval
from aftersales.checkpoints import build_checkpoint_config


def api():
    return importlib.import_module("service.aftersales")


def reader():
    return importlib.import_module("aftersales.task_state")


async def seed(saver, *, customer="learner-001", thread="http-state-002", waiting=False):
    pending = {
        "customer_id": customer,
        "request_id": "SAVED-REQUEST-002",
        "tool_call_id": "saved-call-002",
        "order_id": "DEMO-001",
        "description": "派送暂缓",
        "status": "waiting_approval" if waiting else "created",
    }
    record = {
        "case_ready": True,
        "customer_id": customer,
        "request_id": pending["request_id"],
        "case": {"case_id": "SAVED-CASE-002", "status": "open"},
    }
    messages = [
        HumanMessage(content="帮我登记工单。"),
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
        "approval_decision": None if waiting else True,
        "case_result": None if waiting else record,
        "case_reply": None if waiting else "已登记工单 SAVED-CASE-002，待处理。",
    }
    builder = StateGraph(CaseApprovalState)
    if waiting:
        builder.add_node("case_approval", request_case_approval)
        builder.add_edge(START, "case_approval")
        builder.add_edge("case_approval", END)
    else:
        messages.extend(
            [
                ToolMessage(content="saved result", tool_call_id=pending["tool_call_id"]),
                AIMessage(content=state["case_reply"]),
            ]
        )
        builder.add_node("seed", lambda _: {})
        builder.add_edge(START, "seed")
        builder.add_edge("seed", END)
    config = build_checkpoint_config(customer, thread)
    with tracing_context(enabled=False):
        await builder.compile(checkpointer=saver).ainvoke(state, config=config, durability="sync")
    return config, state


def make_app(saver=None):
    instance = FastAPI()
    instance.include_router(api().router)
    if saver is not None:
        instance.state.aftersales_checkpointer = saver
    return instance


@pytest.mark.asyncio
async def test_get_saved_task_projects_result_and_reply_without_changing_checkpoints(
    tmp_path, monkeypatch
):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "state.sqlite")) as saver:
        config, saved = await seed(saver)
        before = [item.config async for item in saver.alist(config)]
        instance = make_app(saver)
        async with AsyncClient(
            transport=ASGITransport(app=instance), base_url="http://test"
        ) as client:
            response = await client.get("/aftersales/tasks/http-state-002")
            repeated = await client.get("/aftersales/tasks/http-state-002")
        assert response.status_code == 200
        result = response.json()
        assert result == {
            "thread_id": "http-state-002",
            "pending_case": saved["pending_case"],
            "approval_decision": True,
            "case_result": saved["case_result"],
            "final_answer": saved["case_reply"],
            "message_count": 4,
            "next_nodes": [],
        }
        assert repeated.json() == result
        assert [item.config async for item in saver.alist(config)] == before


@pytest.mark.asyncio
async def test_waiting_task_reports_approval_node_without_resuming(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "waiting.sqlite")) as saver:
        config, _ = await seed(saver, waiting=True)
        before = [item.config async for item in saver.alist(config)]
        async with AsyncClient(
            transport=ASGITransport(app=make_app(saver)), base_url="http://test"
        ) as client:
            response = await client.get("/aftersales/tasks/http-state-002")
        assert response.status_code == 200
        assert response.json()["next_nodes"] == ["case_approval"]
        assert response.json()["approval_decision"] is None
        assert response.json()["pending_case"]["status"] == "waiting_approval"
        assert [item.config async for item in saver.alist(config)] == before


@pytest.mark.asyncio
async def test_client_identity_cannot_select_another_customer(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "scope.sqlite")) as saver:
        _, own = await seed(saver)
        await seed(saver, customer="learner-002", thread="other-customer-task")
        async with AsyncClient(
            transport=ASGITransport(app=make_app(saver)), base_url="http://test"
        ) as client:
            spoof = await client.get(
                "/aftersales/tasks/http-state-002?customer_id=learner-002&user_id=learner-002",
                headers={"X-Customer-ID": "learner-002"},
            )
            denied = await client.get(
                "/aftersales/tasks/other-customer-task?customer_id=learner-002"
            )
            missing = await client.get("/aftersales/tasks/missing-task")
        assert spoof.status_code == 200
        assert spoof.json()["pending_case"] == own["pending_case"]
        assert denied.status_code == missing.status_code == 404
        assert denied.json() == missing.json()


@pytest.mark.asyncio
async def test_server_identity_dependency_controls_scope(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "identity.sqlite")) as saver:
        await seed(saver)
        instance = make_app(saver)
        instance.dependency_overrides[api().get_demo_customer_id] = lambda: "learner-002"
        async with AsyncClient(
            transport=ASGITransport(app=instance), base_url="http://test"
        ) as client:
            response = await client.get("/aftersales/tasks/http-state-002")
        assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("thread", ["bad.id", "x" * 65, "aftersales:learner-002:task"])
async def test_invalid_task_path_is_rejected_before_checkpoint_read(monkeypatch, thread):
    module = reader()
    core = AsyncMock(side_effect=AssertionError("checkpoint read must not run"))
    monkeypatch.setattr(module, "read_task_snapshot", core)
    async with AsyncClient(
        transport=ASGITransport(app=make_app(object())), base_url="http://test"
    ) as client:
        response = await client.get("/aftersales/tasks/" + thread)
    assert response.status_code == 422
    core.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_shared_saver_is_503():
    async with AsyncClient(
        transport=ASGITransport(app=make_app()), base_url="http://test"
    ) as client:
        response = await client.get("/aftersales/tasks/http-state-002")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_driver_failure_does_not_expose_sql_or_private_data(tmp_path, monkeypatch, caplog):
    module = reader()
    monkeypatch.setattr(
        module,
        "read_task_snapshot",
        AsyncMock(side_effect=RuntimeError("SELECT PRIVATE_PASSWORD PRIVATE_ORDER")),
    )
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "error.sqlite")) as saver:
        async with AsyncClient(
            transport=ASGITransport(app=make_app(saver)), base_url="http://test"
        ) as client:
            response = await client.get("/aftersales/tasks/http-state-002")
    assert response.status_code == 503
    assert "PRIVATE" not in response.text + caplog.text
    assert "SELECT" not in response.text + caplog.text


@pytest.mark.asyncio
async def test_mismatched_saved_owner_is_not_returned(tmp_path, monkeypatch):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "owner.sqlite")) as saver:
        config, saved = await seed(saver)
        changed = deepcopy(saved["pending_case"])
        changed["customer_id"] = "PRIVATE_OTHER_CUSTOMER"
        builder = StateGraph(CaseApprovalState)
        builder.add_node("seed", lambda _: {})
        builder.add_edge(START, "seed")
        builder.add_edge("seed", END)
        await builder.compile(checkpointer=saver).aupdate_state(
            config, {"pending_case": changed}, as_node="seed"
        )
        async with AsyncClient(
            transport=ASGITransport(app=make_app(saver)), base_url="http://test"
        ) as client:
            response = await client.get("/aftersales/tasks/http-state-002")
    assert response.status_code == 503
    assert "PRIVATE_OTHER_CUSTOMER" not in response.text


@pytest.mark.asyncio
async def test_main_service_route_inherits_existing_bearer_auth(tmp_path, monkeypatch):
    from service import service

    monkeypatch.setattr(service.settings, "AUTH_SECRET", SecretStr("test-only-token"))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "auth.sqlite")) as saver:
        await seed(saver)
        monkeypatch.setattr(service.app.state, "aftersales_checkpointer", saver, raising=False)
        async with AsyncClient(
            transport=ASGITransport(app=service.app), base_url="http://test"
        ) as client:
            denied = await client.get("/aftersales/tasks/http-state-002")
            allowed = await client.get(
                "/aftersales/tasks/http-state-002",
                headers={"Authorization": "Bearer test-only-token"},
            )
        assert denied.status_code == 401
        assert allowed.status_code == 200
