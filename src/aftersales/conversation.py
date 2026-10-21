"""Run a message within a customer-scoped checkpoint and live read-only MCP session."""

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import StateSnapshot
from langsmith import tracing_context

from aftersales.approvals import build_case_proposal_tool
from aftersales.checkpoints import build_checkpoint_config
from aftersales.graph import build_aftersales_graph
from aftersales.mcp_client import load_order_mcp_tools, order_mcp_session
from aftersales.models import get_bound_model
from aftersales.task_state import get_task_snapshot


class AftersalesMessageConflict(ValueError):
    """The saved task requires an explicit operation rather than another message."""


async def invoke_task_message(graph, config: RunnableConfig, message: str):
    """User core: submit a HumanMessage and run until completion or interrupt."""
    return await graph.ainvoke(
        {"messages": [HumanMessage(content=message)]},
        config=config,
        durability="sync",
    )


async def submit_task_message(
    saver: BaseCheckpointSaver,
    customer_id: str,
    thread_id: str,
    message: str,
    connection: dict,
) -> StateSnapshot:
    config = build_checkpoint_config(customer_id, thread_id)
    saved = await get_task_snapshot(saver, customer_id, thread_id)
    if saved.next or saved.values.get("pending_case") is not None:
        # Never use a text message to resume approval, retry a failed node, or
        # replace an application's persisted request_id. A new case uses a new task.
        raise AftersalesMessageConflict("Task requires an explicit operation or a new thread_id.")

    async with order_mcp_session(**connection, customer_id=customer_id) as session:
        tools = await load_order_mcp_tools(session)
        if any(item.name not in {"get_order", "get_shipments"} for item in tools):
            raise ValueError("Message sessions must expose read-only MCP tools.")
        # This local tool is a schema for proposing a write, never a live writer.
        tools.append(build_case_proposal_tool())
        graph = build_aftersales_graph(
            get_bound_model(tools), tools, checkpointer=saver, customer_id=customer_id
        )
        with tracing_context(enabled=False):
            await invoke_task_message(graph, config, message)
        return await graph.aget_state(config)
