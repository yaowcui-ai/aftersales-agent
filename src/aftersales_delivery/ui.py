"""Chinese Streamlit workspace for customers and aftersales approvers.

Run with: streamlit run src/aftersales_delivery/ui.py --server.port 8510
"""

import os
import uuid

import streamlit as st

from aftersales_delivery.client import DeliveryClient, DeliveryClientError

KINDS = {"logistics": "物流工单", "refund": "模拟退款", "reship": "模拟补发"}
STATUSES = {
    "pending": "等待审批",
    "waiting_approval": "等待审批",
    "pending_approval": "等待审批",
    "awaiting_approval": "等待审批",
    "approved": "已批准，等待执行或恢复",
    "rejected": "已拒绝",
    "executed": "业务已执行",
    "completed": "已完成",
    "succeeded": "业务已执行",
    "failed": "处理失败，需核对保存状态",
    "reply_failed": "业务结果已保存，最终回复失败",
    "human_required": "需要人工处理",
    "open": "待处理",
    "processing": "处理中",
    "resolved": "已解决",
    "closed": "已关闭",
}


def show_error(error: DeliveryClientError):
    st.error(error.message)
    st.caption("如果提交后发生超时或断连，请先点击“读取已保存状态”，核对任务和工单，避免重复申请。")


def rows(payload, key):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get(key, payload.get("items", []))
    return []


def show_action(action):
    st.write(f"操作：{KINDS.get(action.get('kind'), action.get('kind', '—'))}")
    st.write(f"订单：{action.get('order_id', '—')} · 动作编号：{action.get('action_id', '—')}")
    st.write(f"客户：{action.get('customer_id', '—')} · 审批版本：{action.get('version', '—')}")
    st.write(f"状态：{STATUSES.get(action.get('status'), action.get('status', '—'))}")
    if action.get("amount") is not None:
        st.write(f"退款金额：¥{action['amount']}")
    if action.get("quantity") is not None:
        st.write(f"数量：{action['quantity']}")
    if action.get("description"):
        st.write(action["description"])
    if action.get("result"):
        st.success("业务执行结果已保存")
        st.json(action["result"])


def show_messages(task):
    """Render saved human/assistant turns; keep tool payloads in the trace panel."""
    for message in task.get("messages", []):
        kind = message.get("type")
        content = message.get("content")
        if kind in {"human", "user", "ai", "assistant"} and content:
            with st.chat_message("user" if kind in {"human", "user"} else "assistant"):
                if isinstance(content, str):
                    st.markdown(content)
                else:
                    st.json(content)
    final_answer = task.get("final_answer")
    if final_answer and not any(
        message.get("type") in {"ai", "assistant"} and message.get("content") == final_answer
        for message in task.get("messages", [])
    ):
        with st.chat_message("assistant"):
            st.markdown(final_answer)


def show_task(task, *, include_messages=True):
    st.caption(f"任务编号：{task.get('thread_id', '—')} · 客户：{task.get('customer_id', '—')}")
    status = task.get("status", "")
    st.write(f"任务状态：{STATUSES.get(status, status or '处理中')}")
    action = task.get("proposal")
    if action:
        with st.expander("申请与执行结果", expanded=True):
            show_action(action)
        if action.get("status") in {
            "pending",
            "pending_approval",
            "awaiting_approval",
            "waiting_approval",
        }:
            st.warning("等待审批：当前仅保存了申请，尚未执行退款、补发或创建实际工单。")
        if action.get("result") and status in {"reply_failed", "failed"}:
            st.warning(
                "业务结果已经保存。最终回复失败时，由审批员恢复原动作即可；请核对结果，不要新建重复申请。"
            )
    if include_messages:
        show_messages(task)
    with st.expander("工具轨迹与运行指标"):
        for message in task.get("messages", []):
            if message.get("type") == "tool" or message.get("tool_calls"):
                st.json(message)
        if task.get("next_nodes"):
            st.write("后续节点", task["next_nodes"])
        st.json(task.get("metrics", {}))


def remember_conversation(task):
    """Cache labels only; checkpoint history remains the durable message store."""
    title = next(
        (
            message["content"]
            for message in task.get("messages", [])
            if message.get("type") in {"human", "user"}
            and isinstance(message.get("content"), str)
            and message["content"].strip()
        ),
        task["thread_id"],
    )
    st.session_state.setdefault("delivery_conversations", {})[task["thread_id"]] = title.replace(
        "\n", " "
    )[:32]
    st.query_params["conversation"] = task["thread_id"]


def recover_conversations(client):
    """Rebuild navigation after a fresh login/refresh; never repost a message."""
    if st.session_state.get("delivery_history_loaded"):
        return
    try:
        records = client.list_conversations()
    except DeliveryClientError as error:
        show_error(error)
        return
    st.session_state["delivery_conversations"] = {
        item["thread_id"]: item.get("title", item["thread_id"])[:32]
        for item in reversed(records)
    }
    st.session_state["delivery_history_loaded"] = True
    target = (st.session_state.get("delivery_thread_id") or st.query_params.get("conversation")
              or (records[0]["thread_id"] if records else None))
    if not target:
        return
    try:
        saved = client.get_task(target)
    except DeliveryClientError as error:
        show_error(error)
    else:
        st.session_state["delivery_thread_id"] = target
        st.session_state["delivery_task"] = saved
        remember_conversation(saved)


def conversation_history(client):
    """Switch only after GET succeeds, so a bad/unauthorized ID cannot replace the chat."""
    recover_conversations(client)
    with st.sidebar:
        st.divider()
        st.subheader("历史对话")
        if st.button("新建对话", key="new_task", width="stretch"):
            st.session_state.pop("delivery_thread_id", None)
            st.session_state.pop("delivery_task", None)
            st.query_params.pop("conversation", None)
            st.rerun()
        active = st.session_state.get("delivery_thread_id")
        target = None
        for thread_id, title in reversed(
            list(st.session_state.get("delivery_conversations", {}).items())
        ):
            if st.button(
                title,
                key=f"history_{thread_id}",
                type="primary" if thread_id == active else "secondary",
                width="stretch",
            ):
                target = thread_id
        st.caption("显示当前账号最近100条对话。刷新或重新登录后仍可查看。")
        if st.button("刷新历史列表", key="refresh_history", width="stretch"):
            st.session_state.pop("delivery_history_loaded", None)
            st.rerun()
        with st.expander("恢复已有任务"):
            with st.form("restore_task"):
                saved_id = st.text_input("已保存的任务编号")
                restore = st.form_submit_button("打开任务", key="load_history")
            if restore:
                if saved_id.strip():
                    target = saved_id.strip()
                else:
                    st.warning("请输入要加载的任务编号。")
        if target:
            try:
                saved = client.get_task(target)
            except DeliveryClientError as error:
                show_error(error)
            else:
                st.session_state["delivery_thread_id"] = target
                st.session_state["delivery_task"] = saved
                remember_conversation(saved)
                st.rerun()


def customer_panel(client):
    st.subheader("我的售后对话")
    conversation_history(client)
    thread_id = st.session_state.get("delivery_thread_id")
    task = st.session_state.get("delivery_task")
    if thread_id:
        st.button("读取已保存状态", key="refresh_task")
        try:
            task = client.get_task(thread_id)
            st.session_state["delivery_task"] = task
            remember_conversation(task)
        except DeliveryClientError as error:
            show_error(error)
            task = None
    # The existing API accepts follow-ups only while there is no saved proposal.
    # Keep the composer visible, but do not offer a send that the API would reject.
    blocked = bool(thread_id and task is None) or bool((task or {}).get("proposal"))
    with st.container(border=True, key="delivery_chat"):
        transcript = st.container(height=480, border=False, autoscroll=True)
        with transcript:
            if task:
                show_messages(task)
            else:
                with st.chat_message("assistant"):
                    st.markdown(
                        "您好，请输入订单号和售后问题。您可以查询物流，或申请模拟退款、补发。"
                        "\n\n也可以从左侧加载历史对话，继续查看之前的消息。"
                    )
        if blocked:
            st.caption("此任务已有售后申请或暂时无法读取。可查看保存状态；其他问题请新建对话。")
        message = st.chat_input("描述您的售后问题", disabled=blocked, max_chars=4000)
        if message and message.strip():
            if not thread_id:
                thread_id = f"delivery-{uuid.uuid4()}"
                st.session_state["delivery_thread_id"] = thread_id
                st.query_params["conversation"] = thread_id
            try:
                with transcript:
                    with st.chat_message("user"):
                        st.markdown(message.strip())
                    with st.spinner("正在处理，请稍候…"):
                        saved = client.send_message(thread_id, message.strip())
                st.session_state["delivery_task"] = saved
                remember_conversation(saved)
                st.rerun()
            except DeliveryClientError as error:
                show_error(error)
    if task:
        show_task(task, include_messages=False)


def approval_panel(client):
    st.subheader("待审批申请")
    st.caption("请核对客户、订单、金额、数量和审批版本。批准后只会按已保存的申请执行。")
    st.button("刷新审批列表", key="refresh_approvals")
    try:
        all_actions = rows(client.list_approvals(), "approvals")
        pending = [
            action
            for action in all_actions
            if action.get("status")
            in {"pending", "pending_approval", "awaiting_approval", "waiting_approval"}
        ]
    except DeliveryClientError as error:
        show_error(error)
        pending = []
        all_actions = []
    if not pending:
        st.info("当前没有待审批申请。")
    for action in pending:
        action_id = action["action_id"]
        with st.expander(
            f"{KINDS.get(action.get('kind'), '售后申请')} · {action.get('order_id', '')} · {action_id}",
            expanded=True,
        ):
            show_action(action)
            if st.button("查看任务与工具轨迹", key=f"inspect_{action_id}"):
                try:
                    show_task(
                        client.get_task(action["thread_id"], customer_id=action.get("customer_id"))
                    )
                except DeliveryClientError as error:
                    show_error(error)
            left, right = st.columns(2)
            approved = left.button("批准", key=f"approve_{action_id}")
            rejected = right.button("拒绝", key=f"reject_{action_id}")
            if approved or rejected:
                try:
                    with st.spinner("正在保存审批并继续处理…"):
                        result = client.decide(
                            action_id, approved=approved, version=action["version"]
                        )
                    st.session_state["delivery_approval_result"] = result
                    st.rerun()
                except DeliveryClientError as error:
                    show_error(error)
    history = [action for action in all_actions if action not in pending]
    if history:
        st.subheader("已处理申请与恢复")
    for action in history:
        action_id = action["action_id"]
        with st.expander(
            f"{STATUSES.get(action.get('status'), action.get('status'))} · {action_id}"
        ):
            show_action(action)
            if st.button("读取任务结果", key=f"read_result_{action_id}"):
                try:
                    show_task(
                        client.get_task(action["thread_id"], customer_id=action.get("customer_id"))
                    )
                except DeliveryClientError as error:
                    show_error(error)
            if action.get("status") in {"approved", "completed", "failed"}:
                st.caption("恢复沿用原动作和已保存的业务结果。已完成业务不会再次退款或扣减库存。")
                if st.button("恢复原动作 / 最终回复", key=f"retry_saved_{action_id}"):
                    try:
                        st.session_state["delivery_approval_result"] = client.retry(action_id)
                        st.rerun()
                    except DeliveryClientError as error:
                        show_error(error)
    if "delivery_approval_result" in st.session_state:
        with st.expander("最近审批 / 恢复结果", expanded=True):
            st.json(st.session_state["delivery_approval_result"])
    with st.expander("读取任务或恢复已批准的原动作"):
        with st.form("read_approval_task"):
            thread_id = st.text_input("任务编号", key="approver_thread")
            read = st.form_submit_button("读取已保存状态")
        if read and thread_id.strip():
            try:
                st.session_state["delivery_inspected_task"] = client.get_task(thread_id.strip())
            except DeliveryClientError as error:
                show_error(error)
        inspected = st.session_state.get("delivery_inspected_task")
        if inspected:
            show_task(inspected)
        st.caption(
            "用于已批准但执行中断、或业务已成功但模型最终回复失败的情况。服务端会核对批准记录并复用原结果。"
        )
        with st.form("retry_action"):
            action_id = st.text_input("原动作编号", key="retry_action_id")
            retry = st.form_submit_button("重试原动作")
        if retry and action_id.strip():
            try:
                with st.spinner("正在恢复原动作…"):
                    st.session_state["delivery_approval_result"] = client.retry(action_id.strip())
                st.rerun()
            except DeliveryClientError as error:
                show_error(error)


def cases_panel(client, approver):
    st.subheader("售后工单")
    st.button("刷新工单", key="refresh_cases")
    try:
        cases = rows(client.list_cases(), "cases")
    except DeliveryClientError as error:
        show_error(error)
        return
    if not cases:
        st.info("暂无已创建的售后工单。待审批申请尚未形成实际工单。")
        return
    for case in cases:
        case_id = case.get("case_id", case.get("id", ""))
        with st.expander(
            f"{case_id} · {case.get('order_id', '')} · {STATUSES.get(case.get('status'), case.get('status', ''))}"
        ):
            try:
                details = client.get_case(case_id)
            except DeliveryClientError as error:
                show_error(error)
                continue
            st.json(details)
            if approver:
                with st.form(f"case_status_{case_id}"):
                    status = st.selectbox(
                        "更新处理状态",
                        ["processing", "resolved", "closed"],
                        format_func=lambda value: STATUSES[value],
                    )
                    resolution = st.text_area("处理说明 / 解决结果")
                    submit = st.form_submit_button("保存处理进度")
                if submit:
                    if not resolution.strip():
                        st.warning("请填写处理说明。")
                    else:
                        try:
                            client.set_case_status(case_id, status, resolution.strip())
                            st.rerun()
                        except DeliveryClientError as error:
                            show_error(error)


def main():
    st.set_page_config(page_title="售后服务工作台", page_icon="📦", layout="wide")
    st.title("售后服务工作台")
    st.caption("查询订单与物流，提交售后申请，跟踪审批和处理结果。退款与补发均为模拟业务。")
    with st.sidebar:
        api_url = st.text_input(
            "API 地址", value=os.getenv("DELIVERY_API_URL", "http://127.0.0.1:8090")
        )
        previous_url = st.session_state.get("delivery_api_url")
        if previous_url and previous_url != api_url:
            st.query_params.pop("conversation", None)
            for key in list(st.session_state):
                if key.startswith("delivery_") and key != "delivery_api_url":
                    del st.session_state[key]
        st.session_state["delivery_api_url"] = api_url
    try:
        client = DeliveryClient(api_url, st.session_state.get("delivery_token"))
    except DeliveryClientError as error:
        show_error(error)
        return
    with client:
        if not st.session_state.get("delivery_token"):
            st.subheader("登录")
            with st.form("delivery_login", clear_on_submit=True):
                username = st.text_input("用户名", autocomplete="username")
                password = st.text_input("密码", type="password", autocomplete="current-password")
                submit = st.form_submit_button("登录")
            if submit:
                if not username.strip() or not password:
                    st.warning("请输入用户名和密码。")
                else:
                    try:
                        login = client.login(username.strip(), password)
                        if not login.get("token") or not isinstance(login.get("user"), dict):
                            raise DeliveryClientError(
                                "invalid_response", "登录响应格式异常，请联系管理员。"
                            )
                        st.session_state["delivery_token"] = login["token"]
                        st.session_state["delivery_user"] = login["user"]
                        st.rerun()
                    except DeliveryClientError as error:
                        show_error(error)
            return
        user = st.session_state.get("delivery_user", {})
        approver = user.get("role") == "approver"
        with st.sidebar:
            st.write(f"当前账号：{user.get('username', '—')}")
            st.write("角色：审批员" if approver else "角色：客户")
            if user.get("customer_id"):
                st.caption(f"客户编号：{user['customer_id']}")
            if st.button("退出登录"):
                st.query_params.pop("conversation", None)
                for key in list(st.session_state):
                    if key.startswith("delivery_"):
                        del st.session_state[key]
                st.rerun()
        work, cases = st.tabs(["审批与恢复" if approver else "售后对话", "工单进度"])
        with work:
            if approver:
                approval_panel(client)
            else:
                customer_panel(client)
        with cases:
            cases_panel(client, approver)


if __name__ == "__main__":
    main()
