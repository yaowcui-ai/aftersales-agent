"""Delivery HTTP contract and UI safety tests; never contact a running service."""

import json
from pathlib import Path

import httpx
import pytest


def test_login_and_bearer_identity():
    from aftersales_delivery.client import DeliveryClient

    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/auth/login":
            return httpx.Response(
                200, json={"token": "session-token", "user": {"role": "customer"}}
            )
        return httpx.Response(200, json={"username": "alice", "customer_id": "C1"})

    with DeliveryClient("http://test", transport=httpx.MockTransport(respond)) as client:
        login = client.login("alice", "private-password")
        client.token = login["token"]
        assert client.me()["customer_id"] == "C1"
    assert json.loads(requests[0].content) == {"username": "alice", "password": "private-password"}
    assert "authorization" not in requests[0].headers
    assert requests[1].headers["authorization"] == "Bearer session-token"
    assert requests[1].extensions["timeout"]["read"] == 120.0


def test_client_uses_contract_and_never_sends_identity_in_body():
    from aftersales_delivery.client import DeliveryClient

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    with DeliveryClient("http://test/", "t", transport=httpx.MockTransport(respond)) as client:
        client.send_message("delivery-task", "物流到哪里了")
        client.get_task("delivery-task")
        client.get_task("delivery-task", customer_id="C1")
        client.list_cases()
        client.get_case("CASE-1")
        client.list_approvals()
        client.decide("ACT-1", approved=False, version=3)
        client.retry("ACT-1")
        client.set_case_status("CASE-1", "resolved", "已处理")
    assert [(r.method, r.url.path) for r in requests] == [
        ("POST", "/tasks/delivery-task/messages"),
        ("GET", "/tasks/delivery-task"),
        ("GET", "/tasks/delivery-task"),
        ("GET", "/cases"),
        ("GET", "/cases/CASE-1"),
        ("GET", "/approvals"),
        ("POST", "/approvals/ACT-1/decision"),
        ("POST", "/approvals/ACT-1/retry"),
        ("POST", "/cases/CASE-1/status"),
    ]
    assert json.loads(requests[0].content) == {"message": "物流到哪里了"}
    assert requests[2].url.params["customer_id"] == "C1"
    assert json.loads(requests[6].content) == {"approved": False, "version": 3}
    assert requests[7].content == b""
    assert json.loads(requests[8].content) == {"status": "resolved", "resolution": "已处理"}


@pytest.mark.parametrize("failure", ["timeout", "connection", "server"])
def test_write_failure_is_not_automatically_retried(failure):
    from aftersales_delivery.client import DeliveryClient, DeliveryClientError

    calls = []

    def respond(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Bearer secret-password", request=request)
        if failure == "connection":
            raise httpx.ConnectError("mysql://password:secret@host", request=request)
        return httpx.Response(500, text="Traceback password=secret")

    with DeliveryClient("http://test", "t", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DeliveryClientError) as caught:
            client.send_message("task", "退款")
    assert len(calls) == 1
    assert "secret" not in str(caught.value)
    assert "Traceback" not in str(caught.value)
    assert "读取" in str(caught.value)


def test_structured_error_and_invalid_response_are_safe():
    from aftersales_delivery.client import DeliveryClient, DeliveryClientError

    def respond(request):
        if request.url.path == "/approvals/a/decision":
            return httpx.Response(
                409,
                json={
                    "detail": {"code": "version_conflict", "message": "审批版本已变化，请刷新。"}
                },
            )
        return httpx.Response(200, text="password=secret")

    with DeliveryClient("http://test", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(DeliveryClientError) as caught:
            client.decide("a", approved=True, version=1)
        assert caught.value.code == "version_conflict"
        assert caught.value.status_code == 409
        assert "审批版本已变化" in str(caught.value)
        with pytest.raises(DeliveryClientError) as malformed:
            client.me()
        assert "secret" not in str(malformed.value)


def test_client_ignores_proxy_environment(monkeypatch):
    from aftersales_delivery.client import DeliveryClient

    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.invalid:9999")
    with DeliveryClient("http://test") as client:
        assert client._http._trust_env is False


@pytest.mark.parametrize(
    "address", ["ftp://test", "http://user:password@test", "http://[", "http://test:abc"]
)
def test_invalid_api_address_has_safe_user_error(address):
    from aftersales_delivery.client import DeliveryClient, DeliveryClientError

    with pytest.raises(DeliveryClientError) as caught:
        DeliveryClient(address)
    assert caught.value.code == "invalid_url"
    assert "password" not in str(caught.value)


def test_ui_login_has_empty_password_and_no_network_request(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from aftersales_delivery.client import DeliveryClient

    def unexpected_request(*args, **kwargs):
        pytest.fail("Login screen must not contact the service before submission")

    monkeypatch.setattr(DeliveryClient, "_request", unexpected_request)
    ui = Path(__file__).parents[2] / "src" / "aftersales_delivery" / "ui.py"
    app = AppTest.from_file(str(ui)).run()
    assert not app.exception
    assert app.title[0].value == "售后服务工作台"
    inputs = {item.label: item for item in app.text_input}
    assert set(inputs) == {"API 地址", "用户名", "密码"}
    assert inputs["密码"].value == ""
    assert "登录" in [button.label for button in app.button]
    app.button[0].click().run()
    assert not app.exception
    assert any("用户名和密码" in item.value for item in app.warning)


def test_ui_customer_pending_approval_cannot_post_decision(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from aftersales_delivery.client import DeliveryClient

    writes = []
    task = {
        "thread_id": "delivery-test",
        "customer_id": "C1",
        "status": "pending_approval",
        "messages": [{"type": "human", "content": "申请退款"}],
        "next_nodes": ["approval"],
        "proposal": {
            "action_id": "a",
            "kind": "refund",
            "status": "waiting_approval",
            "order_id": "O1",
            "amount": "19.90",
            "quantity": 1,
            "version": 1,
        },
    }
    monkeypatch.setattr(DeliveryClient, "get_task", lambda *args: task)
    monkeypatch.setattr(DeliveryClient, "list_cases", lambda *args: [])
    monkeypatch.setattr(DeliveryClient, "decide", lambda *args, **kwargs: writes.append(kwargs))
    ui = Path(__file__).parents[2] / "src" / "aftersales_delivery" / "ui.py"
    # Allow local UI startup while Docker/Edge are running; assertions stay unchanged.
    app = AppTest.from_file(str(ui), default_timeout=10)
    app.session_state["delivery_token"] = "customer-session"
    app.session_state["delivery_user"] = {
        "username": "alice",
        "customer_id": "C1",
        "role": "customer",
    }
    app.session_state["delivery_thread_id"] = "delivery-test"
    app.run()
    assert not app.exception
    assert not writes
    assert not any(button.label in {"批准", "拒绝", "重试原动作"} for button in app.button)
    assert any("等待审批" in item.value for item in app.warning)
    assert len(app.chat_input) == 1
    assert app.chat_input[0].disabled
    app.button(key="refresh_task").click().run()
    assert not app.exception
    assert not writes


def test_ui_approver_decides_snapshot_version_once(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from aftersales_delivery.client import DeliveryClient

    decisions = []
    action = {
        "action_id": "ACT-1",
        "thread_id": "delivery-t",
        "customer_id": "C1",
        "kind": "refund",
        "status": "pending",
        "order_id": "O1",
        "amount": "28.50",
        "quantity": 1,
        "version": 4,
    }
    monkeypatch.setattr(
        DeliveryClient, "list_approvals", lambda *args: [] if decisions else [action]
    )
    monkeypatch.setattr(DeliveryClient, "list_cases", lambda *args: [])

    def decide(self, action_id, *, approved, version):
        decisions.append((action_id, approved, version))
        return {"status": "completed", "action_id": action_id}

    monkeypatch.setattr(DeliveryClient, "decide", decide)
    ui = Path(__file__).parents[2] / "src" / "aftersales_delivery" / "ui.py"
    app = AppTest.from_file(str(ui))
    app.session_state["delivery_token"] = "approver-session"
    app.session_state["delivery_user"] = {"username": "staff", "role": "approver"}
    app.run()
    assert not app.exception
    assert any("28.50" in item.value for item in app.markdown)
    app.button(key="approve_ACT-1").click().run()
    assert not app.exception
    assert decisions == [("ACT-1", True, 4)]
    app.button(key="refresh_approvals").click().run()
    assert not app.exception
    assert decisions == [("ACT-1", True, 4)]


def test_ui_customer_submission_timeout_does_not_repost(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from aftersales_delivery.client import DeliveryClient, DeliveryClientError

    writes = []

    def send_message(self, thread_id, message):
        writes.append((thread_id, message))
        raise DeliveryClientError("timeout", "请求超时，请先读取已保存状态。")

    monkeypatch.setattr(DeliveryClient, "send_message", send_message)
    monkeypatch.setattr(
        DeliveryClient,
        "get_task",
        lambda self, thread_id: {
            "thread_id": thread_id,
            "customer_id": "C1",
            "status": "pending_approval",
            "messages": [],
            "proposal": {"action_id": "a", "status": "pending", "kind": "refund"},
        },
    )
    monkeypatch.setattr(DeliveryClient, "list_cases", lambda *args: [])
    ui = Path(__file__).parents[2] / "src" / "aftersales_delivery" / "ui.py"
    app = AppTest.from_file(str(ui))
    app.session_state["delivery_token"] = "customer-session"
    app.session_state["delivery_user"] = {
        "username": "alice",
        "customer_id": "C1",
        "role": "customer",
    }
    app.run()
    app.chat_input[0].set_value("订单 O1 申请退款").run()
    assert not app.exception
    assert len(writes) == 1
    assert writes[0][0].startswith("delivery-")
    app.run()
    assert not app.exception
    assert len(writes) == 1
    assert any("等待审批" in item.value for item in app.warning)
    app.button(key="refresh_task").click().run()
    assert not app.exception
    assert len(writes) == 1


def test_ui_displays_saved_final_answer_when_message_history_has_no_answer(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from aftersales_delivery.client import DeliveryClient

    monkeypatch.setattr(
        DeliveryClient,
        "get_task",
        lambda *args: {
            "thread_id": "delivery-t",
            "status": "completed",
            "messages": [],
            "final_answer": "已查询：包裹今日送达。",
            "proposal": None,
        },
    )
    monkeypatch.setattr(DeliveryClient, "list_cases", lambda *args: [])
    ui = Path(__file__).parents[2] / "src" / "aftersales_delivery" / "ui.py"
    app = AppTest.from_file(str(ui))
    app.session_state["delivery_token"] = "customer-session"
    app.session_state["delivery_user"] = {"username": "alice", "role": "customer"}
    app.session_state["delivery_thread_id"] = "delivery-t"
    app.run()
    assert not app.exception
    assert any("包裹今日送达" in item.value for item in app.markdown)


def test_ui_completed_action_is_history_and_task_read_binds_owner(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from aftersales_delivery.client import DeliveryClient

    reads = []
    monkeypatch.setattr(
        DeliveryClient,
        "list_approvals",
        lambda *args: [
            {
                "action_id": "completed-action",
                "customer_id": "C2",
                "thread_id": "delivery-old",
                "kind": "refund",
                "status": "completed",
                "version": 1,
                "order_id": "O2",
            }
        ],
    )
    monkeypatch.setattr(DeliveryClient, "list_cases", lambda *args: [])

    def get_task(self, thread_id, *, customer_id=None):
        reads.append((thread_id, customer_id))
        return {"thread_id": thread_id, "status": "completed", "messages": []}

    monkeypatch.setattr(DeliveryClient, "get_task", get_task)
    ui = Path(__file__).parents[2] / "src" / "aftersales_delivery" / "ui.py"
    app = AppTest.from_file(str(ui))
    app.session_state["delivery_token"] = "approver-session"
    app.session_state["delivery_user"] = {"username": "staff", "role": "approver"}
    app.run()
    assert not app.exception
    assert not any(button.label in {"批准", "拒绝"} for button in app.button)
    assert any(button.key == "retry_saved_completed-action" for button in app.button)
    app.button(key="read_result_completed-action").click().run()
    assert not app.exception
    assert reads == [("delivery-old", "C2")]
