"""Caller-scoped MCP sessions and LangChain tool adaptation."""

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_order_server_parameters(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    customer_id: str,
    case_request_id: str | None = None,
) -> StdioServerParameters:
    if case_request_id is not None and (not case_request_id.strip() or len(case_request_id) > 64):
        raise ValueError("case_request_id must contain 1-64 characters and cannot be blank.")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "aftersales.mcp_server"],
        cwd=str(PROJECT_ROOT),
        env={
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
            "PYTHONIOENCODING": "utf-8",
            "AFTERSALES_MYSQL_HOST": host,
            "AFTERSALES_MYSQL_PORT": str(port),
            "AFTERSALES_MYSQL_USER": username,
            "AFTERSALES_MYSQL_PASSWORD": password,
            "AFTERSALES_CUSTOMER_ID": customer_id,
            "AFTERSALES_CASE_REQUEST_ID": case_request_id if case_request_id is not None else "",
        },
    )


@asynccontextmanager
async def order_mcp_session(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    customer_id: str,
    case_request_id: str | None = None,
) -> AsyncIterator[ClientSession]:
    parameters = build_order_server_parameters(
        host=host,
        port=port,
        username=username,
        password=password,
        customer_id=customer_id,
        case_request_id=case_request_id,
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=20)
        ) as session:
            await session.initialize()
            yield session


async def load_order_mcp_tools(session: ClientSession) -> list[BaseTool]:
    return await load_mcp_tools(session)


def report_mcp_error(error: Exception) -> None:
    pending = [error]
    seen = set()
    for _ in range(12):
        if not pending:
            break
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BaseExceptionGroup):
            pending.extend(reversed(current.exceptions))
        else:
            # Never print process environments or secret-bearing exception messages.
            print("MCP_CLIENT_FAILED:", type(current).__name__)
            if isinstance(current, NotImplementedError):
                print("MCP_EXERCISE_INCOMPLETE: complete the current MCP integration function.")
