"""Customer-scoped aftersales graph with persisted approval before case writes."""

import json
from typing import Literal

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from aftersales.approvals import (
    AftersalesApprovalError,
    CaseApprovalState,
    CaseExecutor,
    CaseResponder,
    prepare_case_proposal,
    reject_case_proposal,
    request_case_approval,
    route_after_case_approval,
)

INSTRUCTIONS = (
    "你是电商售后助手，目前可以查询当前客户的订单与包裹物流。"
    "查询订单或物流事实必须使用相应工具：订单信息使用 get_order，"
    "包裹进度及物流异常使用 get_shipments。"
    "从用户消息或历史中确认订单号，无法确认时请用户提供，不要编造。"
    "只依据工具返回的信息回答。found=false 只表示未找到当前客户可查询的订单，"
    "不能据此断言数据库中不存在该订单，也不能断言它一定属于其他客户。"
    "delivered_at 为 null 只表示没有记录送达时间，不能仅凭该字段确认实际配送情况。"
    "shipments 可能包含多个包裹，按包裹分别说明状态和异常，不能由一个推断其余包裹。"
    "shipments 为空只表示没有查到当前客户可见的物流记录，不能判断具体原因或丢件。"
    "回答模拟物流记录时保留模拟标识和事件时间。"
)

CASE_PROPOSAL_INSTRUCTIONS = (
    "本轮还可以调用 create_case 提出物流异常工单申请。"
    "用户明确要求登记工单时，用该工具提出申请，不要仅用自然语言承诺。"
    "该调用只代表申请，系统会暂停并展示人工确认，当前尚未创建工单。"
    "不要在工具调用前自行向用户索取这次审批，确认由系统节点处理。"
    "一次只提出一张工单，订单和描述必须来自用户或查询事实，不要编造。"
    "人工确认前不能宣称工单已创建，不能编造工单编号。"
)


def route_after_model(state: MessagesState) -> Literal["tools", "done"]:
    last_message = state["messages"][-1]
    if not isinstance(last_message, AIMessage):
        raise TypeError("The model node must produce an AIMessage.")
    return "tools" if last_message.tool_calls else "done"


def build_aftersales_graph(
    model_with_tools: Runnable,
    tools: list[BaseTool],
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    customer_id: str | None = None,
    case_executor: CaseExecutor | None = None,
    case_responder: CaseResponder | None = None,
):
    """Create a caller-owned graph; no global model, DB, or customer context."""
    tools_by_name = {tool.name: tool for tool in tools}
    case_enabled = "create_case" in tools_by_name
    if case_enabled and (
        checkpointer is None
        or not isinstance(customer_id, str)
        or not customer_id.strip()
        or len(customer_id) > 32
    ):
        raise AftersalesApprovalError("Case proposals require checkpoints and a trusted customer.")
    instructions = INSTRUCTIONS + CASE_PROPOSAL_INSTRUCTIONS if case_enabled else INSTRUCTIONS

    async def call_model(state: MessagesState) -> dict:
        response = await model_with_tools.ainvoke(
            [SystemMessage(content=instructions), *state["messages"]]
        )
        if not isinstance(response, AIMessage):
            raise TypeError("The model must return an AIMessage.")
        # MessagesState merges this new message into the existing history.
        return {"messages": [response]}

    async def run_tools(state: MessagesState) -> dict:
        request = state["messages"][-1]
        if not isinstance(request, AIMessage):
            raise TypeError("The tools node requires an AIMessage with ToolCalls.")
        results = []
        # Sequential execution keeps a caller's async MySQL connection single-use.
        for tool_call in request.tool_calls:
            if case_enabled and tool_call["name"] == "create_case":
                raise AftersalesApprovalError("Case writes must use the approved execution node.")
            selected_tool = tools_by_name.get(tool_call["name"])
            if selected_tool is None:
                results.append(
                    ToolMessage(
                        content=json.dumps({"error": "UNKNOWN_TOOL"}),
                        tool_call_id=tool_call["id"],
                        name=tool_call["name"],
                        status="error",
                    )
                )
                continue
            results.append(await selected_tool.ainvoke(tool_call))
        return {"messages": results}

    builder = StateGraph(CaseApprovalState if case_enabled else MessagesState)
    builder.add_node("model", call_model)
    builder.add_node("tools", run_tools)

    builder.add_edge(START, "model")
    if case_enabled:

        def route_case_or_read(state: CaseApprovalState):
            route = route_after_model(state)
            if route == "tools" and any(
                call["name"] == "create_case" for call in state["messages"][-1].tool_calls
            ):
                return "prepare_case"
            return route

        def save_case_proposal(state: CaseApprovalState):
            return prepare_case_proposal(state, customer_id)

        async def execute_case(state: CaseApprovalState):
            if state.get("approval_decision") is not True or case_executor is None:
                raise AftersalesApprovalError("Approved case execution is not configured.")
            return await case_executor(state)

        builder.add_node("prepare_case", save_case_proposal)
        builder.add_node("case_approval", request_case_approval)
        builder.add_node("reject_case", reject_case_proposal)
        builder.add_node("execute_case", execute_case)
        builder.add_conditional_edges(
            "model",
            route_case_or_read,
            {"tools": "tools", "done": END, "prepare_case": "prepare_case"},
        )
        builder.add_edge("prepare_case", "case_approval")
        builder.add_conditional_edges(
            "case_approval",
            route_after_case_approval,
            {"reject_case": "reject_case", "execute_case": "execute_case"},
        )
        builder.add_edge("reject_case", END)
        if case_responder is not None:
            builder.add_node("case_reply", case_responder)
            builder.add_edge("execute_case", "case_reply")
            builder.add_edge("case_reply", END)
        else:
            builder.add_edge("execute_case", END)
    else:
        builder.add_conditional_edges(
            "model",
            route_after_model,
            {"tools": "tools", "done": END},
        )
    builder.add_edge("tools", "model")

    return builder.compile(checkpointer=checkpointer)
