"""Execute the exact human-approved application over a scoped MCP session."""

import json
import re
from typing import Any

from mcp import ClientSession
from mcp.types import CallToolResult, TextContent

from aftersales.approvals import (
    AftersalesApprovalError,
    CaseApprovalState,
    CaseExecutor,
    case_result_messages,
    prepare_case_proposal,
)
from aftersales.mcp_client import order_mcp_session


class AftersalesCaseExecutionError(AftersalesApprovalError):
    """A constant-message execution failure safe for the local CLI."""


async def invoke_approved_case(session: ClientSession, pending: dict[str, Any]) -> CallToolResult:
    """User core: call create_case with the exact saved order and description."""
    return await session.call_tool(
        "create_case",
        {
            "order_id": pending["order_id"],
            "description": pending["description"],
        },
    )


def validate_case_result(record: Any, pending: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "order_id": pending["order_id"],
        "case_type": "logistics_exception",
        "description": pending["description"],
    }
    if (
        not isinstance(record, dict)
        or record.get("case_ready") is not True
        or record.get("customer_id") != pending["customer_id"]
        or record.get("request_id") != pending["request_id"]
        or record.get("request_payload") != expected
        or not isinstance(record.get("case"), dict)
    ):
        raise AftersalesCaseExecutionError("CASE_RESULT_INVALID")
    case = record["case"]
    case_id = case.get("case_id")
    if (
        not isinstance(case_id, str)
        or not case_id.strip()
        or len(case_id) > 32
        or any(case.get(name) != value for name, value in expected.items())
        or case.get("status") not in {"open", "in_progress", "resolved", "closed"}
    ):
        raise AftersalesCaseExecutionError("CASE_RESULT_INVALID")
    return record


def parse_case_result(result: CallToolResult, pending: dict[str, Any]) -> dict[str, Any]:
    if result.isError:
        text = " ".join(item.text for item in result.content if isinstance(item, TextContent))
        for code in (
            "ORDER_NOT_FOUND",
            "CASE_REQUEST_CONFLICT",
            "CASE_INPUT_INVALID",
            "CASE_DATA_INVALID",
            "CASE_READ_BACK_FAILED",
            "CASE_OPERATION_FAILED",
        ):
            if re.search(rf"\b{code}\b", text):
                raise AftersalesCaseExecutionError(code)
        raise AftersalesCaseExecutionError("MCP_CASE_ERROR")
    if len(result.content) != 1 or not isinstance(result.content[0], TextContent):
        raise AftersalesCaseExecutionError("CASE_RESULT_INVALID")
    try:
        record = json.loads(result.content[0].text)
    except (ValueError, TypeError):
        raise AftersalesCaseExecutionError("CASE_RESULT_INVALID") from None
    return validate_case_result(record, pending)


def build_case_executor(
    *, host: str, port: int, username: str, password: str, customer_id: str
) -> CaseExecutor:
    async def execute(state: CaseApprovalState) -> dict[str, Any]:
        if state.get("approval_decision") is not True:
            raise AftersalesApprovalError("Case execution requires explicit human approval.")
        original = state.get("pending_case")
        if not isinstance(original, dict):
            raise AftersalesApprovalError("Case execution requires an already saved application.")
        pending = prepare_case_proposal(state, customer_id)["pending_case"]
        if pending != original:
            raise AftersalesApprovalError("The saved application does not match its tool request.")
        async with order_mcp_session(
            host=host,
            port=port,
            username=username,
            password=password,
            customer_id=customer_id,
            case_request_id=pending["request_id"],
        ) as session:
            result = await invoke_approved_case(session, pending)
        record = parse_case_result(result, pending)
        return {
            "pending_case": {**pending, "status": "created"},
            "approval_decision": True,
            "case_result": record,
            "messages": case_result_messages(state, record, status="success"),
        }

    return execute
