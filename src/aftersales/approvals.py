"""Persist exact case proposals before the user-owned human approval pause."""

import json
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph import MessagesState

from business.cases import validate_case_input


class AftersalesApprovalError(ValueError):
    """A constant-message approval validation error safe to display."""


class CaseApprovalState(MessagesState):
    pending_case: dict[str, Any] | None
    approval_decision: bool | None
    case_result: dict[str, Any] | None
    case_reply: str | None


type CaseExecutor = Callable[[CaseApprovalState], Awaitable[dict[str, Any]]]
type CaseResponder = Callable[[CaseApprovalState], Awaitable[dict[str, Any]]]


def build_case_proposal_tool() -> BaseTool:
    @tool("create_case")
    async def propose_case(order_id: str, description: str) -> str:
        """提出物流异常工单申请，等待系统人工确认；本次调用不代表工单已创建。

        order_id 是客户提供的订单号，description 保留客户描述的问题。
        一次只提出一张工单；客户身份和申请编号由后端确定。
        """
        raise AftersalesApprovalError("A case proposal cannot execute a business write directly.")

    return propose_case


def _single_case_call(state: MessagesState) -> dict[str, Any]:
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[-1], AIMessage):
        raise AftersalesApprovalError("A case proposal requires the model's tool request.")
    ids = [call.get("id") for call in messages[-1].tool_calls]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise AftersalesApprovalError("The saved tool batch contains invalid call identifiers.")
    calls = [call for call in messages[-1].tool_calls if call["name"] == "create_case"]
    if len(calls) != 1 or not isinstance(calls[0].get("id"), str) or not calls[0]["id"]:
        raise AftersalesApprovalError("Exactly one identified case proposal is supported per task.")
    arguments = calls[0]["args"]
    if set(arguments) != {"order_id", "description"}:
        raise AftersalesApprovalError("A case proposal accepts only order_id and description.")
    return calls[0]


def prepare_case_proposal(state: CaseApprovalState, customer_id: str) -> dict[str, Any]:
    call = _single_case_call(state)
    arguments = call["args"]
    previous = state.get("pending_case")
    if previous is not None:
        if not isinstance(previous, dict) or any(
            previous.get(name) != value
            for name, value in {
                "customer_id": customer_id,
                "tool_call_id": call["id"],
                "order_id": arguments["order_id"],
                "description": arguments["description"],
                "status": "waiting_approval",
            }.items()
        ):
            raise AftersalesApprovalError("This task already has a different saved application.")
        request_id = previous.get("request_id")
    else:
        request_id = uuid4().hex
    try:
        validate_case_input(
            customer_id=customer_id,
            request_id=request_id,
            order_id=arguments["order_id"],
            description=arguments["description"],
        )
    except ValueError:
        raise AftersalesApprovalError("The case application contains invalid fields.") from None
    return {
        "pending_case": {
            "customer_id": customer_id,
            "request_id": request_id,
            "tool_call_id": call["id"],
            "order_id": arguments["order_id"],
            "description": arguments["description"],
            "status": "waiting_approval",
        },
        "approval_decision": None,
    }


def case_approval_payload(state: CaseApprovalState) -> dict[str, Any]:
    pending = state.get("pending_case")
    if not isinstance(pending, dict):
        raise AftersalesApprovalError("No saved case proposal is available for approval.")
    validated = prepare_case_proposal(state, pending.get("customer_id"))
    return {
        "action": "create_case",
        **validated["pending_case"],
        "question": "是否同意登记这张物流异常工单？",
    }


def approval_decision_update(decision: Any) -> dict[str, bool]:
    if type(decision) is not bool:
        raise AftersalesApprovalError("Approval must be an explicit boolean decision.")
    return {"approval_decision": decision}


def request_case_approval(state: CaseApprovalState) -> dict[str, bool]:
    """Pause on the exact saved application; the interrupt call is the user's core."""
    from langgraph.types import interrupt

    decision = interrupt(case_approval_payload(state))
    return approval_decision_update(decision)


def route_after_case_approval(state: CaseApprovalState) -> str:
    if state.get("approval_decision") is False:
        return "reject_case"
    if state.get("approval_decision") is True:
        return "execute_case"
    raise AftersalesApprovalError("Approval must be an explicit boolean decision.")


def case_result_messages(
    state: CaseApprovalState, result: dict[str, Any], *, status: str
) -> list[ToolMessage]:
    call = _single_case_call(state)
    return [
        ToolMessage(
            content=json.dumps(
                result if item["id"] == call["id"] else {"error": "TOOL_BATCH_CANCELLED"},
                ensure_ascii=False,
            ),
            tool_call_id=item["id"],
            name=item["name"],
            status=status if item["id"] == call["id"] else "error",
        )
        for item in state["messages"][-1].tool_calls
    ]


def reject_case_proposal(state: CaseApprovalState) -> dict[str, Any]:
    """Record rejection and close the unexecuted batch without calling any tools."""
    if route_after_case_approval(state) != "reject_case":
        raise AftersalesApprovalError("Rejection requires an explicit False decision.")
    case_approval_payload(state)
    return {
        "pending_case": {**state["pending_case"], "status": "rejected"},
        "approval_decision": False,
        "messages": case_result_messages(
            state, {"case_ready": False, "error": "CASE_REJECTED"}, status="error"
        ),
    }
