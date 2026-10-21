"""Compose explicit approval and final reply while preserving completed writes."""

from collections.abc import Callable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import StateSnapshot

from aftersales.approvals import AftersalesApprovalError, CaseExecutor, CaseResponder
from aftersales.decisions import AftersalesDecisionNotFound, submit_case_decision
from aftersales.http_replies import AftersalesReplyNotReady, submit_case_reply
from aftersales.replies import validate_created_case
from aftersales.task_state import get_task_snapshot


async def submit_case_workflow(
    saver: BaseCheckpointSaver,
    customer_id: str,
    thread_id: str,
    approved: bool,
    executor_factory: Callable[[], CaseExecutor],
    case_responder: CaseResponder,
) -> StateSnapshot:
    """Called under the HTTP task lock; no new application or model judgement."""
    if type(approved) is not bool:
        raise AftersalesApprovalError("Approval must be an explicit boolean decision.")
    saved = await get_task_snapshot(saver, customer_id, thread_id)
    if not saved.values:
        raise AftersalesDecisionNotFound("Aftersales task not found.")
    pending = saved.values.get("pending_case")
    already_created = (
        isinstance(pending, dict)
        and pending.get("customer_id") == customer_id
        and pending.get("status") == "created"
        and saved.values.get("approval_decision") is True
    )
    if approved and already_created:
        # A previous request may have created the case then lost its model reply.
        # Validate that completed action and skip even constructing the writer.
        validate_created_case(saved.values, customer_id)
        if saved.next not in ((), ("case_reply",)):
            raise AftersalesReplyNotReady("The saved case cannot resume its reply here.")
    else:
        saved = await submit_case_decision(
            saver, customer_id, thread_id, approved, executor_factory
        )
    if not approved:
        return saved
    return await submit_case_reply(saver, customer_id, thread_id, case_responder)
