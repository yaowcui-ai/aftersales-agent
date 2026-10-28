"""Executable B1/B2/B3 ablations with identical sandbox tools and policy gates.

ScriptedModel consumes form inputs, never expectations. It tests wiring only.
LiveModel makes raw HTTP requests with no implicit SDK retries; one shared ledger
reserves each request including reply/error attempts before network transmission.
"""

import copy
import json
import math
import uuid


PUBLIC_TOOLS = {"list_orders", "get_order", "get_shipments", "get_inventory", "get_case", "prepare_action"}


class ScriptedModel:
    def __init__(self):
        self.calls = 0

    def reset(self, case):
        self.steps = copy.deepcopy([s for s in case["input_steps"] if s["tool"] in PUBLIC_TOOLS])
        self.position = 0

    async def __call__(self, messages, tools):
        from langchain_core.messages import AIMessage
        self.calls += 1
        if self.position >= len(self.steps):
            return AIMessage(content="沙箱工具结果已记录。")
        step = self.steps[self.position]
        self.position += 1
        return AIMessage(content="", tool_calls=[{"id": str(uuid.uuid4()), "name": step["tool"], "args": copy.deepcopy(step["args"])}])


class LiveModel:
    def __init__(self, budget):
        from aftersales_delivery.config import project_values
        values = project_values()
        self.url = values.get("COMPATIBLE_BASE_URL") or values.get("DEEPSEEK_API_BASE") or "https://api.deepseek.com/v1"
        self.key = values.get("COMPATIBLE_API_KEY") or values.get("DEEPSEEK_API_KEY")
        self.model_name = values.get("COMPATIBLE_MODEL") or values.get("DEEPSEEK_MODEL") or "deepseek-chat"
        if not self.key:
            raise ValueError("No project-owned model credentials configured")
        self.budget = budget
        self.calls = 0
        self.usage = []

    async def __call__(self, messages, tools):
        import httpx
        from langchain_core.messages import AIMessage
        from langchain_core.utils.function_calling import convert_to_openai_tool
        payload_messages = []
        for message in messages:
            role = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}.get(message.type)
            item = {"role": role, "content": message.content}
            if message.type == "ai" and message.tool_calls:
                item["tool_calls"] = [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}} for c in message.tool_calls]
            if message.type == "tool":
                item["tool_call_id"] = message.tool_call_id
            payload_messages.append(item)
        payload = {"model": self.model_name, "messages": payload_messages, "temperature": 0,
                   "max_tokens": 800, "tools": [convert_to_openai_tool(t) for t in tools]}
        # Local heuristic avoids downloading tokenizer data: UTF-8 bytes / 2
        # plus completion cap. Provider tokenization may differ. Never report
        # this estimate as billed usage or claim an actual-token hard cap.
        estimate = math.ceil(len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) / 2) + 800
        self.budget.reserve(estimate)
        self.calls += 1
        usage_recorded = False
        try:
            async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
                response = await client.post(self.url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}"}, json=payload)
            if not response.is_success:
                # Do not persist response bodies, headers, URLs or credentials.
                raise RuntimeError(f"MODEL_HTTP_{response.status_code}")
            body = response.json()
            usage = body.get("usage")
            converted = {"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens"), "total_tokens": usage.get("total_tokens")} if usage else None
            self.budget.record(converted)
            self.usage.append(converted)
            usage_recorded = True
            result = body["choices"][0]["message"]
            calls = [{"id": c["id"], "name": c["function"]["name"], "args": json.loads(c["function"]["arguments"])} for c in result.get("tool_calls", [])]
            return AIMessage(content=result.get("content") or "", tool_calls=calls)
        finally:
            if not usage_recorded:
                self.budget.record(None)
                self.usage.append(None)


def model_eligible(case):
    """Single request plus external admin/fault controls; no scripted rephrasing score."""
    public = [s for s in case["input_steps"] if s["tool"] in PUBLIC_TOOLS]
    return len(public) == 1 and case["input_steps"][0]["tool"] in PUBLIC_TOOLS


async def run_model_baseline(case, sandbox, baseline, mode, model):
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from aftersales_delivery.agent import INSTRUCTIONS, TOOLS, build_graph, graph_config
    if model is None:
        raise ValueError("An explicit scripted or live model is required")
    if not model_eligible(case):
        raise ValueError("Model baseline requires a single public request; catalog has separate B0-only controls")
    if hasattr(model, "reset"):
        model.reset(case)
    start_calls = model.calls
    start_usage = len(model.usage) if hasattr(model, "usage") else 0
    # Same prompt/tools/temperature/budget and policy boundaries in all arms.
    messages = [HumanMessage(content="\n".join(case["user_turns"]))]

    async def dispatch(response):
        results = []
        for call in response.tool_calls:
            # Admin and test-control methods are never model-visible.
            if call["name"] not in PUBLIC_TOOLS:
                value = await sandbox.invoke("UNKNOWN_MODEL_TOOL", {"name": call["name"]})
            else:
                value = await sandbox.invoke(call["name"], call["args"])
            results.append(ToolMessage(content=json.dumps(value, ensure_ascii=False, default=str), tool_call_id=call["id"], name=call["name"]))
        return results

    graph, graph_resumed = None, False
    if baseline == "B1":
        # Direct one-shot tool selection: no iterative observation/replanning.
        response = await model([SystemMessage(content=INSTRUCTIONS), *messages], TOOLS)
        await dispatch(response)
    elif baseline == "B2":
        from langgraph.graph import END, START, MessagesState, StateGraph

        async def model_node(state):
            return {"messages": [await model([SystemMessage(content=INSTRUCTIONS), *state["messages"]], TOOLS)]}

        async def tool_node(state):
            return {"messages": await dispatch(state["messages"][-1])}

        graph = StateGraph(MessagesState)
        graph.add_node("model", model_node)
        graph.add_node("tools", tool_node)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", lambda s: "tools" if s["messages"][-1].tool_calls and model.calls - start_calls < 6 else END)
        graph.add_conditional_edges("tools", lambda s: END if sandbox.action else "model")
        await graph.compile().ainvoke({"messages": messages}, {"recursion_limit": 16})
    elif baseline == "B3":
        from langgraph.checkpoint.memory import InMemorySaver

        class TracedStore:
            async def prepare(self, customer, thread, **args):
                args["order_id"] = args.pop("order")
                result = await sandbox.invoke("prepare_action", args)
                if "error" in result:
                    from aftersales_delivery.business import BusinessError
                    raise BusinessError(result["error"])
                return result

            async def get_action(self, action_id):
                return await sandbox.store.get_action(action_id)

        async def read(name, args):
            result = await sandbox.invoke(name, args)
            if "error" in result:
                from aftersales_delivery.business import BusinessError
                raise BusinessError(result["error"])
            return result

        async def execute(action):
            result = await sandbox.invoke("execute", {})
            if "error" in result:
                from aftersales_delivery.business import BusinessError
                raise BusinessError(result["error"])
            return result

        async def reply(action):
            return AIMessage(content=f"沙箱结果已持久化，工单 {action['result']['case_id']}")

        graph = build_graph(TracedStore(), InMemorySaver(), "customer-a", "scenario-task", model, read, execute, reply, max_calls=6)
        await graph.ainvoke({"messages": messages, "turn_calls": 0}, graph_config("customer-a", "scenario-task"))
        # Product graph interrupt is real; the harness is the external reviewer.
    else:
        raise ValueError("Unknown baseline")
    # The external scenario actor supplies identical approvals/state mutations.
    # Missing/invalid proposals never receive a synthetic approval or repair.
    for step in case["input_steps"][1:]:
        if step["tool"] not in PUBLIC_TOOLS and sandbox.action is not None:
            if baseline == "B3" and step["tool"] == "execute" and not graph_resumed and sandbox.action["status"] in {"approved", "rejected"}:
                from langgraph.types import Command
                from aftersales_delivery.business import BusinessError
                try:
                    await graph.ainvoke(Command(resume={"review_recorded": True}), graph_config("customer-a", "scenario-task"))
                except BusinessError as exc:
                    # Tool trace already contains this actual policy rejection.
                    # A rejected business effect is not a second model failure.
                    if not sandbox.errors or sandbox.errors[-1] != exc.code:
                        raise
                graph_resumed = True
            else:
                await sandbox.invoke(step["tool"], step["args"])
    usage = model.usage[start_usage:] if hasattr(model, "usage") else []
    known = sum(u["total_tokens"] for u in usage if u and u.get("total_tokens") is not None)
    return {"http_calls": model.calls - start_calls if mode == "live" else 0,
            "scripted_calls": model.calls - start_calls if mode != "live" else 0,
            "model_name": getattr(model, "model_name", "ScriptedModel"),
            "total_tokens": None if mode == "live" and any(not u or u.get("total_tokens") is None for u in usage) else known,
            "reported_total_tokens": known, "usage": usage,
            "graph_resumed": graph_resumed,
            "graph_scope": "B3 product graph with checkpoint interrupt/resume; external reviewer and fault controls" if baseline == "B3" else baseline}, model.calls - start_calls
