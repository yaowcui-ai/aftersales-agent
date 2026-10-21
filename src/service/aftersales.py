"""Aftersales HTTP operations using a server-owned local demo customer identity."""

import asyncio
import logging
from typing import Annotated, Any
from weakref import WeakValueDictionary

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import StateSnapshot
from pydantic import BaseModel, ConfigDict, Field, field_validator

from aftersales.approvals import AftersalesApprovalError
from aftersales.checkpoints import AftersalesCheckpointError
from aftersales.conversation import AftersalesMessageConflict, submit_task_message
from aftersales.decisions import AftersalesDecisionNotFound
from aftersales.execution import AftersalesCaseExecutionError, build_case_executor
from aftersales.http_replies import (
    AftersalesReplyNotFound,
    AftersalesReplyNotReady,
    submit_case_reply,
)
from aftersales.models import get_case_reply_model
from aftersales.replies import AftersalesCaseReplyError, build_case_responder
from aftersales.task_state import get_task_snapshot
from aftersales.workflow import submit_case_workflow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/aftersales", tags=["aftersales"])


class AftersalesTaskState(BaseModel):
    model_config = ConfigDict(strict=True)

    thread_id: str
    pending_case: dict[str, Any] | None = None
    approval_decision: bool | None = None
    case_result: dict[str, Any] | None = None
    final_answer: str | None = None
    message_count: int
    next_nodes: list[str]


class TaskMessageInput(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        json_schema_extra={
            "example": {
                "message": "请为订单 DEMO-001 提出物流异常工单申请，"
                "问题描述：收件信息待核实，派送暂缓。"
            }
        },
    )

    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be blank.")
        return value


class TaskApprovalInput(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", json_schema_extra={"example": {"approved": True}}
    )

    approved: bool = Field(description="true 同意创建原申请的工单；false 拒绝。")


def get_mcp_connection() -> dict:
    from core.settings import settings

    if settings.MYSQL_USER is None or settings.MYSQL_PASSWORD is None:
        raise HTTPException(status_code=503, detail="Aftersales tool connection is unavailable.")
    return {
        "host": settings.MYSQL_HOST,
        "port": settings.MYSQL_PORT,
        "username": settings.MYSQL_USER,
        "password": settings.MYSQL_PASSWORD.get_secret_value(),
    }


def task_operation_lock(request: Request, customer_id: str, thread_id: str) -> asyncio.Lock:
    # Single-process HTTP serialization. CLI/other service workers need their own
    # coordination; do not run them concurrently against this teaching task.
    locks = getattr(request.app.state, "aftersales_task_locks", None)
    if locks is None:
        locks = WeakValueDictionary()
        request.app.state.aftersales_task_locks = locks
    key = (customer_id, thread_id)
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


def project_task_state(
    snapshot: StateSnapshot, customer_id: str, thread_id: str
) -> AftersalesTaskState:
    values = snapshot.values
    if not values:
        raise HTTPException(status_code=404, detail="Aftersales task not found.")
    pending = values.get("pending_case")
    record = values.get("case_result")
    for item in (pending, record):
        if item is not None and (
            not isinstance(item, dict) or item.get("customer_id") != customer_id
        ):
            raise AftersalesCheckpointError("Saved task has an inconsistent customer scope.")
    messages = values.get("messages", [])
    if not isinstance(messages, list) or not all(
        isinstance(message, BaseMessage) for message in messages
    ):
        raise AftersalesCheckpointError("Saved task contains invalid messages.")
    # Read-only answers live in messages; case_reply is only set after case creation.
    # Never expose an earlier answer as the result of a pending or failed new turn.
    last = messages[-1] if messages else None
    answer = (
        last.content
        if not snapshot.next
        and isinstance(last, AIMessage)
        and not last.tool_calls
        and isinstance(last.content, str)
        and last.content.strip()
        else None
    )
    return AftersalesTaskState(
        thread_id=thread_id,
        pending_case=pending,
        approval_decision=values.get("approval_decision"),
        case_result=record,
        final_answer=answer,
        message_count=len(messages),
        next_nodes=list(snapshot.next),
    )


def get_demo_customer_id() -> str:
    """Local teaching identity; replace with authenticated customer lookup later.

    Caller-supplied IDs and the service bearer token do not choose this identity.
    """
    return "learner-001"


def get_task_checkpointer(request: Request) -> BaseCheckpointSaver:
    saver = getattr(request.app.state, "aftersales_checkpointer", None)
    if saver is None:
        raise HTTPException(status_code=503, detail="Aftersales task storage is unavailable.")
    return saver


@router.get("/tasks/{thread_id}", response_model=AftersalesTaskState)
async def get_aftersales_task(
    thread_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
    customer_id: Annotated[str, Depends(get_demo_customer_id)],
    saver: Annotated[BaseCheckpointSaver, Depends(get_task_checkpointer)],
) -> AftersalesTaskState:
    """Read the server-selected demo customer's saved task without running the Agent."""
    try:
        snapshot = await get_task_snapshot(saver, customer_id, thread_id)
        return project_task_state(snapshot, customer_id, thread_id)
    except HTTPException:
        raise
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="Complete read_task_snapshot first.") from None
    except Exception as error:
        # Driver messages can contain SQL or private parameters; report type only.
        logger.warning("Aftersales task-state read failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=503, detail="Aftersales task state could not be read."
        ) from None


@router.post("/tasks/{thread_id}/messages", response_model=AftersalesTaskState)
async def post_aftersales_message(
    request: Request,
    thread_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
    body: TaskMessageInput,
    customer_id: Annotated[str, Depends(get_demo_customer_id)],
    saver: Annotated[BaseCheckpointSaver, Depends(get_task_checkpointer)],
) -> AftersalesTaskState:
    """Run a message to an answer or approval interrupt; never approve via chat text."""
    try:
        async with task_operation_lock(request, customer_id, thread_id):
            snapshot = await submit_task_message(
                saver, customer_id, thread_id, body.message, get_mcp_connection()
            )
            return project_task_state(snapshot, customer_id, thread_id)
    except HTTPException:
        raise
    except AftersalesMessageConflict:
        raise HTTPException(
            status_code=409,
            detail="Task requires an explicit operation; use a new thread_id for a new case.",
        ) from None
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="Complete invoke_task_message first.") from None
    except Exception as error:
        logger.warning("Aftersales message execution failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=503, detail="Aftersales message could not be completed. Read task state."
        ) from None


@router.post("/tasks/{thread_id}/approval", response_model=AftersalesTaskState)
async def post_aftersales_approval(
    request: Request,
    thread_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
    body: TaskApprovalInput,
    customer_id: Annotated[str, Depends(get_demo_customer_id)],
    saver: Annotated[BaseCheckpointSaver, Depends(get_task_checkpointer)],
) -> AftersalesTaskState:
    """Apply the decision; approved cases also generate their saved final reply."""
    try:
        async with task_operation_lock(request, customer_id, thread_id):
            snapshot = await submit_case_workflow(
                saver,
                customer_id,
                thread_id,
                body.approved,
                lambda: build_case_executor(**get_mcp_connection(), customer_id=customer_id),
                build_case_responder(get_case_reply_model, customer_id=customer_id),
            )
            return project_task_state(snapshot, customer_id, thread_id)
    except HTTPException:
        raise
    except (AftersalesDecisionNotFound, AftersalesReplyNotFound):
        raise HTTPException(status_code=404, detail="Aftersales task not found.") from None
    except AftersalesReplyNotReady:
        raise HTTPException(
            status_code=409, detail="This task cannot resume its final reply."
        ) from None
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail="Complete resume_http_approval first."
        ) from None
    except AftersalesCaseExecutionError as error:
        code = str(error)
        if code in {"ORDER_NOT_FOUND", "CASE_REQUEST_CONFLICT"}:
            raise HTTPException(
                status_code=404 if code == "ORDER_NOT_FOUND" else 409, detail=code
            ) from None
        logger.warning("Aftersales approved execution failed: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="Approved case execution failed.") from None
    except AftersalesCaseReplyError as error:
        logger.warning("Aftersales workflow reply failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=503, detail="Final reply could not be completed. Retry the same approval."
        ) from None
    except AftersalesApprovalError:
        raise HTTPException(
            status_code=409, detail="This task cannot accept this approval decision."
        ) from None
    except Exception as error:
        logger.warning("Aftersales approval failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=503,
            detail="Task execution or reply did not finish. Read task state and retry the same approval.",
        ) from None


@router.post("/tasks/{thread_id}/reply", response_model=AftersalesTaskState)
async def post_aftersales_reply(
    request: Request,
    thread_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
    customer_id: Annotated[str, Depends(get_demo_customer_id)],
    saver: Annotated[BaseCheckpointSaver, Depends(get_task_checkpointer)],
) -> AftersalesTaskState:
    """Generate or read the saved case reply, without configuring any case writer."""
    try:
        async with task_operation_lock(request, customer_id, thread_id):
            snapshot = await submit_case_reply(
                saver,
                customer_id,
                thread_id,
                build_case_responder(get_case_reply_model, customer_id=customer_id),
            )
            return project_task_state(snapshot, customer_id, thread_id)
    except HTTPException:
        raise
    except AftersalesReplyNotFound:
        raise HTTPException(status_code=404, detail="Aftersales task not found.") from None
    except AftersalesReplyNotReady:
        raise HTTPException(
            status_code=409, detail="This task is not ready for its final reply."
        ) from None
    except NotImplementedError:
        raise HTTPException(
            status_code=501, detail="Complete resume_http_case_reply first."
        ) from None
    except AftersalesCaseReplyError as error:
        logger.warning("Aftersales final reply validation failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=503, detail="A valid final reply could not be saved."
        ) from None
    except AftersalesApprovalError:
        raise HTTPException(
            status_code=409, detail="This saved task cannot generate its final reply."
        ) from None
    except Exception as error:
        logger.warning("Aftersales final reply failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=503, detail="Final reply failed. Retry this task's reply endpoint."
        ) from None
