"""Read a customer-scoped task without executing Agent nodes."""

from langchain_core.runnables import RunnableConfig, RunnableLambda
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import StateSnapshot

from aftersales.approvals import build_case_proposal_tool
from aftersales.checkpoints import AftersalesCheckpointError, build_checkpoint_config
from aftersales.graph import build_aftersales_graph


async def _no_execution(_):
    raise AftersalesCheckpointError("Task-state reads must not execute Agent nodes.")


async def read_task_snapshot(graph, config: RunnableConfig) -> StateSnapshot:
    """User core: read the persisted snapshot; never invoke or resume the graph."""
    return await graph.aget_state(config)


async def get_task_snapshot(
    saver: BaseCheckpointSaver, customer_id: str, thread_id: str
) -> StateSnapshot:
    config = build_checkpoint_config(customer_id, thread_id)
    graph = build_aftersales_graph(
        RunnableLambda(_no_execution),
        [build_case_proposal_tool()],
        checkpointer=saver,
        customer_id=customer_id,
        case_responder=_no_execution,
    )
    return await read_task_snapshot(graph, config)
