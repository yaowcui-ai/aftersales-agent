"""Initialize only the isolated delivery database and project-owned credentials."""

import argparse
import asyncio
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from aftersales_delivery.config import DATABASE, get_config  # noqa: E402
from aftersales_delivery.db import initialize  # noqa: E402
from aftersales_delivery.security import hash_password  # noqa: E402


def configure():
    path = ROOT / ".env.delivery"
    if path.exists():
        print("Existing delivery credentials retained.")
        return
    passwords = {name: secrets.token_urlsafe(15) for name in ("customer1", "customer2", "reviewer")}
    accounts = {}
    for name, role, owner in [
        ("customer1", "customer", "learner-001"),
        ("customer2", "customer", "learner-002"),
        ("reviewer", "approver", ""),
    ]:
        accounts[name] = {
            "role": role,
            "customer_id": owner,
            "password_hash": hash_password(passwords[name]),
        }
    content = (
        f"DELIVERY_SIGNING_KEY='{secrets.token_urlsafe(48)}'\n"
        f"DELIVERY_ACCOUNTS_JSON='{json.dumps(accounts, separators=(',', ':'))}'\n"
        f"DELIVERY_MYSQL_PASSWORD='{secrets.token_urlsafe(32)}'\n"
        "DELIVERY_CHECKPOINT_BACKEND=mysql\n"
    )
    with path.open("x", encoding="utf-8") as output:
        output.write(content)
    credentials = ROOT / "output/delivery/credentials.txt"
    credentials.parent.mkdir(parents=True, exist_ok=True)
    with credentials.open("x", encoding="utf-8") as output:
        json.dump(passwords, output, ensure_ascii=False, indent=2)
    print(f"Credentials saved locally: {credentials}")


async def setup():
    config = get_config()
    url = make_url(config.db_url)
    if url.get_backend_name() == "sqlite":
        raise ValueError("Deployment setup requires the isolated MySQL database.")
    if url.database != DATABASE:
        raise ValueError("Unexpected delivery database name.")
    admin = create_async_engine(url.set(database=""))
    try:
        async with admin.begin() as connection:
            result = await connection.execute(
                text("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME=:name"),
                {"name": DATABASE},
            )
            exists = result.first() is not None
            if not exists:
                await connection.execute(
                    text(
                        "CREATE DATABASE `aftersales_delivery` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
                    )
                )
    finally:
        await admin.dispose()
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            table_names = set(
                (
                    await connection.execute(
                        text(
                            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=:name"
                        ),
                        {"name": DATABASE},
                    )
                ).scalars()
            )
            if table_names and "delivery_metadata" not in table_names:
                raise ValueError(
                    "Database name occupied by unrecognized data; initialization refused."
                )
            if "delivery_metadata" in table_names:
                marker = await connection.execute(
                    text("SELECT app FROM delivery_metadata WHERE id=1")
                )
                if marker.scalar() != "aftersales_delivery_v1":
                    raise ValueError("Unrecognized delivery schema marker.")
            else:
                await connection.execute(
                    text(
                        "CREATE TABLE delivery_metadata (id INTEGER PRIMARY KEY, app VARCHAR(80) NOT NULL)"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO delivery_metadata (id,app) VALUES (1,'aftersales_delivery_v1')"
                    )
                )
        await initialize(engine)
        async with engine.begin() as connection:
            columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                            "WHERE TABLE_SCHEMA=:name AND TABLE_NAME='events'"
                        ),
                        {"name": DATABASE},
                    )
                ).scalars()
            )
            if "sequence" not in columns:
                await connection.execute(
                    text(
                        "ALTER TABLE events ADD COLUMN `sequence` BIGINT NOT NULL AUTO_INCREMENT, "
                        "DROP PRIMARY KEY, ADD PRIMARY KEY (`sequence`), "
                        "ADD UNIQUE KEY uq_events_event_id (event_id)"
                    )
                )
    finally:
        await engine.dispose()

    import asyncmy
    from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver

    connection = await asyncmy.connect(
        host=url.host,
        port=url.port or 3306,
        user=url.username,
        password=url.password,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
    )
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME=%s",
                ("aftersales_agent_checkpoints",),
            )
            exists = await cursor.fetchone() is not None
            if not exists:
                await cursor.execute(
                    "CREATE DATABASE `aftersales_agent_checkpoints` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
                )
            await cursor.execute(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=%s",
                ("aftersales_agent_checkpoints",),
            )
            count = (await cursor.fetchone())[0]
            await cursor.execute("USE `aftersales_agent_checkpoints`")
        if count == 0:
            await AsyncMySaver(connection).setup()
        else:
            from memory.mysql import MySQLSaver

            await MySQLSaver(connection).setup()
        # Only the newly isolated Docker instance may need its initial defaults
        # normalized. Never alter the pre-existing local checkpoint database.
        if os.environ.get("DELIVERY_CONTAINER") == "1" and url.host == "mysql":
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "ALTER DATABASE `aftersales_agent_checkpoints` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
                )
                for table in (
                    "checkpoint_migrations",
                    "checkpoints",
                    "checkpoint_blobs",
                    "checkpoint_writes",
                ):
                    await cursor.execute(
                        "SELECT TABLE_COLLATION FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                        ("aftersales_agent_checkpoints", table),
                    )
                    row = await cursor.fetchone()
                    if row and row[0] != "utf8mb4_bin":
                        await cursor.execute(
                            f"ALTER TABLE `aftersales_agent_checkpoints`.`{table}` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
                        )
    finally:
        connection.close()
    print("DELIVERY_SCHEMA_READY: isolated business database; MySQL checkpoints verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--configure-only", action="store_true")
    args = parser.parse_args()
    try:
        if not args.schema_only:
            configure()
        if not args.configure_only:
            asyncio.run(setup())
    except Exception as error:
        print(
            f"DELIVERY_SETUP_FAILED: {type(error).__name__}; see project setup instructions.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
