"""Authenticated, stateless Streamable HTTP tools; execution reads trusted approvals."""

import json
from contextlib import asynccontextmanager

from anyio import CancelScope
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from aftersales_delivery.business import BusinessError, Store
from aftersales_delivery.config import get_config
from aftersales_delivery.security import AuthenticationError, decode_token


def create_app(config=None):
    config = config or get_config()
    engine = create_async_engine(config.db_url, pool_pre_ping=True)
    store = Store(engine)

    @asynccontextmanager
    async def lifespan(_):
        try:
            yield {"store": store}
        finally:
            with CancelScope(shield=True):
                await engine.dispose()

    server = FastMCP(
        "aftersales-delivery",
        stateless_http=True,
        json_response=True,
        lifespan=lifespan,
        log_level="WARNING",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "mcp:*", "testserver", "test"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://mcp:*"],
        ),
    )

    def claims(ctx: Context):
        request = ctx.request_context.request
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        return decode_token(token, config.signing_key, purpose="mcp")

    async def guarded(operation):
        try:
            return json.dumps(await operation, ensure_ascii=False, default=str)
        except BusinessError as error:
            raise ToolError(error.code) from None
        except Exception:
            raise ToolError("TOOL_UNAVAILABLE") from None

    @server.tool()
    async def list_orders(ctx: Context, limit: int = 20, offset: int = 0) -> str:
        """无需订单号，查询当前客户订单总数及分页列表；只展示可信身份所属订单。"""
        return await guarded(store.list_orders(claims(ctx)["customer_id"], limit, offset))

    @server.tool()
    async def get_order(order_id: str, ctx: Context) -> str:
        """查询当前客户的订单及实付、已退款金额。未找到不表示订单全局不存在。"""
        owner = claims(ctx)["customer_id"]
        try:
            order = await store.get_order(owner, order_id)
        except BusinessError as error:
            if error.code != "ORDER_NOT_FOUND":
                raise ToolError(error.code) from None
            order = None
        return json.dumps({"found": order is not None, "order": order}, ensure_ascii=False)

    @server.tool()
    async def get_shipments(order_id: str, ctx: Context) -> str:
        """查询当前客户指定订单全部模拟包裹，逐个判断异常；空列表不能判断丢件。"""
        return await guarded(_shipments(claims(ctx)["customer_id"], order_id))

    async def _shipments(owner, order_id):
        return {"shipments": await store.get_shipments(owner, order_id)}

    @server.tool()
    async def get_inventory(sku: str, ctx: Context) -> str:
        """查询模拟可用库存，最终执行时仍会再次校验并扣减。"""
        claims(ctx)
        return await guarded(store.get_inventory(sku))

    @server.tool()
    async def get_case(case_id: str, ctx: Context) -> str:
        """查询当前客户可见工单的真实处理状态；未找到时不泄露他人信息。"""
        return await guarded(store.get_case(claims(ctx)["customer_id"], case_id))

    @server.tool()
    async def execute_action(action_id: str, ctx: Context) -> str:
        """内部执行入口：仅接受绑定该动作的可信上下文，并读取数据库真实批准记录。"""
        identity = claims(ctx)
        if identity.get("action_id") != action_id:
            raise ToolError("ACTION_SCOPE_DENIED")
        action = await store.get_action(action_id)
        if not action or action["customer_id"] != identity["customer_id"]:
            raise ToolError("ACTION_SCOPE_DENIED")
        return await guarded(store.execute(action_id))

    app = server.streamable_http_app()

    async def authenticate(request, call_next):
        if request.url.path == "/health":
            return JSONResponse({"status": "ok", "transport": "streamable-http"})
        try:
            decode_token(
                request.headers.get("authorization", "").removeprefix("Bearer "),
                config.signing_key,
                purpose="mcp",
            )
        except AuthenticationError:
            return JSONResponse({"error": "UNAUTHORIZED"}, status_code=401)
        return await call_next(request)

    app.add_middleware(BaseHTTPMiddleware, dispatch=authenticate)
    return app


def __getattr__(name):
    if name == "app":
        return create_app()
    raise AttributeError(name)
