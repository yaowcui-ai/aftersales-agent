"""Product REST endpoints sharing the existing authorization and approval engine."""
import hashlib
import json
import time
from typing import Literal

from fastapi import Depends
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import Field, field_validator

from aftersales_delivery.agent import graph_config
from aftersales_delivery.task_tracking import list_tasks


def register_platform_routes(app, config, runtime, identity, approver):
    # Import here to avoid a cycle with the compatibility FastAPI factory.
    from aftersales_delivery.api import Input, public_error

    class Application(Input):
        request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
        kind: Literal["logistics", "refund", "return_refund", "reship"]
        order_id: str = Field(min_length=1, max_length=80)
        description: str = Field(min_length=1, max_length=4000)
        amount: str | None = Field(default=None, max_length=30)
        quantity: int = Field(default=1, ge=1, le=1000)

        @field_validator("order_id", "description")
        @classmethod
        def nonblank(cls, value):
            if not value.strip():
                raise ValueError("Cannot be blank")
            return value.strip()

    @app.get("/orders")
    async def orders(limit: int = 20, offset: int = 0, user=Depends(identity), rt=Depends(runtime)):
        if user["role"] != "customer":
            raise public_error("CUSTOMER_REQUIRED", 403)
        return await rt.store.list_orders(user["customer_id"], limit, offset)

    @app.get("/orders/{order_id}")
    async def order(order_id: str, user=Depends(identity), rt=Depends(runtime)):
        if user["role"] != "customer":
            raise public_error("CUSTOMER_REQUIRED", 403)
        return await rt.store.get_order(user["customer_id"], order_id)

    @app.get("/orders/{order_id}/shipments")
    async def shipments(order_id: str, user=Depends(identity), rt=Depends(runtime)):
        if user["role"] != "customer":
            raise public_error("CUSTOMER_REQUIRED", 403)
        return {"shipments": await rt.store.get_shipments(user["customer_id"], order_id)}

    @app.get("/tasks")
    async def tasks(user=Depends(identity), rt=Depends(runtime)):
        return await list_tasks(rt.store.engine, None if user["role"] == "approver" else user["customer_id"])

    @app.get("/tickets")
    async def tickets(user=Depends(identity), rt=Depends(runtime)):
        rows = await rt.store.list_tickets(None if user["role"] == "approver" else user["customer_id"])
        return [await enrich_ticket(rt, row) for row in rows]

    async def enrich_ticket(rt, row):
        action = await rt.store.get_action(row["action_id"])
        return {**action, **row, "ticket_status": row["status"],
                "action_status": action["status"], "action": action}

    @app.get("/tickets/{ticket_id}")
    async def ticket(ticket_id: str, user=Depends(identity), rt=Depends(runtime)):
        row = await rt.store.get_ticket(None if user["role"] == "approver" else user["customer_id"], ticket_id)
        return await enrich_ticket(rt, row)

    @app.get("/requests/{request_id}")
    async def recover_request(request_id: str, user=Depends(identity), rt=Depends(runtime)):
        if user["role"] != "customer":
            raise public_error("CUSTOMER_REQUIRED", 403)
        # Only the authenticated customer's graph namespace is searched.
        thread = "form-" + hashlib.sha256(request_id.encode()).hexdigest()[:40]
        return await rt.state(user["customer_id"], thread)

    @app.post("/requests")
    async def application(body: Application, user=Depends(identity), rt=Depends(runtime)):
        if user["role"] != "customer":
            raise public_error("CUSTOMER_REQUIRED", 403)
        customer = user["customer_id"]
        thread = "form-" + hashlib.sha256(body.request_id.encode()).hexdigest()[:40]
        async with rt.lock(customer, thread):
            action = await rt.store.prepare(customer, thread, body.kind, body.order_id,
                                             body.description, body.amount, body.quantity)
            graph, options = rt.graph(customer, thread), graph_config(customer, thread)
            saved = await graph.aget_state(options)
            if not saved.values:
                await graph.aupdate_state(options, {
                    "messages": [HumanMessage(content="表单申请：" + json.dumps(body.model_dump(), ensure_ascii=False))],
                    "proposal": action, "status": "waiting_approval", "final_answer": None,
                    "turn_calls": 0, "metrics": {"started_at": time.time(), "source": "form"},
                }, as_node="tools")
                await graph.ainvoke(None, options, durability="sync")
            return await rt.state(customer, thread)

    @app.post("/actions/{action_id}/cancel")
    async def cancel(action_id: str, user=Depends(identity), rt=Depends(runtime)):
        if user["role"] != "customer":
            raise public_error("CUSTOMER_REQUIRED", 403)
        action = await rt.store.get_action(action_id)
        if action["customer_id"] != user["customer_id"]:
            raise public_error("ACTION_NOT_FOUND", 404)
        async with rt.lock(user["customer_id"], action["thread_id"]):
            action = await rt.store.cancel_action(user["customer_id"], action_id)
            answer = "申请已取消，未执行退款或补发。"
            graph, options = rt.graph(user["customer_id"], action["thread_id"]), graph_config(user["customer_id"], action["thread_id"])
            await graph.aupdate_state(options, {"proposal": action, "status": "cancelled",
                "final_answer": answer, "messages": [AIMessage(content=answer)]}, as_node="reply")
            return {"action": action, "task": await rt.state(user["customer_id"], action["thread_id"])}

    @app.post("/returns/{action_id}/receive")
    async def receive(action_id: str, user=Depends(approver), rt=Depends(runtime)):
        action = await rt.store.get_action(action_id)
        async with rt.lock(action["customer_id"], action["thread_id"]):
            if action["kind"] == "return_refund" and action["status"] == "completed":
                return await rt.resume(action)
            action = await rt.store.receive_return(action_id, user["username"])
            graph, options = rt.graph(action["customer_id"], action["thread_id"]), graph_config(action["customer_id"], action["thread_id"])
            await graph.aupdate_state(options, {"proposal": action, "status": "executed"}, as_node="execute")
            await graph.ainvoke(None, options, durability="sync")
            return await rt.state(action["customer_id"], action["thread_id"])

    @app.get("/admin/overview")
    async def overview(user=Depends(approver), rt=Depends(runtime)):
        data = await rt.store.overview()
        task_rows = await list_tasks(rt.store.engine)
        data["tasks"] = task_rows
        data["alerts"] = [*data.get("alerts", []),
            *[row for row in task_rows if row["status"] in {"failed", "execution_failed", "reply_failed", "human_required", "recovery_required"}]]
        data.setdefault("counts", {})["tasks"] = len(task_rows)
        data["counts"]["pending"] = data["counts"].get("waiting_approval", 0)
        return data
