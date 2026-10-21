import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import create_async_engine


@pytest_asyncio.fixture
async def client(tmp_path):
    from aftersales_delivery.api import create_app
    from aftersales_delivery.config import Config
    from aftersales_delivery.db import initialize
    from aftersales_delivery.security import hash_password

    config = Config(
        f"sqlite+aiosqlite:///{tmp_path / 'business.sqlite'}",
        "key" * 16,
        {
            name: {
                "password_hash": hash_password("test-password"),
                "role": role,
                "customer_id": owner,
            }
            for name, role, owner in [
                ("customer1", "customer", "learner-001"),
                ("customer2", "customer", "learner-002"),
                ("reviewer", "approver", ""),
            ]
        },
        checkpoint_backend="sqlite",
        checkpoint_path=str(tmp_path / "checkpoints.sqlite"),
    )
    engine = create_async_engine(config.db_url)
    await initialize(engine)
    await engine.dispose()

    async def model(messages, tools):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "prepare_action",
                    "id": "proposal",
                    "args": {
                        "kind": "refund",
                        "order_id": "DEMO-004",
                        "description": "模拟退款申请",
                        "amount": "10.00",
                    },
                }
            ],
        )

    async def reply(action):
        return f"模拟退款已完成，工单 {action['result']['case_id']}。"

    app = create_app(config, model=model, reply=reply, local_tools=True)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def login(client, name):
    response = await client.post(
        "/auth/login", json={"username": name, "password": "test-password"}
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


@pytest.mark.asyncio
async def test_authorization_approval_and_cached_replay(client):
    assert (await client.get("/cases")).status_code == 401
    owner = await login(client, "customer1")
    other = await login(client, "customer2")
    reviewer = await login(client, "reviewer")
    response = await client.post(
        "/tasks/test-001/messages", json={"message": "退款10元"}, headers=owner
    )
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["status"] == "waiting_approval"
    action = task["proposal"]
    assert not action["result"]
    assert (await client.get("/tasks/test-001", headers=other)).status_code == 404
    route = f"/approvals/{action['action_id']}/decision"
    body = {"approved": True, "version": 1}
    assert (await client.post(route, json=body, headers=owner)).status_code == 403
    assert (
        await client.post(route, json={**body, "version": 2}, headers=reviewer)
    ).status_code == 409
    approved = await client.post(route, json=body, headers=reviewer)
    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert result["status"] == "completed"
    case_id = result["proposal"]["result"]["case_id"]
    assert case_id in result["final_answer"]
    assert (await client.post(route, json=body, headers=reviewer)).json() == result
    assert len((await client.get("/cases", headers=owner)).json()) == 1
    assert (await client.get(f"/cases/{case_id}", headers=other)).status_code == 404
    assert (
        await client.post(route, json={"approved": False, "version": 1}, headers=reviewer)
    ).status_code == 409


@pytest.mark.asyncio
async def test_rejection_and_untrusted_payload_cannot_create_case(client):
    owner = await login(client, "customer1")
    reviewer = await login(client, "reviewer")
    assert (
        await client.post(
            "/tasks/test/messages",
            json={"message": "退款", "customer_id": "learner-002"},
            headers=owner,
        )
    ).status_code == 422
    task = (
        await client.post("/tasks/reject/messages", json={"message": "退款"}, headers=owner)
    ).json()
    action = task["proposal"]
    rejected = await client.post(
        f"/approvals/{action['action_id']}/decision",
        json={"approved": False, "version": 1},
        headers=reviewer,
    )
    assert rejected.json()["status"] == "rejected"
    assert (await client.get("/cases", headers=owner)).json() == []


@pytest.mark.asyncio
async def test_reply_model_sees_actual_business_result_not_competing_application_id():
    import json

    from aftersales_delivery.api import Runtime
    from aftersales_delivery.config import Config

    captured = []

    class ReplyModel:
        async def ainvoke(self, messages):
            captured.extend(messages)
            return AIMessage(content="真实工单 actual-case-id")

    rt = Runtime(Config("sqlite+aiosqlite:///:memory:", "key" * 16, {}), None, None)
    rt.reply_model = ReplyModel()
    await rt.reply(
        {
            "action_id": "competing-application-id",
            "result": {
                "case_id": "actual-case-id",
                "order_id": "DEMO-001",
                "kind": "logistics",
                "case_status": "open",
                "simulated": True,
            },
        }
    )
    payload = json.loads(captured[-1].content)
    assert "action_id" not in payload
    assert "competing-application-id" not in captured[-1].content
    assert payload["case_id"] == "actual-case-id"
