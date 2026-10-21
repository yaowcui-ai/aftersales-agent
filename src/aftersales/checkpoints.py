"""Customer-scoped checkpoint configuration for the local aftersales launcher."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncmy
from asyncmy.errors import MySQLError
from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver


class AftersalesCheckpointError(RuntimeError):
    """A checkpoint failure that is safe to display without driver details."""


def build_checkpoint_config(customer_id: str, thread_id: str) -> RunnableConfig:
    for name, value, limit in (("customer_id", customer_id, 32), ("thread_id", thread_id, 64)):
        if not re.fullmatch(rf"[A-Za-z0-9_-]{{1,{limit}}}", value):
            raise ValueError(f"{name} must contain 1-{limit} ASCII letters, digits, _ or -.")
    return {
        "configurable": {"thread_id": f"aftersales:{customer_id}:{thread_id}"},
        "recursion_limit": 8,
    }


async def read_checkpoint_history(
    saver: BaseCheckpointSaver, config: RunnableConfig
) -> list[BaseMessage] | None:
    """Read saved messages without invoking graph nodes or modifying a checkpoint."""
    saved = await saver.aget_tuple(config)
    if saved is None:
        return None
    messages = saved.checkpoint["channel_values"].get("messages", [])
    if not isinstance(messages, list) or not all(
        isinstance(item, BaseMessage) for item in messages
    ):
        raise AftersalesCheckpointError("Saved checkpoint contains an invalid message history.")
    return messages


async def read_checkpoint_approval(
    saver: BaseCheckpointSaver, config: RunnableConfig
) -> dict[str, Any] | None:
    """Inspect saved approval fields without resuming or invoking the graph."""
    saved = await saver.aget_tuple(config)
    if saved is None:
        return None
    values = saved.checkpoint["channel_values"]
    pending = values.get("pending_case")
    decision = values.get("approval_decision")
    if pending is not None and not isinstance(pending, dict):
        raise AftersalesCheckpointError("Saved checkpoint contains an invalid case application.")
    if decision is not None and type(decision) is not bool:
        raise AftersalesCheckpointError("Saved checkpoint contains an invalid approval decision.")
    return {"pending_case": pending, "approval_decision": decision}


@asynccontextmanager
async def aftersales_checkpointer(
    *, host: str, port: int, username: str, password: str
) -> AsyncIterator[BaseCheckpointSaver]:
    # The framework backend loads settings; defer it until a checkpoint run starts.
    from memory.mysql import CHECKPOINT_DATABASE, MySQLSaver

    try:
        async with asyncmy.connect(
            host=host,
            port=port,
            user=username,
            password=password,
            db=CHECKPOINT_DATABASE,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
        ) as connection:
            saver = MySQLSaver(connection)
            try:
                # This subclass only reads the existing schema; it never migrates it.
                await saver.setup()
            except ValueError as error:
                raise AftersalesCheckpointError(
                    f"Checkpoint schema check failed: {error}"
                ) from None
            yield saver
    except MySQLError as error:
        code = error.args[0] if error.args and isinstance(error.args[0], int) else None
        raise AftersalesCheckpointError(
            f"MySQL checkpoint operation failed; error code: {code}"
        ) from None
