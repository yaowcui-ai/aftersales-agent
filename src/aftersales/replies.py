"""Generate a customer reply from a verified, already persisted case result."""

import json
from collections.abc import Callable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable

from aftersales.approvals import AftersalesApprovalError, CaseApprovalState, CaseResponder
from aftersales.execution import validate_case_result
from business.cases import validate_case_input

CASE_REPLY_INSTRUCTIONS = (
    "你负责向客户说明已经登记的物流异常工单结果。"
    "业务事实以本轮成功 create_case 工具返回的工单记录为准。"
    "用简洁中文回复，必须完整给出订单号和工单号，并根据返回状态说明当前进度。"
    "open 表示工单已登记、待处理，不表示物流问题已经解决。"
    "不能编造处理结果、退款、补发、联系承运商或预计完成时间。"
    "保留问题描述中模拟数据的标识。无需再次申请、调用工具或索取审批，直接回复。"
    "不向客户解释内部申请编号、数据库或检查点实现。"
)


class AftersalesCaseReplyError(AftersalesApprovalError):
    """A constant-message validation failure without private model/request details."""


async def invoke_case_reply(model: Runnable, messages: list[BaseMessage]) -> AIMessage:
    """User core: ask the plain model to express the saved execution result."""
    return await model.ainvoke(messages)


def validate_created_case(state: CaseApprovalState, customer_id: str) -> dict[str, Any]:
    pending = state.get("pending_case")
    if (
        not isinstance(pending, dict)
        or pending.get("customer_id") != customer_id
        or pending.get("status") != "created"
        or state.get("approval_decision") is not True
    ):
        raise AftersalesCaseReplyError("A reply requires this customer's approved, created case.")
    try:
        validate_case_input(
            customer_id=customer_id,
            request_id=pending.get("request_id"),
            order_id=pending.get("order_id"),
            description=pending.get("description"),
        )
    except ValueError:
        raise AftersalesCaseReplyError(
            "The saved case application contains invalid fields."
        ) from None
    record = validate_case_result(state.get("case_result"), pending)
    messages = state.get("messages", [])
    call_id = pending.get("tool_call_id")
    if not isinstance(messages, list) or not isinstance(call_id, str) or not call_id:
        raise AftersalesCaseReplyError("The saved case tool history is invalid.")
    calls = [
        call
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
        if call.get("id") == call_id
    ]
    results = [
        message
        for message in messages
        if isinstance(message, ToolMessage) and message.tool_call_id == call_id
    ]
    if (
        len(calls) != 1
        or calls[0]["name"] != "create_case"
        or calls[0]["args"]
        != {"order_id": pending["order_id"], "description": pending["description"]}
        or len(results) != 1
        or results[0].status != "success"
    ):
        raise AftersalesCaseReplyError("The saved case tool request/result does not match.")
    try:
        tool_record = json.loads(results[0].text)
    except (TypeError, ValueError):
        raise AftersalesCaseReplyError("The saved case tool result is invalid.") from None
    if tool_record != record:
        raise AftersalesCaseReplyError("The saved case record differs from its tool result.")
    return record


def validate_case_reply(response: Any, record: dict[str, Any]) -> str:
    if not isinstance(response, AIMessage) or response.tool_calls or not response.text.strip():
        raise AftersalesCaseReplyError(
            "A final case reply must be nonempty text without ToolCalls."
        )
    text = response.text.strip()
    if record["case"]["case_id"] not in text or record["case"]["order_id"] not in text:
        raise AftersalesCaseReplyError(
            "The final reply must include the actual order and case IDs."
        )
    return text


def build_case_responder(
    model_factory: Callable[[], Runnable], *, customer_id: str
) -> CaseResponder:
    async def respond(state: CaseApprovalState) -> dict[str, Any]:
        record = validate_created_case(state, customer_id)
        messages = [SystemMessage(content=CASE_REPLY_INSTRUCTIONS), *state["messages"]]
        response = await invoke_case_reply(model_factory(), messages)
        text = validate_case_reply(response, record)
        return {"messages": [response], "case_reply": text}

    return respond
