"""Faults are injected, not inferred from successful HTTP responses."""
import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def store(tmp_path):
    from aftersales_delivery.business import Store
    from aftersales_delivery.db import initialize, seed_platform

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fault.sqlite'}")
    await initialize(engine)
    await seed_platform(engine)
    yield Store(engine)
    await engine.dispose()


async def approve(store, thread="fault"):
    action = await store.prepare("learner-001", thread, "refund", "UP-PAID", "fault test", "10.00")
    await store.decide(action["action_id"], True, 1, "reviewer", reason="validated")
    return action


@pytest.mark.asyncio
async def test_database_statement_failure_rolls_back_then_retries_once(store):
    from aftersales_delivery.db import refunds, cases
    action = await approve(store)

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if "INSERT INTO refunds" in statement:
            raise RuntimeError("INJECTED_DATABASE_WRITE_FAILURE")

    event.listen(store.engine.sync_engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="INJECTED_DATABASE_WRITE_FAILURE"):
            await store.execute(action["action_id"])
    finally:
        event.remove(store.engine.sync_engine, "before_cursor_execute", fail_insert)
    assert (await store.get_order("learner-001", "UP-PAID"))["refunded_amount"] == "0.00"
    assert (await store.get_action(action["action_id"]))["status"] == "approved"
    async with store.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(refunds)) == 0
        assert await conn.scalar(select(func.count()).select_from(cases)) == 0
    await store.execute(action["action_id"])
    await store.execute(action["action_id"])
    async with store.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(refunds)) == 1


@pytest.mark.asyncio
async def test_business_commit_lost_reply_does_not_duplicate_after_pool_restart(store):
    from aftersales_delivery.business import Store
    from aftersales_delivery.db import refunds
    action = await approve(store)

    async def lost_response():
        await store.execute(action["action_id"])
        raise ConnectionError("INJECTED_RESPONSE_LOSS_AFTER_COMMIT")

    with pytest.raises(ConnectionError):
        await lost_response()
    await store.engine.dispose()
    restarted = Store(store.engine)
    result = await restarted.execute(action["action_id"])
    assert result["status"] == "completed"
    assert (await restarted.get_order("learner-001", "UP-PAID"))["refunded_amount"] == "10.00"
    async with store.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(refunds)) == 1


@pytest.mark.asyncio
async def test_http500_and_timeout_read_tools_exhaust_without_business_write(store):
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from aftersales_delivery.agent import build_graph, graph_config
    from aftersales_delivery.db import actions, refunds

    for error in (TimeoutError, ConnectionError):
        calls = []

        async def model(messages, tools):
            if messages[-1].type == "tool":
                return AIMessage(content="查询服务不可用，请稍后读取状态。")
            return AIMessage(content="", tool_calls=[{"name": "get_order", "id": "get", "args": {"order_id": "UP-PAID"}}])

        async def read(name, args):
            calls.append(name)
            raise error("INJECTED_READ_TRANSPORT_ERROR")

        async def forbidden(*args):
            raise AssertionError("No business execution allowed")

        async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
            graph = build_graph(store, saver, "learner-001", error.__name__, model, read, forbidden, forbidden)
            result = await graph.ainvoke({"messages": [{"role": "user", "content": "查询订单"}], "turn_calls": 0},
                                         graph_config("learner-001", error.__name__))
            assert result["status"] == "answered" and len(calls) == 3
            assert not result.get("proposal")
    async with store.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(actions)) == 0
        assert await conn.scalar(select(func.count()).select_from(refunds)) == 0


@pytest.mark.asyncio
async def test_cancel_versus_execute_never_causes_partial_refund(store):
    import asyncio
    from aftersales_delivery.business import BusinessError
    action = await approve(store)
    results = await asyncio.gather(store.execute(action["action_id"]),
        store.cancel_action("learner-001", action["action_id"]), return_exceptions=True)
    final = await store.get_action(action["action_id"])
    amount = (await store.get_order("learner-001", "UP-PAID"))["refunded_amount"]
    assert (final["status"], amount) in {("completed", "10.00"), ("cancelled", "0.00")}
    assert any(isinstance(result, BusinessError) for result in results)


@pytest.mark.asyncio
async def test_actual_http500_mcp_response_is_bounded_without_business_effect(store, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from aftersales_delivery import transport
    from aftersales_delivery.agent import build_graph, graph_config
    from aftersales_delivery.config import Config
    from aftersales_delivery.db import actions

    app = FastAPI()
    requests = []

    @app.post("/mcp")
    async def broken_mcp():
        requests.append(500)
        return JSONResponse({"error": "INJECTED_HTTP500"}, status_code=500)

    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers=headers, timeout=timeout, auth=auth)

    monkeypatch.setattr(transport, "http_client", factory)
    config = Config("sqlite+aiosqlite:///:memory:", "key" * 16, {}, mcp_url="http://test/mcp")

    async def model(messages, tools):
        if messages[-1].type == "tool":
            return AIMessage(content="查询服务暂时不可用，未创建申请。")
        return AIMessage(content="", tool_calls=[{"name": "get_order", "id": "get", "args": {"order_id": "UP-PAID"}}])

    async def forbidden(*args):
        raise AssertionError("No business effects allowed")

    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = build_graph(store, saver, "learner-001", "http500", model,
            lambda name, args: transport.call_tool(config, "learner-001", name, args), forbidden, forbidden)
        state = await graph.ainvoke({"messages": [{"role": "user", "content": "查询订单"}], "turn_calls": 0},
                                    graph_config("learner-001", "http500"))
        assert state["status"] == "answered" and not state.get("proposal")
    assert requests == [500, 500, 500]
    async with store.engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(actions)) == 0
