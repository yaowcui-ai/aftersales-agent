"""Durable navigation index; LangGraph checkpoints remain the execution authority."""
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


async def save_task(engine, customer, thread, status, *, final_answer=None, error_code=None,
                    last_message=None, action_id=None):
    from aftersales_delivery.db import task_index

    now = datetime.now(UTC).isoformat()
    key = sha256((customer + "\0" + thread).encode()).hexdigest()
    values = dict(task_id=key, customer_id=customer, thread_id=thread, status=status,
                  action_id=action_id, error_code=error_code, final_answer=final_answer,
                  last_message=last_message, created_at=now, updated_at=now)
    async with engine.begin() as conn:
        statement = (mysql_insert if conn.dialect.name == "mysql" else sqlite_insert)(task_index).values(**values)
        updates = {k: v for k, v in values.items() if k not in {"task_id", "customer_id", "thread_id", "created_at"}}
        if last_message is None:
            updates.pop("last_message")
        if conn.dialect.name == "mysql":
            statement = statement.on_duplicate_key_update(**updates)
        else:
            statement = statement.on_conflict_do_update(index_elements=["task_id"], set_=updates)
        await conn.execute(statement)


async def list_tasks(engine, customer=None):
    from aftersales_delivery.db import task_index

    async with engine.connect() as conn:
        query = select(task_index).order_by(task_index.c.updated_at.desc()).limit(500)
        if customer is not None:
            query = query.where(task_index.c.customer_id == customer)
        result = [dict(row) for row in (await conn.execute(query)).mappings()]
        return [row for row in result if customer is None or row["customer_id"] == customer]
