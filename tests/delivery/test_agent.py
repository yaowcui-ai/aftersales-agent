import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_real_graph_pauses_executes_approved_action_and_replays_without_duplicate(tmp_path):
    from aftersales_delivery.agent import build_graph, graph_config
    from aftersales_delivery.business import Store
    from aftersales_delivery.db import initialize

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'business.sqlite'}")
    await initialize(engine)
    store = Store(engine)
    calls = []

    async def model(messages, tools):
        calls.append(messages)
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "prepare_action",
                    "id": "prepare-1",
                    "args": {
                        "kind": "refund",
                        "order_id": "DEMO-004",
                        "description": "模拟退款",
                        "amount": "100.00",
                        "quantity": 1,
                    },
                }
            ],
        )

    async def read(name, args):
        raise AssertionError("not needed in this case")

    async def execute(action):
        return await store.execute(action["action_id"])

    async def reply(action):
        return f"模拟处理完成，工单 {action['result']['case_id']}"

    try:
        async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
            graph = build_graph(store, saver, "learner-001", "test-1", model, read, execute, reply)
            config = graph_config("learner-001", "test-1")
            await graph.ainvoke(
                {"messages": [{"role": "user", "content": "申请退款100元"}], "turn_calls": 0},
                config,
                durability="sync",
            )
            snapshot = await graph.aget_state(config)
            assert snapshot.next == ("approval",)
            action = snapshot.values["proposal"]
            assert action["status"] == "waiting_approval"
            await store.decide(action["action_id"], True, 1, "reviewer")
            from langgraph.types import Command

            await graph.ainvoke(Command(resume=True), config, durability="sync")
            result = (await graph.aget_state(config)).values
            assert result["status"] == "completed"
            assert result["proposal"]["result"]["case_id"] in result["final_answer"]
            before = await store.get_order("learner-001", "DEMO-004")
            await execute(result["proposal"])
            assert await store.get_order("learner-001", "DEMO-004") == before
            assert len(calls) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mcp_http_rejects_session_token_and_scopes_execute(tmp_path):
    from httpx import ASGITransport, AsyncClient

    from aftersales_delivery.config import Config
    from aftersales_delivery.mcp_server import create_app
    from aftersales_delivery.security import issue_token

    config = Config(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}", "key" * 16, {})
    app = create_app(config)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.post("/mcp")).status_code == 401
        token = issue_token(config.signing_key, "customer1", "learner-001", "customer")
        assert (
            await client.post("/mcp", headers={"Authorization": f"Bearer {token}"})
        ).status_code == 401
