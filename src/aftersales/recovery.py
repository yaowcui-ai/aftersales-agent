"""Recover saved approval tasks without running a model."""

from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command, StateSnapshot

from aftersales.approvals import (
    AftersalesApprovalError,
    CaseExecutor,
    CaseResponder,
    build_case_proposal_tool,
    prepare_case_proposal,
)
from aftersales.execution import validate_case_result
from aftersales.graph import build_aftersales_graph
from aftersales.replies import validate_case_reply, validate_created_case


def _unreachable_model(_):
    raise AftersalesApprovalError("Approval recovery must not invoke a model.")


async def resume_case_rejection(graph, config: RunnableConfig) -> None:
    """User core: return an explicit False decision to the saved interrupt."""
    from langgraph.types import Command

    await graph.ainvoke(
        Command(resume=False),
        config=config,
        durability="sync",
    )


async def reject_saved_case(
    saver: BaseCheckpointSaver, config: RunnableConfig, customer_id: str
) -> StateSnapshot:
    graph = build_aftersales_graph(
        RunnableLambda(_unreachable_model),
        [build_case_proposal_tool()],
        checkpointer=saver,
        customer_id=customer_id,
    )
    before = await graph.aget_state(config)
    if (
        before.next != ("case_approval",)
        or not any(task.name == "case_approval" and task.interrupts for task in before.tasks)
        or before.values.get("approval_decision") is not None
    ):
        raise AftersalesApprovalError("No waiting case approval interrupt exists for this task.")
    original = before.values.get("pending_case")
    if not isinstance(original, dict):
        raise AftersalesApprovalError("No saved case application exists for this task.")
    validated = prepare_case_proposal(before.values, customer_id)["pending_case"]
    if validated != original:
        raise AftersalesApprovalError("The saved application does not match its tool request.")
    await resume_case_rejection(graph, config)
    after = await graph.aget_state(config)
    if (
        after.next
        or after.values.get("approval_decision") is not False
        or after.values.get("pending_case") != {**original, "status": "rejected"}
    ):
        raise AftersalesApprovalError(
            "The task did not finish with the saved application rejected."
        )
    return after


async def reply_to_saved_case(
    saver: BaseCheckpointSaver,
    config: RunnableConfig,
    customer_id: str,
    case_responder: CaseResponder,
) -> StateSnapshot:
    graph = build_aftersales_graph(
        RunnableLambda(_unreachable_model),
        [build_case_proposal_tool()],
        checkpointer=saver,
        customer_id=customer_id,
        case_responder=case_responder,
    )
    before = await graph.aget_state(config)
    record = validate_created_case(before.values, customer_id)
    stored_reply = before.values.get("case_reply")
    if stored_reply is not None:
        messages = before.values.get("messages", [])
        if before.next or not messages or validate_case_reply(messages[-1], record) != stored_reply:
            raise AftersalesApprovalError("The saved final reply is inconsistent with this task.")
        return before
    if before.next not in ((), ("case_reply",)):
        raise AftersalesApprovalError("This task is not ready to generate its final reply.")
    if not before.next:
        # The case really completed before this node existed. Keep its verified
        # result and schedule the new reply edge; this never invokes execute_case.
        await graph.aupdate_state(config, {"case_result": record}, as_node="execute_case")
    await graph.ainvoke(None, config=config, durability="sync")
    after = await graph.aget_state(config)
    if (
        after.next
        or after.values.get("pending_case") != before.values["pending_case"]
        or after.values.get("case_result") != record
        or after.values.get("approval_decision") is not True
    ):
        raise AftersalesApprovalError("The final reply changed the completed case state.")
    if validate_case_reply(after.values["messages"][-1], record) != after.values.get("case_reply"):
        raise AftersalesApprovalError("The final case reply was not saved correctly.")
    return after


async def approve_saved_case(
    saver: BaseCheckpointSaver,
    config: RunnableConfig,
    customer_id: str,
    case_executor: CaseExecutor,
) -> StateSnapshot:
    graph = build_aftersales_graph(
        RunnableLambda(_unreachable_model),
        [build_case_proposal_tool()],
        checkpointer=saver,
        customer_id=customer_id,
        case_executor=case_executor,
    )
    before = await graph.aget_state(config)
    pending = before.values.get("pending_case")
    if not isinstance(pending, dict) or pending.get("customer_id") != customer_id:
        raise AftersalesApprovalError("No saved case application exists for this customer/task.")
    if (
        not before.next
        and pending.get("status") == "created"
        and before.values.get("approval_decision") is True
    ):
        validate_case_result(before.values.get("case_result"), pending)
        return before
    if prepare_case_proposal(before.values, customer_id)["pending_case"] != pending:
        raise AftersalesApprovalError("The saved application does not match its tool request.")
    is_waiting = (
        before.next == ("case_approval",)
        and before.values.get("approval_decision") is None
        and any(task.name == "case_approval" and task.interrupts for task in before.tasks)
    )
    is_retry = before.next == ("execute_case",) and before.values.get("approval_decision") is True
    if not is_waiting and not is_retry:
        raise AftersalesApprovalError("No waiting approval or approved execution exists here.")
    await graph.ainvoke(
        Command(resume=True) if is_waiting else None,
        config=config,
        durability="sync",
    )
    after = await graph.aget_state(config)
    if (
        after.next
        or after.values.get("approval_decision") is not True
        or after.values.get("pending_case") != {**pending, "status": "created"}
    ):
        raise AftersalesApprovalError("The approved case execution did not finish.")
    validate_case_result(after.values.get("case_result"), pending)
    return after
