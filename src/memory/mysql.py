import logging
from contextlib import asynccontextmanager

import asyncmy
from asyncmy.errors import MySQLError
from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver

from core.settings import settings

logger = logging.getLogger(__name__)
CHECKPOINT_DATABASE = "aftersales_agent_checkpoints"
REQUIRED_TABLES = {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}


def validate_mysql_config() -> None:
    missing = [name for name in ("MYSQL_USER", "MYSQL_PASSWORD") if getattr(settings, name) is None]
    if missing:
        raise ValueError("Missing MySQL checkpoint configuration: " + ", ".join(missing))
    if settings.MYSQL_CHECKPOINT_DB != CHECKPOINT_DATABASE:
        raise ValueError(f"MySQL checkpoints must use the project database {CHECKPOINT_DATABASE}.")


class MySQLSaver(AsyncMySaver):
    async def setup(self) -> None:
        # Workbench owns schema changes; service startup must not run automatic migrations.
        async with self._cursor() as cursor:
            await cursor.execute(
                "SELECT TABLE_NAME AS table_name FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA = %s",
                (CHECKPOINT_DATABASE,),
            )
            tables = {row["table_name"] for row in await cursor.fetchall()}
            missing = REQUIRED_TABLES - tables
            if missing:
                raise ValueError("Missing MySQL checkpoint tables: " + ", ".join(sorted(missing)))

            await cursor.execute(
                "SELECT COUNT(*) AS total, MIN(v) AS min_version, MAX(v) AS max_version "
                "FROM `aftersales_agent_checkpoints`.`checkpoint_migrations`"
            )
            row = await cursor.fetchone()
            if (row["total"], row["min_version"], row["max_version"]) != (22, 0, 21):
                raise ValueError(
                    "MySQL checkpoint schema must contain migration versions 0 through 21."
                )
        logger.info("MySQL checkpoint schema verified: %s", CHECKPOINT_DATABASE)


@asynccontextmanager
async def get_mysql_saver():
    validate_mysql_config()
    assert settings.MYSQL_PASSWORD is not None
    try:
        async with asyncmy.create_pool(
            host=settings.MYSQL_HOST,
            port=settings.MYSQL_PORT,
            user=settings.MYSQL_USER,
            password=settings.MYSQL_PASSWORD.get_secret_value(),
            db=CHECKPOINT_DATABASE,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=5,
            minsize=1,
            maxsize=5,
            pool_recycle=1800,
        ) as pool:
            yield MySQLSaver(pool)
    except MySQLError as error:
        code = error.args[0] if error.args and isinstance(error.args[0], int) else None
        raise RuntimeError(
            f"MySQL checkpoint database operation failed; error code: {code}"
        ) from None
