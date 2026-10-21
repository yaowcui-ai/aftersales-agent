import importlib
import json
from uuid import UUID

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from langsmith import tracing_context


def module():
    assert importlib.util.find_spec("aftersales.approvals") is not None, (
        "Approval module is missing"
    )
    return importlib.import_module("aftersales.approvals")


def proposal(**changes):
    args = {"order_id": "DEMO-001", "description": " 物流异常：收件信息待核实。 "}
    args.update(changes)
    return AIMessage(
        content="", tool_calls=[{"name": "create_case", "args": args, "id": "call-case-001"}]
    )


def test_pending_proposal_freezes_context_and_reuses_its_saved_request_id():
    backend = module()
    message = proposal()
    first = backend.prepare_case_proposal({"messages": [message]}, "learner-001")
    pending = first["pending_case"]
    assert UUID(hex=pending["request_id"]).hex == pending["request_id"]
    assert pending["customer_id"] == "learner-001"
    assert pending["tool_call_id"] == "call-case-001"
    assert pending["description"] == message.tool_calls[0]["args"]["description"]
    assert pending["status"] == "waiting_approval"
    second = backend.prepare_case_proposal({"messages": [message], **first}, "learner-001")
    assert second == first
    payload = backend.case_approval_payload({"messages": [message], **first})
    assert payload["action"] == "create_case"
    assert payload["request_id"] == pending["request_id"]
    assert payload["order_id"] == "DEMO-001"
    assert payload["description"] == pending["description"]


@pytest.mark.parametrize(
    "args", [{"description": " "}, {"order_id": "x" * 33}, {"customer_id": "other"}]
)
def test_invalid_or_untrusted_tool_arguments_are_rejected(args):
    backend = module()
    with pytest.raises(backend.AftersalesApprovalError):
        backend.prepare_case_proposal({"messages": [proposal(**args)]}, "learner-001")


def test_multiple_write_proposals_or_changed_pending_application_are_rejected():
    backend = module()
    first_message = proposal()
    with pytest.raises(backend.AftersalesApprovalError):
        backend.prepare_case_proposal(
            {"messages": [AIMessage(content="", tool_calls=first_message.tool_calls * 2)]},
            "learner-001",
        )
    pending = backend.prepare_case_proposal({"messages": [first_message]}, "learner-001")
    with pytest.raises(backend.AftersalesApprovalError):
        backend.prepare_case_proposal(
            {"messages": [proposal(description="changed")], **pending}, "learner-001"
        )


@pytest.mark.parametrize("value", [None, "false", 1])
def test_decision_does_not_accept_truthy_non_boolean_values(value):
    backend = module()
    with pytest.raises(backend.AftersalesApprovalError):
        backend.approval_decision_update(value)


@pytest.mark.parametrize("value", [True, False])
def test_boolean_decision_update(value):
    assert module().approval_decision_update(value) == {"approval_decision": value}


@pytest.mark.asyncio
async def test_proposal_tool_has_only_business_arguments_and_cannot_write_directly():
    backend = module()
    tool = backend.build_case_proposal_tool()
    assert tool.name == "create_case"
    assert set(tool.args) == {"order_id", "description"}
    with pytest.raises(backend.AftersalesApprovalError):
        with tracing_context(enabled=False):
            await tool.ainvoke({"order_id": "DEMO-001", "description": "Issue"})


def test_write_proposal_graph_requires_persistence_and_trusted_customer():
    backend = module()
    graph = importlib.import_module("aftersales.graph")
    model = RunnableLambda(lambda _: proposal())
    tool = backend.build_case_proposal_tool()
    with pytest.raises(backend.AftersalesApprovalError):
        graph.build_aftersales_graph(model, [tool], customer_id="learner-001")
    with pytest.raises(backend.AftersalesApprovalError):
        graph.build_aftersales_graph(model, [tool], checkpointer=InMemorySaver())


@pytest.mark.asyncio
async def test_graph_routes_to_saved_proposal_without_executing_a_write():
    backend = module()
    graph_module = importlib.import_module("aftersales.graph")
    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "approval-routing-test"}}
    graph = graph_module.build_aftersales_graph(
        RunnableLambda(lambda _: proposal()),
        [backend.build_case_proposal_tool()],
        customer_id="learner-001",
        checkpointer=saver,
    )
    with tracing_context(enabled=False):
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="create case")]}, config=config
        )
    assert result["pending_case"]["status"] == "waiting_approval"
    assert result["approval_decision"] is None
    assert result["__interrupt__"]
    assert [message.type for message in result["messages"]] == ["human", "ai"]
    saved = await graph.aget_state(config)
    assert saved.values["pending_case"] == result["pending_case"]


@pytest.mark.asyncio
async def test_read_only_graph_keeps_unknown_tool_result_for_an_unregistered_case_tool():
    graph_module = importlib.import_module("aftersales.graph")

    async def respond(messages):
        if isinstance(messages[-1], HumanMessage):
            return proposal()
        assert isinstance(messages[-1], ToolMessage)
        assert json.loads(messages[-1].text) == {"error": "UNKNOWN_TOOL"}
        return AIMessage(content="Unknown tool was not executed.")

    graph = graph_module.build_aftersales_graph(RunnableLambda(respond), [])
    with tracing_context(enabled=False):
        result = await graph.ainvoke({"messages": [HumanMessage(content="unknown tool")]})
    assert result["messages"][-2].status == "error"
