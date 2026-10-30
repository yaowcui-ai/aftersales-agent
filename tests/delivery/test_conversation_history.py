"""History is recovered from persisted checkpoints, never from browser identity."""
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import create_async_engine
from streamlit.testing.v1 import AppTest

from aftersales_delivery.api import create_app
from aftersales_delivery.client import DeliveryClient
from aftersales_delivery.config import Config
from aftersales_delivery.db import initialize
from aftersales_delivery.security import hash_password


@pytest.mark.asyncio
async def test_history_survives_api_restart_and_is_scoped_to_exact_customer(tmp_path):
    config = Config(
        f"sqlite+aiosqlite:///{tmp_path / 'business.sqlite'}", "key" * 16,
        {name: {"password_hash": hash_password("test-password"), "role": role, "customer_id": owner}
         for name, role, owner in [("alice", "customer", "C1"), ("bob", "customer", "C1x"),
                                    ("reviewer", "approver", "")]},
        checkpoint_backend="sqlite", checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
    )
    engine = create_async_engine(config.db_url)
    await initialize(engine)
    await engine.dispose()
    calls = []

    async def model(messages, tools):
        calls.append(1)
        return AIMessage(content="已保存的只读回复")

    async def login(client, name):
        response = await client.post("/auth/login", json={"username": name, "password": "test-password"})
        return {"Authorization": "Bearer " + response.json()["token"]}

    for first in (True, False):
        app = create_app(config, model=model, local_tools=True)
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/conversations")).status_code == 401
                alice, bob = await login(client, "alice"), await login(client, "bob")
                if first:
                    assert (await client.get("/conversations", headers=alice)).json() == []
                    for headers, thread, message in [(alice, "old", "查询订单"),
                                                      (alice, "new", "查询物流"),
                                                      (bob, "foreign", "其他客户的秘密")]:
                        result = await client.post(f"/tasks/{thread}/messages", headers=headers,
                                                   json={"message": message})
                        assert result.status_code == 200
                    # Multiple checkpoints/turns must produce only one history entry.
                    await client.post("/tasks/new/messages", headers=alice, json={"message": "继续查询"})
                history = await client.get("/conversations?customer_id=C1x", headers=alice)
                assert history.status_code == 200
                rows = history.json()
                assert [row["thread_id"] for row in rows] == ["new", "old"]
                assert rows[0]["title"] == "查询物流"
                assert all(row["updated_at"] for row in rows)
                assert "其他客户的秘密" not in history.text
                assert len((await client.get("/conversations", headers=bob)).json()) == 1
                assert (await client.get("/conversations", headers=await login(client, "reviewer"))).status_code == 403
                assert (await client.get("/conversations?limit=101", headers=alice)).status_code == 422
                assert len(calls) == 4  # Listing or restoring never invokes the model.


def test_clean_streamlit_session_recovers_saved_history_without_reposting(monkeypatch):
    task = {"thread_id": "saved-chat", "status": "completed", "proposal": None,
            "messages": [{"type": "human", "content": "刷新之前的问题"},
                         {"type": "ai", "content": "刷新之前的回复"}],
            "final_answer": "刷新之前的回复"}
    monkeypatch.setattr(DeliveryClient, "list_conversations", lambda self: [
        {"thread_id": "saved-chat", "title": "刷新之前的问题"}], raising=False)
    monkeypatch.setattr(DeliveryClient, "get_task", lambda self, thread: task)
    monkeypatch.setattr(DeliveryClient, "list_cases", lambda self: [])
    monkeypatch.setattr(DeliveryClient, "send_message", lambda *args: pytest.fail("History read reposted a message"))
    ui = Path(__file__).parents[2] / "src/aftersales_delivery/ui.py"
    for _ in range(2):  # A browser reload creates a new Streamlit session.
        app = AppTest.from_file(str(ui), default_timeout=10)
        app.session_state["delivery_token"] = "customer-session"
        app.session_state["delivery_user"] = {"username": "alice", "role": "customer", "customer_id": "C1"}
        app.run()
        assert not app.exception
        assert app.session_state.filtered_state.get("delivery_thread_id") == "saved-chat"
        assert [m.markdown[0].value for m in app.chat_message] == ["刷新之前的问题", "刷新之前的回复"]
        assert app.query_params["conversation"] == ["saved-chat"]
        app.button(key="new_task").click().run()
        assert not app.session_state.filtered_state.get("delivery_thread_id")
        assert "conversation" not in app.query_params


def test_history_http_client_is_read_only_and_uses_bearer_identity():
    import httpx
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[])
    with DeliveryClient("http://test", "t", transport=httpx.MockTransport(respond)) as client:
        assert client.list_conversations() == []
    assert [(r.method, r.url.path) for r in requests] == [("GET", "/conversations")]
    assert requests[0].headers["Authorization"] == "Bearer t"
    assert not requests[0].url.query
