import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from asyncmy.errors import OperationalError
from langgraph.checkpoint.mysql.asyncmy import AsyncMySaver
from pydantic import SecretStr

from core.settings import Settings

DATABASE = "aftersales_agent_checkpoints"
TABLES = {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
PASSWORD = "test-only-password:@/%#"


def backend(monkeypatch, **overrides):
    assert importlib.util.find_spec("memory.mysql") is not None, "MySQL backend is missing"
    module = importlib.import_module("memory.mysql")
    values = dict(
        MYSQL_HOST="127.0.0.1",
        MYSQL_PORT=3306,
        MYSQL_USER="existing-account",
        MYSQL_PASSWORD=SecretStr(PASSWORD),
        MYSQL_CHECKPOINT_DB=DATABASE,
    )
    values.update(overrides)
    monkeypatch.setattr(module, "settings", SimpleNamespace(**values))
    return module


class Cursor:
    def __init__(self, tables=TABLES, versions=(22, 0, 21)):
        self.tables = tables
        self.versions = versions
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, sql, parameters=None):
        self.statements.append((sql, parameters))

    async def fetchall(self):
        return [{"table_name": name} for name in self.tables]

    async def fetchone(self):
        count, minimum, maximum = self.versions
        return {"total": count, "min_version": minimum, "max_version": maximum}


class Connection:
    def __init__(self, cursor):
        self.test_cursor = cursor

    def cursor(self, *args):
        return self.test_cursor


class Pool:
    def __init__(self, cursor):
        self.connection = Connection(cursor)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def test_mysql_settings_preserve_password_and_default_target(monkeypatch):
    monkeypatch.setenv("USE_FAKE_MODEL", "true")
    config = Settings(
        _env_file=None,
        DEFAULT_MODEL=None,
        DATABASE_TYPE="mysql",
        MYSQL_USER="existing-account",
        MYSQL_PASSWORD=PASSWORD,
    )
    assert config.DATABASE_TYPE.value == "mysql"
    assert config.MYSQL_CHECKPOINT_DB == DATABASE
    assert config.MYSQL_HOST == "127.0.0.1"
    assert config.MYSQL_PORT == 3306
    assert isinstance(config.MYSQL_PASSWORD, SecretStr)
    assert config.MYSQL_PASSWORD.get_secret_value() == PASSWORD
    assert PASSWORD not in repr(config)


@pytest.mark.asyncio
async def test_factory_selects_mysql_and_keeps_long_term_store_in_memory(monkeypatch):
    module = backend(monkeypatch)
    from langgraph.store.memory import InMemoryStore

    import memory
    from core.settings import DatabaseType

    selected = object()
    monkeypatch.setattr(memory, "settings", SimpleNamespace(DATABASE_TYPE=DatabaseType.MYSQL))
    monkeypatch.setattr(memory, "get_mysql_saver", lambda: selected)
    monkeypatch.setattr(
        memory, "get_sqlite_saver", Mock(side_effect=AssertionError("SQLite was selected"))
    )
    assert memory.initialize_database() is selected
    async with memory.initialize_store() as store:
        assert isinstance(store, InMemoryStore)
    assert module is not None


@pytest.mark.asyncio
async def test_mysql_startup_only_reads_schema_and_closes_pool(monkeypatch):
    module = backend(monkeypatch)
    cursor = Cursor()
    pool = Pool(cursor)
    connect = Mock(return_value=pool)
    monkeypatch.setattr(module.asyncmy, "create_pool", connect)

    async with module.get_mysql_saver() as saver:
        assert isinstance(saver, AsyncMySaver)
        await saver.setup()
    assert pool.closed
    assert connect.call_args.kwargs["db"] == DATABASE
    assert connect.call_args.kwargs["password"] == PASSWORD
    assert connect.call_args.kwargs["charset"] == "utf8mb4"
    assert connect.call_args.kwargs["autocommit"] is True
    assert len(cursor.statements) == 2
    assert all(sql.lstrip().upper().startswith("SELECT ") for sql, _ in cursor.statements)
    assert cursor.statements[0][1] == (DATABASE,)
    assert f"`{DATABASE}`.`checkpoint_migrations`" in cursor.statements[1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tables", "versions"),
    [(TABLES - {"checkpoint_writes"}, (22, 0, 21)), (TABLES, (21, 0, 20))],
)
async def test_incomplete_schema_stops_startup_and_closes_pool(monkeypatch, tables, versions):
    module = backend(monkeypatch)
    pool = Pool(Cursor(tables, versions))
    monkeypatch.setattr(module.asyncmy, "create_pool", Mock(return_value=pool))
    with pytest.raises(ValueError, match="checkpoint"):
        async with module.get_mysql_saver() as saver:
            await saver.setup()
    assert pool.closed
    assert all(
        sql.lstrip().upper().startswith("SELECT ")
        for sql, _ in pool.connection.test_cursor.statements
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"MYSQL_USER": None},
        {"MYSQL_PASSWORD": None},
        {"MYSQL_CHECKPOINT_DB": "existing_project_database"},
    ],
)
async def test_invalid_config_is_rejected_before_connecting(monkeypatch, overrides):
    module = backend(monkeypatch, **overrides)
    connect = Mock(side_effect=AssertionError("Must not connect"))
    monkeypatch.setattr(module.asyncmy, "create_pool", connect)
    with pytest.raises(ValueError):
        async with module.get_mysql_saver():
            pytest.fail("Invalid settings were accepted")
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_driver_error_does_not_expose_password(monkeypatch):
    module = backend(monkeypatch)
    monkeypatch.setattr(
        module.asyncmy,
        "create_pool",
        Mock(side_effect=OperationalError(1045, "private driver details " + PASSWORD)),
    )
    with pytest.raises(RuntimeError, match="1045") as caught:
        async with module.get_mysql_saver():
            pytest.fail("Connection error was ignored")
    assert PASSWORD not in str(caught.value)
    assert caught.value.__suppress_context__
