"""Customer-scoped read-only Tools for the aftersales Agent."""

import json

from langchain_core.tools import BaseTool, tool
from sqlalchemy.ext.asyncio import AsyncConnection

from business.orders import get_order
from business.shipments import get_shipments


def build_order_tool(connection: AsyncConnection, customer_id: str) -> BaseTool:
    """Bind one caller's connection and identity; do not share across customers."""

    @tool("get_order")
    async def order_tool(order_id: str) -> str:
        """查询当前客户有权查看的订单信息。

        found=false 表示未找到当前客户可查询的订单，order 为 null。
        可能是订单不存在，也可能是不属于当前客户；结果不区分这两种情况。
        不能据此断言订单在数据库中不存在，或断言订单一定属于其他客户。
        """
        order = await get_order(connection, order_id, customer_id)
        return json.dumps(
            {"found": order is not None, "order": order}, ensure_ascii=False, default=str
        )

    return order_tool


def build_shipment_tool(connection: AsyncConnection, customer_id: str) -> BaseTool:
    """Bind the same trusted customer scope to the already learned shipment query."""

    @tool("get_shipments")
    async def shipment_tool(order_id: str) -> str:
        """查询当前客户指定订单的全部包裹物流，返回 shipments 列表。

        每个包裹独立提供状态、最近事件和事件时间，需分别查看是否存在异常。
        空列表只表示没有查到当前客户可见的物流记录，可能尚无物流、订单不存在
        或不属于当前客户，不能区分具体原因，也不能据此判断丢件。
        """
        shipments = await get_shipments(connection, order_id, customer_id)
        return json.dumps({"shipments": shipments}, ensure_ascii=False, default=str)

    return shipment_tool
