"""Apply an HTTP decision to the exact saved application, without a model call."""

from collections.abc import Callable

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import StateSnapshot
from langsmith import tracing_context

from aftersales.approvals import AftersalesApprovalError, CaseExecutor
from aftersales.checkpoints import AftersalesCheckpointError, build_checkpoint_config
from aftersales.recovery import approve_saved_case, reject_saved_case
from aftersales.replies import validate_created_case
from aftersales.task_state import get_task_snapshot


class AftersalesDecisionNotFound(LookupError):
    """No saved task is visible in the server-selected customer scope."""


async def resume_http_approval(
    saver: BaseCheckpointSaver,
    config: RunnableConfig,
    customer_id: str,
    case_executor: CaseExecutor,
) -> StateSnapshot:
    """User core: pass the saved task to the existing approved recovery helper."""
    return await approve_saved_case(saver, config, customer_id, case_executor)


async def submit_case_decision(
    saver: BaseCheckpointSaver,
    customer_id: str,
    thread_id: str,
    approved: bool,
    executor_factory: Callable[[], CaseExecutor],
) -> StateSnapshot:
    if type(approved) is not bool:
        raise AftersalesApprovalError("Approval must be an explicit boolean decision.")
    config = build_checkpoint_config(customer_id, thread_id)
    saved = await get_task_snapshot(saver, customer_id, thread_id)
    if not saved.values:
        raise AftersalesDecisionNotFound("Aftersales task not found.")
    pending = saved.values.get("pending_case")
    if not isinstance(pending, dict):
        raise AftersalesApprovalError("This task has no case application to approve.")
    if pending.get("customer_id") != customer_id:
        raise AftersalesDecisionNotFound("Aftersales task not found.")
    decision = saved.values.get("approval_decision")

    if not saved.next and pending.get("status") == "created" and decision is True:
        if not approved:
            raise AftersalesApprovalError("An accepted approval cannot be reversed.")
        validate_created_case(saved.values, customer_id)
        return saved
    if not saved.next and pending.get("status") == "rejected" and decision is False:
        if approved:
            raise AftersalesApprovalError("A rejected application needs a new task.")
        if saved.values.get("case_result") is not None:
            raise AftersalesCheckpointError("A rejected task contains an inconsistent case result.")
        return saved

    waiting = (
        saved.next == ("case_approval",)
        and decision is None
        and pending.get("status") == "waiting_approval"
        and any(task.name == "case_approval" and task.interrupts for task in saved.tasks)
    )
    retrying = (
        saved.next == ("execute_case",)
        and decision is True
        and pending.get("status") == "waiting_approval"
    )
    if not waiting and not (approved and retrying):
        raise AftersalesApprovalError("This task cannot accept this approval decision.")

    with tracing_context(enabled=False):
        if approved:
            # The factory reads project credentials, but does not open MCP until
            # the approved execution node runs. Replays/rejections never call it.
            return await resume_http_approval(saver, config, customer_id, executor_factory())
        return await reject_saved_case(saver, config, customer_id)
