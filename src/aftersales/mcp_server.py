"""Local scoped MCP tools; stdout is reserved for protocol messages."""

import json
import os
import sys
from contextlib import asynccontextmanager

from anyio import CancelScope
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from business.cases import (
    CaseRequestConflictError,
    OrderUnavailableError,
    create_case_with_request,
    get_case_request,
    validate_case_input,
)
from business.orders import get_order
from business.shipments import get_shipments

BUSINESS_DATABASE = "aftersales_agent"


async def query_order_for_mcp(engine: AsyncEngine, order_id: str, customer_id: str) -> str:
    try:
        async with engine.connect() as connection:
            order = await get_order(connection, order_id, customer_id)
    except (SQLAlchemyError, OSError, TimeoutError) as error:
        original = getattr(error, "orig", error)
        code = original.args[0] if original.args and isinstance(original.args[0], int) else None
        raise ToolError(
            f"ORDER_QUERY_FAILED: {type(original).__name__}; error code: {code}"
        ) from None
    return json.dumps({"found": order is not None, "order": order}, ensure_ascii=False, default=str)


async def query_shipments_for_mcp(engine: AsyncEngine, order_id: str, customer_id: str) -> str:
    try:
        async with engine.connect() as connection:
            shipments = await get_shipments(connection, order_id, customer_id)
    except (SQLAlchemyError, OSError, TimeoutError) as error:
        original = getattr(error, "orig", error)
        code = original.args[0] if original.args and isinstance(original.args[0], int) else None
        raise ToolError(
            f"SHIPMENT_QUERY_FAILED: {type(original).__name__}; error code: {code}"
        ) from None
    return json.dumps({"shipments": shipments}, ensure_ascii=False, default=str)


async def create_case_for_mcp(
    engine: AsyncEngine,
    customer_id: str,
    request_id: str,
    order_id: str,
    description: str,
) -> str:
    try:
        validate_case_input(
            customer_id=customer_id,
            request_id=request_id,
            order_id=order_id,
            description=description,
        )
    except ValueError:
        raise ToolError("CASE_INPUT_INVALID") from None
    try:
        case_id = await create_case_with_request(
            engine,
            customer_id=customer_id,
            request_id=request_id,
            order_id=order_id,
            description=description,
        )
        async with engine.connect() as connection:
            record = await get_case_request(connection, customer_id, request_id)
        if record is None or record["case"]["case_id"] != case_id:
            raise ToolError("CASE_READ_BACK_FAILED")
    except OrderUnavailableError:
        raise ToolError("ORDER_NOT_FOUND") from None
    except CaseRequestConflictError:
        raise ToolError("CASE_REQUEST_CONFLICT") from None
    except ValueError:
        raise ToolError("CASE_DATA_INVALID") from None
    except (SQLAlchemyError, OSError, TimeoutError) as error:
        original = getattr(error, "orig", error)
        arguments = getattr(original, "args", ())
        code = arguments[0] if arguments and isinstance(arguments[0], int) else None
        raise ToolError(
            f"CASE_OPERATION_FAILED: {type(original).__name__}; error code: {code}"
        ) from None
    return json.dumps({"case_ready": True, **record}, ensure_ascii=False, default=str)


def build_mcp_server(
    engine: AsyncEngine, customer_id: str, *, case_request_id: str | None = None
) -> FastMCP:
    if case_request_id is not None and (not case_request_id.strip() or len(case_request_id) > 64):
        raise ValueError("case_request_id must contain 1-64 characters and cannot be blank.")

    @asynccontextmanager
    async def lifespan(_server: FastMCP):
        try:
            yield {}
        finally:
            # MCP cancels its server task on disconnect; finish releasing connections.
            with CancelScope(shield=True):
                await engine.dispose()

    server = FastMCP("aftersales-orders", lifespan=lifespan, log_level="WARNING")

    @server.tool(name="get_order", structured_output=False)
    async def get_order_mcp(order_id: str) -> str:
        """查询当前客户订单；未找到表示不存在或不属于当前客户，无法区分。"""
        return await query_order_for_mcp(engine, order_id, customer_id)

    @server.tool(name="get_shipments", structured_output=False)
    async def get_shipments_mcp(order_id: str) -> str:
        """查询当前客户指定订单的全部包裹物流，返回 shipments 列表。

        每个包裹独立提供状态、最近事件和事件时间，需分别查看是否存在异常。
        空列表只表示没有查到当前客户可见的物流记录，可能尚无物流、订单不存在
        或不属于当前客户，不能区分具体原因，也不能据此判断丢件。
        """
        return await query_shipments_for_mcp(engine, order_id, customer_id)

    if case_request_id is not None:

        @server.tool(name="create_case", structured_output=False)
        async def create_case_mcp(order_id: str, description: str) -> str:
            """为当前客户创建物流异常工单，相同申请内容重试时复用原工单。

            description 表达客户的问题；订单必须属于当前客户。
            客户身份与稳定申请编号由调用方固定，不能通过工具参数指定。
            内容变化会报申请冲突。返回 case_ready 只表示工单已登记，尚未解决问题。
            """
            return await create_case_for_mcp(
                engine, customer_id, case_request_id, order_id, description
            )

    return server


def engine_from_environment() -> tuple[AsyncEngine, str]:
    required = ["AFTERSALES_MYSQL_USER", "AFTERSALES_MYSQL_PASSWORD", "AFTERSALES_CUSTOMER_ID"]
    missing = [name for name in required if name not in os.environ]
    if missing:
        raise ValueError("Missing MCP startup parameters: " + ", ".join(missing))
    customer_id = os.environ["AFTERSALES_CUSTOMER_ID"]
    if not customer_id.strip() or not os.environ["AFTERSALES_MYSQL_USER"].strip():
        raise ValueError("MCP customer and database login must not be blank.")
    try:
        port = int(os.environ.get("AFTERSALES_MYSQL_PORT", "3306"))
    except ValueError:
        raise ValueError("Invalid MCP database port.") from None
    url = URL.create(
        "mysql+asyncmy",
        username=os.environ["AFTERSALES_MYSQL_USER"],
        password=os.environ["AFTERSALES_MYSQL_PASSWORD"],
        host=os.environ.get("AFTERSALES_MYSQL_HOST", "127.0.0.1"),
        port=port,
        database=BUSINESS_DATABASE,
        query={"charset": "utf8mb4"},
    )
    return (
        create_async_engine(
            url,
            poolclass=NullPool,
            connect_args={"connect_timeout": 5},
            hide_parameters=True,
        ),
        customer_id,
    )


def main() -> int:
    try:
        engine, customer_id = engine_from_environment()
        server = build_mcp_server(
            engine,
            customer_id,
            case_request_id=os.environ.get("AFTERSALES_CASE_REQUEST_ID") or None,
        )
        server.run(transport="stdio")
    except NotImplementedError as error:
        print(f"MCP_EXERCISE_INCOMPLETE: {error}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"MCP_CONFIGURATION_ERROR: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
