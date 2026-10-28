"""Authenticated delivery API; business and graph state remain separate and durable."""

import asyncio
import logging
import json
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Annotated
from weakref import WeakValueDictionary

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi import Path as ApiPath
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import create_async_engine

from aftersales_delivery.agent import build_graph, graph_config
from aftersales_delivery.business import BusinessError, Store
from aftersales_delivery.config import get_config
from aftersales_delivery.security import (
    AuthenticationError,
    decode_token,
    issue_token,
    verify_password,
)
from aftersales_delivery.transport import call_tool

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)
Thread = Annotated[str, ApiPath(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class Input(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class Login(Input):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class Message(Input):
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Message cannot be blank.")
        return value


class Decision(Input):
    approved: bool
    version: int = Field(ge=1)
    reason: str = Field(default="", max_length=1000)


class CaseStatus(Input):
    status: str
    resolution: str = Field(max_length=4000)


def public_error(code, status=409, message="请求未完成，请读取已保存状态后处理。"):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class Runtime:
    def __init__(self, config, store, saver, *, model=None, reply=None, local_tools=False):
        self.config, self.store, self.saver = config, store, saver
        self.model_override, self.reply_override, self.local_tools = model, reply, local_tools
        self.locks = WeakValueDictionary()
        self.bound_model = None
        self.reply_model = None

    def lock(self, customer, thread):
        key = (customer, thread)
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        return lock

    async def model(self, messages, tools):
        if self.model_override:
            return await self.model_override(messages, tools)
        if self.bound_model is None:
            from aftersales.models import get_bound_model

            self.bound_model = get_bound_model(tools)
        return await self.bound_model.ainvoke(messages)

    async def read(self, customer, name, arguments):
        if self.local_tools:
            if name == "list_orders":
                return await self.store.list_orders(customer, **arguments)
            if name == "get_order":
                order = await self.store.get_order(customer, arguments["order_id"])
                return {"found": order is not None, "order": order}
            if name == "get_shipments":
                return {
                    "shipments": await self.store.get_shipments(customer, arguments["order_id"])
                }
            if name == "get_case":
                return await self.store.get_case(customer, arguments["case_id"])
            if name == "get_inventory":
                return await self.store.get_inventory(arguments["sku"])
            raise ValueError("UNKNOWN_TOOL")
        return await call_tool(self.config, customer, name, arguments)

    async def execute(self, action):
        if self.local_tools:
            return await self.store.execute(action["action_id"])
        return await call_tool(
            self.config,
            action["customer_id"],
            "execute_action",
            {"action_id": action["action_id"]},
            action_id=action["action_id"],
        )

    async def reply(self, action):
        if self.reply_override:
            return await self.reply_override(action)
        from langchain_core.messages import SystemMessage

        if self.reply_model is None:
            from aftersales.models import get_case_reply_model

            self.reply_model = get_case_reply_model()
        import json

        answer = await self.reply_model.ainvoke(
            [
                SystemMessage(
                    content="只依据给定的已执行业务结果，用中文简要回复。保留实际工单编号和订单号。"
                    "退款/补发均明确为模拟业务，物流open表示待处理不能称已解决。不得添加承诺或新动作。"
                ),
                HumanMessage(content=json.dumps(action["result"], ensure_ascii=False)),
            ]
        )
        if answer.tool_calls:
            raise ValueError("UNEXPECTED_REPLY_TOOL_CALL")
        return answer

    def graph(self, customer, thread):
        return build_graph(
            self.store,
            self.saver,
            customer,
            thread,
            self.model,
            lambda name, args: self.read(customer, name, args),
            self.execute,
            self.reply,
            max_calls=self.config.max_model_calls,
        )

    async def state(self, customer, thread):
        graph = self.graph(customer, thread)
        snapshot = await graph.aget_state(graph_config(customer, thread))
        if not snapshot.values:
            raise public_error("TASK_NOT_FOUND", 404)
        values = snapshot.values
        messages = []
        for item in values.get("messages", []):
            messages.append(
                {
                    "type": item.type,
                    "content": item.content,
                    "tool_calls": getattr(item, "tool_calls", []),
                }
            )
        metrics = dict(values.get("metrics", {}))
        if "started_at" in metrics:
            metrics["elapsed_seconds"] = round(
                metrics.get("finished_at", time.time()) - metrics["started_at"], 3
            )
        result = {
            "thread_id": thread,
            "customer_id": customer,
            "status": values.get("status", "running"),
            "messages": messages,
            "proposal": values.get("proposal"),
            "final_answer": values.get("final_answer"),
            "next_nodes": list(snapshot.next),
            "metrics": metrics,
        }
        if self.config.platform_enabled:
            from aftersales_delivery.task_tracking import save_task

            proposal = values.get("proposal")
            if proposal:
                action = await self.store.get_action(proposal["action_id"])
                result["proposal"] = action
                result["business_status"] = action["status"]
                if action["status"] == "failed":
                    result.update(status="execution_failed", final_answer=None)
                elif action["status"] == "cancelled":
                    result.update(status="cancelled", final_answer="申请已取消，未执行退款或补发。")
                elif action["status"] == "completed" and result["status"] not in {"completed", "reply_failed"}:
                    result.update(status="recovery_required", final_answer=None,
                                  recovery_required=True)
            await save_task(self.store.engine, customer, thread, result["status"],
                            final_answer=result["final_answer"],
                            action_id=(result.get("proposal") or {}).get("action_id"))
        return result

    async def resume(self, action):
        customer, thread = action["customer_id"], action["thread_id"]
        graph = self.graph(customer, thread)
        config = graph_config(customer, thread)
        for _ in range(4):
            snapshot = await graph.aget_state(config)
            if not snapshot.values:
                raise public_error("TASK_CHECKPOINT_MISSING", 409)
            if snapshot.next:
                command = Command(resume=True) if snapshot.next == ("approval",) else None
                with tracing_context(enabled=False):
                    await graph.ainvoke(command, config, durability="sync")
                after = await graph.aget_state(config)
                if not after.next:
                    return await self.state(customer, thread)
                continue
            if snapshot.values.get("status") == "reply_failed" or (
                action["status"] == "completed"
                and snapshot.values.get("status") == "awaiting_return"
            ):
                await graph.aupdate_state(
                    config, {"proposal": action, "status": "executed"}, as_node="execute"
                )
                continue
            return await self.state(customer, thread)
        raise public_error("RECOVERY_LIMIT", 503)


def create_app(config=None, *, model=None, reply=None, local_tools=False):
    config = config or get_config()

    @asynccontextmanager
    async def lifespan(app):
        engine = create_async_engine(config.db_url, pool_pre_ping=True)
        try:
            if config.platform_enabled:
                from aftersales_delivery.db import initialize, seed_platform
                await initialize(engine)
                await seed_platform(engine)
            async with AsyncExitStack() as stack:
                if config.checkpoint_backend == "sqlite":
                    Path(config.checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
                    saver = await stack.enter_async_context(
                        AsyncSqliteSaver.from_conn_string(config.checkpoint_path)
                    )
                elif config.checkpoint_backend == "mysql":
                    from memory.mysql import get_mysql_saver

                    saver = await stack.enter_async_context(get_mysql_saver())
                    await saver.setup()
                else:
                    raise ValueError("UNKNOWN_CHECKPOINT_BACKEND")
                app.state.runtime = Runtime(
                    config, Store(engine), saver, model=model, reply=reply, local_tools=local_tools
                )
                yield
        finally:
            await engine.dispose()

    app = FastAPI(title="电商售后 Agent 交付服务", version="1.0.0", lifespan=lifespan)
    login_attempts = {}

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        # Never log bodies, credentials, tokens, database URLs or model prompts.
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.warning(json.dumps({"event": "request_failed", "request_id": request_id,
                                       "method": request.method, "route": request.url.path}))
            raise
        response.headers["X-Request-ID"] = request_id
        logger.info(json.dumps({"event": "http_request", "request_id": request_id,
                               "method": request.method, "route": request.url.path,
                               "status": response.status_code,
                               "elapsed_ms": round((time.monotonic() - started) * 1000, 2)}))
        return response

    async def runtime(request: Request):
        return request.app.state.runtime

    async def identity(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        if credentials is None:
            raise public_error("UNAUTHORIZED", 401, "请先登录。")
        try:
            claims = decode_token(credentials.credentials, config.signing_key)
        except AuthenticationError:
            raise public_error("UNAUTHORIZED", 401, "登录信息无效或已过期。") from None
        account = config.accounts.get(claims["sub"])
        if not account or (account["role"], account["customer_id"]) != (
            claims["role"],
            claims["customer_id"],
        ):
            raise public_error("UNAUTHORIZED", 401)
        return {
            "username": claims["sub"],
            "role": claims["role"],
            "customer_id": claims["customer_id"],
        }

    async def approver(user=Depends(identity)):
        if user["role"] != "approver":
            raise public_error("FORBIDDEN", 403, "当前账号没有审批权限。")
        return user

    @app.exception_handler(BusinessError)
    async def business_error(_, error):
        from fastapi.responses import JSONResponse

        status = (
            404 if error.code in {"ORDER_NOT_FOUND", "CASE_NOT_FOUND", "ACTION_NOT_FOUND", "TICKET_NOT_FOUND"} else 409
        )
        return JSONResponse(
            status_code=status,
            content={
                "detail": {
                    "code": error.code,
                    "message": "业务规则拒绝该操作，请核对订单、参数或当前状态。",
                }
            },
        )

    @app.exception_handler(Exception)
    async def execution_error(_, error):
        from fastapi.responses import JSONResponse

        logger.warning("Delivery operation incomplete: %s", type(error).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "OPERATION_INCOMPLETE",
                    "message": "操作或回复未完成，请先读取保存状态；已批准动作沿用原编号重试。",
                }
            },
        )

    @app.get("/health")
    async def health(rt=Depends(runtime)):
        await rt.store.get_inventory("SKU-001")
        return {"status": "ok", "business": "simulated", "checkpoints": config.checkpoint_backend}

    @app.post("/auth/login")
    async def login(body: Login, request: Request):
        key = (request.client.host if request.client else "local", body.username)
        attempts = [value for value in login_attempts.get(key, []) if time.monotonic() - value < 60]
        if len(attempts) >= 8:
            raise public_error("LOGIN_RATE_LIMIT", 429, "登录尝试过多，请稍后重试。")
        account = config.accounts.get(body.username)
        valid = account and await asyncio.to_thread(
            verify_password, body.password, account["password_hash"]
        )
        if not valid:
            login_attempts[key] = [*attempts, time.monotonic()]
            raise public_error("INVALID_LOGIN", 401, "账号或密码不正确。")
        login_attempts.pop(key, None)
        user = {
            "username": body.username,
            "role": account["role"],
            "customer_id": account["customer_id"],
        }
        return {
            "token": issue_token(
                config.signing_key,
                **{
                    "username": body.username,
                    "role": account["role"],
                    "customer_id": account["customer_id"],
                },
            ),
            "user": user,
        }

    @app.get("/me")
    async def me(user=Depends(identity)):
        return user

    @app.post("/tasks/{thread_id}/messages")
    async def message(
        thread_id: Thread, body: Message, user=Depends(identity), rt=Depends(runtime)
    ):
        if user["role"] != "customer":
            raise public_error("CUSTOMER_REQUIRED", 403)
        customer = user["customer_id"]
        async with rt.lock(customer, thread_id):
            graph, graph_options = rt.graph(customer, thread_id), graph_config(customer, thread_id)
            snapshot = await graph.aget_state(graph_options)
            if snapshot.next or snapshot.values.get("proposal"):
                raise public_error(
                    "EXPLICIT_OPERATION_REQUIRED", 409, "任务已有待处理操作；新申请请新建任务。"
                )
            if config.platform_enabled:
                from aftersales_delivery.task_tracking import save_task
                await save_task(rt.store.engine, customer, thread_id, "running", last_message=body.message)
            try:
                with tracing_context(enabled=False):
                    await graph.ainvoke(
                    {
                        "messages": [HumanMessage(content=body.message)],
                        "turn_calls": 0,
                        "status": "running",
                        "final_answer": None,
                        "metrics": {"started_at": time.time()},
                    },
                    graph_options,
                    durability="sync",
                    )
            except Exception as error:
                if config.platform_enabled:
                    await save_task(rt.store.engine, customer, thread_id, "failed", error_code=type(error).__name__)
                raise
            return await rt.state(customer, thread_id)

    @app.get("/tasks/{thread_id}")
    async def task(
        thread_id: Thread,
        customer_id: str | None = None,
        user=Depends(identity),
        rt=Depends(runtime),
    ):
        customer = user["customer_id"]
        if user["role"] == "approver":
            if customer_id:
                customer = customer_id
            else:
                matches = [
                    action["customer_id"]
                    for action in await rt.store.list_actions(None)
                    if action["thread_id"] == thread_id
                ]
                if len(set(matches)) != 1:
                    raise public_error("TASK_NOT_FOUND_OR_AMBIGUOUS", 404)
                customer = matches[0]
        return await rt.state(customer, thread_id)

    @app.get("/approvals")
    async def approvals(user=Depends(approver), rt=Depends(runtime)):
        return await rt.store.list_actions(None)

    async def decision_result(action_id, approved, version, user, rt, *, retry=False, reason=""):
        action = await rt.store.get_action(action_id)
        if action is None:
            raise public_error("ACTION_NOT_FOUND", 404)
        async with rt.lock(action["customer_id"], action["thread_id"]):
            if retry:
                if action["status"] not in {"approved", "completed", "failed", "awaiting_return"}:
                    raise public_error("NOT_APPROVED")
            else:
                if config.platform_enabled:
                    action = await rt.store.decide(action_id, approved, version, user["username"], reason=reason)
                else:
                    action = await rt.store.decide(action_id, approved, version, user["username"])
            result = await rt.resume(action)
            if result["status"] == "reply_failed":
                raise public_error(
                    "BUSINESS_COMPLETED_REPLY_FAILED",
                    503,
                    "业务已执行，回复未完成；可按原动作编号重试回复。",
                )
            return result

    @app.post("/approvals/{action_id}/decision")
    async def decide(action_id: str, body: Decision, user=Depends(approver), rt=Depends(runtime)):
        return await decision_result(action_id, body.approved, body.version, user, rt, reason=body.reason)

    @app.post("/approvals/{action_id}/retry")
    async def retry(action_id: str, user=Depends(approver), rt=Depends(runtime)):
        return await decision_result(action_id, True, 1, user, rt, retry=True)

    @app.get("/cases")
    async def cases(user=Depends(identity), rt=Depends(runtime)):
        return await rt.store.list_cases(
            None if user["role"] == "approver" else user["customer_id"]
        )

    @app.get("/cases/{case_id}")
    async def case(case_id: str, user=Depends(identity), rt=Depends(runtime)):
        result = await rt.store.get_case(
            None if user["role"] == "approver" else user["customer_id"], case_id
        )
        if result is None:
            raise public_error("CASE_NOT_FOUND", 404)
        return result

    @app.post("/cases/{case_id}/status")
    async def status(case_id: str, body: CaseStatus, user=Depends(approver), rt=Depends(runtime)):
        return await rt.store.set_case_status(
            case_id, body.status, body.resolution, user["username"]
        )

    @app.get("/actions/{action_id}/events")
    async def events(action_id: str, user=Depends(identity), rt=Depends(runtime)):
        action = await rt.store.get_action(action_id)
        if (
            not action
            or user["role"] != "approver"
            and action["customer_id"] != user["customer_id"]
        ):
            raise public_error("ACTION_NOT_FOUND", 404)
        return await rt.store.get_events(action_id)

    if config.platform_enabled:
        from aftersales_delivery.platform_api import register_platform_routes
        register_platform_routes(app, config, runtime, identity, approver)
    return app


def __getattr__(name):
    if name == "app":
        return create_app()
    raise AttributeError(name)
