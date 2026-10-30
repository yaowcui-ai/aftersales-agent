"""Customer chat history stays read-only until the user sends a new message."""

from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from aftersales_delivery.client import DeliveryClient, DeliveryClientError

UI = Path(__file__).parents[2] / "src/aftersales_delivery/ui.py"


@pytest.fixture
def chat(monkeypatch):
    tasks = {
        "delivery-old": {
            "thread_id": "delivery-old",
            "customer_id": "C1",
            "status": "completed",
            "proposal": None,
            "messages": [
                {"type": "human", "content": "查询 O1 的物流"},
                {"type": "ai", "content": "", "tool_calls": [{"name": "get_shipments"}]},
                {"type": "tool", "content": '{"private_tool_payload": true}'},
                {"type": "ai", "content": "O1 的包裹正在运输。"},
            ],
            "final_answer": "O1 的包裹正在运输。",
        }
    }
    reads, writes = [], []

    def get_task(self, thread_id):
        reads.append(thread_id)
        if thread_id not in tasks:
            raise DeliveryClientError("TASK_NOT_FOUND", "当前账号未找到此对话。", 404)
        return deepcopy(tasks[thread_id])

    def send_message(self, thread_id, message):
        writes.append((thread_id, message))
        task = tasks.setdefault(
            thread_id,
            {"thread_id": thread_id, "customer_id": "C1", "proposal": None, "messages": []},
        )
        task["messages"].extend(
            [{"type": "human", "content": message}, {"type": "ai", "content": "已收到追问。"}]
        )
        task.update(status="completed", final_answer="已收到追问。")
        return deepcopy(task)

    monkeypatch.setattr(DeliveryClient, "get_task", get_task)
    monkeypatch.setattr(DeliveryClient, "send_message", send_message)
    monkeypatch.setattr(DeliveryClient, "list_cases", lambda *args: [])
    monkeypatch.setattr(DeliveryClient, "list_conversations", lambda *args: [])
    app = AppTest.from_file(str(UI), default_timeout=10)
    app.session_state["delivery_token"] = "customer-session"
    app.session_state["delivery_user"] = {
        "username": "alice",
        "customer_id": "C1",
        "role": "customer",
    }
    return app, tasks, reads, writes


def load(app, thread_id):
    next(item for item in app.text_input if item.label == "已保存的任务编号").set_value(thread_id)
    app.button(key="load_history").click().run()
    assert not app.exception


def test_history_load_renders_both_roles_without_reposting_or_duplicate_reply(chat):
    app, _, reads, writes = chat
    app.run()
    assert any(button.key == "load_history" for button in app.button)
    load(app, "delivery-old")
    assert app.session_state["delivery_thread_id"] == "delivery-old"
    assert [item.name for item in app.chat_message] == ["user", "assistant"]
    assert app.chat_message[0].markdown[0].value == "查询 O1 的物流"
    assert app.chat_message[1].markdown[0].value == "O1 的包裹正在运输。"
    assert reads and set(reads) == {"delivery-old"}
    assert writes == []


def test_unavailable_history_does_not_replace_the_active_conversation(chat):
    app, _, _, writes = chat
    app.session_state["delivery_thread_id"] = "delivery-old"
    app.run()
    assert any(button.key == "load_history" for button in app.button)
    load(app, "missing-or-other-customer")
    assert app.session_state["delivery_thread_id"] == "delivery-old"
    assert app.chat_message[0].markdown[0].value == "查询 O1 的物流"
    assert app.error
    assert writes == []


def test_new_dialog_keeps_recent_history_available_for_switching(chat):
    app, _, _, writes = chat
    app.session_state["delivery_thread_id"] = "delivery-old"
    app.run()
    app.button(key="new_task").click().run()
    assert not app.exception
    assert not app.session_state.filtered_state.get("delivery_thread_id")
    assert any(button.key == "history_delivery-old" for button in app.button)
    app.button(key="history_delivery-old").click().run()
    assert not app.exception
    assert app.session_state["delivery_thread_id"] == "delivery-old"
    assert len(app.chat_message) == 2
    assert writes == []


def test_followup_in_loaded_history_preserves_thread_and_sends_only_once(chat):
    app, _, _, writes = chat
    app.run()
    assert any(button.key == "load_history" for button in app.button)
    load(app, "delivery-old")
    app.chat_input[0].set_value("再解释一下包裹状态").run()
    assert not app.exception
    assert writes == [("delivery-old", "再解释一下包裹状态")]
    assert [item.name for item in app.chat_message] == ["user", "assistant", "user", "assistant"]
    app.button(key="refresh_task").click().run()
    assert not app.exception
    assert len(writes) == 1
    assert len(app.chat_message) == 4


@pytest.mark.parametrize("status", ["waiting_approval", "completed"])
def test_existing_business_application_stays_visible_with_disabled_composer(chat, status):
    app, tasks, _, writes = chat
    tasks["delivery-old"]["proposal"] = {
        "action_id": "ACT-1",
        "status": status,
        "kind": "logistics",
        "order_id": "O1",
    }
    app.session_state["delivery_thread_id"] = "delivery-old"
    app.run()
    assert not app.exception
    assert len(app.chat_message) == 2
    assert len(app.chat_input) == 1
    assert app.chat_input[0].disabled
    assert writes == []
