"""Read-only history adapter for pinned MySQL/SQLite checkpoint schemas.

Discover existing conversations without a migration or a second message store.
Only authenticated customer prefixes are queried; never scan/deserialise other
customers' checkpoints. MySQL's pinned saver cursor owns pool/lock management.
"""
import re

from aftersales_delivery.agent import graph_config


async def list_conversations(runtime, customer, limit=100):
    prefix = f"delivery:{customer}:"
    saver = runtime.saver
    if runtime.config.checkpoint_backend == "mysql":
        async with saver._cursor() as cursor:
            await cursor.execute(
                "SELECT thread_id, MAX(checkpoint_id) AS newest FROM checkpoints "
                "WHERE checkpoint_ns='' AND BINARY LEFT(thread_id, %s)=BINARY %s "
                "GROUP BY thread_id ORDER BY newest DESC LIMIT %s",
                (len(prefix), prefix, limit),
            )
            ids = [row["thread_id"] for row in await cursor.fetchall()]
    else:
        await saver.setup()
        async with saver.lock:
            async with saver.conn.execute(
                "SELECT thread_id, MAX(checkpoint_id) AS newest FROM checkpoints "
                "WHERE checkpoint_ns='' AND substr(thread_id, 1, ?)=? COLLATE BINARY "
                "GROUP BY thread_id ORDER BY newest DESC LIMIT ?",
                (len(prefix), prefix, limit),
            ) as cursor:
                ids = [row[0] for row in await cursor.fetchall()]
    result = []
    for identifier in ids:
        if not identifier.startswith(prefix):
            continue
        thread = identifier[len(prefix):]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", thread):
            continue
        snapshot = await runtime.graph(customer, thread).aget_state(graph_config(customer, thread))
        values = snapshot.values
        messages = values.get("messages", [])
        if not messages:
            continue
        title = next((m.content for m in messages if m.type == "human"
                      and isinstance(m.content, str) and m.content.strip()), thread)
        result.append({"thread_id": thread, "title": title.replace("\n", " ")[:64],
                       "status": values.get("status", "running"),
                       "updated_at": snapshot.created_at})
    return result
