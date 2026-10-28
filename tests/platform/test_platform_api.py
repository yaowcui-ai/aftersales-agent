import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage


@pytest_asyncio.fixture
async def platform(tmp_path):
    from aftersales_delivery.api import create_app
    from aftersales_delivery.config import Config
    from aftersales_delivery.security import hash_password

    accounts = {name: {"password_hash": hash_password("sandbox-test"), "role": role,
                       "customer_id": customer}
                for name, role, customer in [("customer1", "customer", "learner-001"),
                    ("customer2", "customer", "learner-002"), ("reviewer", "approver", "")]}
    config = Config(f"sqlite+aiosqlite:///{tmp_path / 'business.sqlite'}", "key" * 16,
                    accounts, checkpoint_backend="sqlite",
                    checkpoint_path=str(tmp_path / "checkpoints.sqlite"), platform_enabled=True)

    async def model(messages, tools):
        if messages[-1].type == "tool":
            return AIMessage(content="已根据工具查询订单。")
        return AIMessage(content="", tool_calls=[{"name": "list_orders", "args": {}, "id": "list"}])

    async def reply(action):
        return f"沙箱模拟结果：工单 {action['result']['case_id']}"

    app = create_app(config, model=model, reply=reply, local_tools=True)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {}
            for name in accounts:
                response = await client.post("/auth/login", json={"username": name, "password": "sandbox-test"})
                assert response.status_code == 200
                headers[name] = {"Authorization": "Bearer " + response.json()["token"]}
            yield client, headers, app


def request(request_id="platform-refund", order="UP-PAID", kind="refund"):
    return {"request_id": request_id, "order_id": order, "kind": kind,
            "description": "沙箱售后申请", "amount": "10.00"}


@pytest.mark.asyncio
async def test_react_rest_contract_and_customer_isolation(platform):
    client, headers, _ = platform
    assert (await client.get("/orders")).status_code == 401
    result = await client.get("/orders", headers=headers["customer1"])
    assert result.status_code == 200
    assert any(o["order_id"] == "UP-PAID" for o in result.json()["orders"])
    assert (await client.get("/orders/UP-OTHER", headers=headers["customer1"])).status_code == 404
    assert (await client.get("/orders/UP-SHIPPED/shipments", headers=headers["customer1"])).status_code == 200
    assert (await client.get("/admin/overview", headers=headers["customer1"])).status_code == 403
    response = await client.post("/tasks/my-count/messages", headers=headers["customer1"], json={"message": "有几个订单"})
    assert response.json()["status"] == "answered"
    own = (await client.get("/tasks", headers=headers["customer1"])).json()
    assert any(t["thread_id"] == "my-count" for t in own)
    assert (await client.get("/tasks", headers=headers["customer2"])).json() == []


@pytest.mark.asyncio
async def test_form_approval_reason_frozen_payload_and_replay(platform):
    client, headers, _ = platform
    response = await client.post("/requests", headers=headers["customer1"], json=request())
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["status"] == "waiting_approval"
    action = task["proposal"]
    assert (await client.get("/tickets", headers=headers["customer1"])).json()
    assert (await client.get("/tickets", headers=headers["customer2"])).json() == []
    route = f"/approvals/{action['action_id']}/decision"
    body = {"approved": True, "version": action["version"], "reason": "核对未发货订单与金额"}
    assert (await client.post(route, headers=headers["customer1"], json=body)).status_code == 403
    assert (await client.post(route, headers=headers["reviewer"], json={**body, "amount": "999.00"})).status_code == 422
    approved = await client.post(route, headers=headers["reviewer"], json=body)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "completed"
    assert (await client.post(route, headers=headers["reviewer"], json=body)).json() == approved.json()
    events = (await client.get(f"/actions/{action['action_id']}/events", headers=headers["reviewer"])).json()
    assert any(e["event_type"] == "decision" for e in events)
    assert (await client.get("/orders/UP-PAID", headers=headers["customer1"])).json()["refunded_amount"] == "10.00"
    duplicate = await client.post("/requests", headers=headers["customer1"], json=request())
    assert duplicate.status_code == 200
    assert duplicate.json()["proposal"]["action_id"] == action["action_id"]
    conflict = await client.post("/requests", headers=headers["customer1"], json={**request(), "amount": "11.00"})
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_cancel_and_return_receipt_via_admin(platform):
    client, headers, _ = platform
    initial = (await client.post("/requests", headers=headers["customer1"], json=request("cancel-me"))).json()
    aid = initial["proposal"]["action_id"]
    assert (await client.post(f"/actions/{aid}/cancel", headers=headers["customer2"])).status_code == 404
    assert (await client.post(f"/actions/{aid}/cancel", headers=headers["customer1"])).status_code == 200
    rejected = await client.post(f"/approvals/{aid}/decision", headers=headers["reviewer"], json={"approved": True, "version": 1})
    assert rejected.status_code == 409
    task = (await client.post("/requests", headers=headers["customer1"], json=request("return-me", "UP-SHIPPED", "return_refund"))).json()
    action = task["proposal"]
    approved = await client.post(f"/approvals/{action['action_id']}/decision", headers=headers["reviewer"], json={"approved": True, "version": 1, "reason": "同意先退货"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "awaiting_return"
    assert (await client.get("/orders/UP-SHIPPED", headers=headers["customer1"])).json()["refunded_amount"] == "0.00"
    route = f"/returns/{action['action_id']}/receive"
    assert (await client.post(route, headers=headers["customer1"])).status_code == 403
    received = await client.post(route, headers=headers["reviewer"])
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "completed"
    assert (await client.post(route, headers=headers["reviewer"])).status_code == 200
    assert (await client.get("/orders/UP-SHIPPED", headers=headers["customer1"])).json()["refunded_amount"] == "10.00"
