"""HTTP final-reply recovery for a verified, already created case."""

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import StateSnapshot
from langsmith import tracing_context

from aftersales.approvals import CaseResponder
from aftersales.checkpoints import build_checkpoint_config
from aftersales.recovery import reply_to_saved_case
from aftersales.replies import AftersalesCaseReplyError, validate_created_case
from aftersales.task_state import get_task_snapshot


class AftersalesReplyNotFound(LookupError):
    """No task is visible in this customer's scope."""


class AftersalesReplyNotReady(ValueError):
    """A verified created case is required before generating its final reply."""


async def resume_http_case_reply(
    saver: BaseCheckpointSaver,
    config: RunnableConfig,
    customer_id: str,
    case_responder: CaseResponder,
) -> StateSnapshot:
    """User core: resume only the reply stage of the already created case."""
    return await reply_to_saved_case(saver, config, customer_id, case_responder)


async def submit_case_reply(
    saver: BaseCheckpointSaver,
    customer_id: str,
    thread_id: str,
    case_responder: CaseResponder,
) -> StateSnapshot:
    config = build_checkpoint_config(customer_id, thread_id)
    saved = await get_task_snapshot(saver, customer_id, thread_id)
    if not saved.values:
        raise AftersalesReplyNotFound("Aftersales task not found.")
    pending = saved.values.get("pending_case")
    if isinstance(pending, dict) and pending.get("customer_id") != customer_id:
        raise AftersalesReplyNotFound("Aftersales task not found.")
    try:
        validate_created_case(saved.values, customer_id)
    except AftersalesCaseReplyError:
        raise AftersalesReplyNotReady("This task has no verified created case.") from None
    if saved.next not in ((), ("case_reply",)):
        raise AftersalesReplyNotReady("This task is not ready for its final reply.")
    with tracing_context(enabled=False):
        return await resume_http_case_reply(saver, config, customer_id, case_responder)
