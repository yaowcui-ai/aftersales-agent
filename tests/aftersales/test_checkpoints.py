import importlib
from unittest.mock import Mock

import pytest
from asyncmy.errors import OperationalError

DATABASE = "aftersales_agent_checkpoints"
TABLES = {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
PASSWORD = "PRIVATE_PASSWORD_WITH:@/%#"


def module():
    return importlib.import_module("aftersales.checkpoints")


class Cursor:
    def __init__(self, tables=TABLES):
        self.tables = tables
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
        return {"total": 22, "min_version": 0, "max_version": 21}


class Connection:
    def __init__(self, tables=TABLES):
        self.test_cursor = Cursor(tables)
        self.closed = False

    def cursor(self, *args):
        return self.test_cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True
        return False


def connection_options():
    return dict(host="127.0.0.1", port=3306, username="root", password=PASSWORD)


def test_checkpoint_config_separates_customers_and_prior_demo_threads():
    build = module().build_checkpoint_config
    first = build("learner-001", "task-001")
    assert first == build("learner-001", "task-001")
    assert first["configurable"]["thread_id"] == "aftersales:learner-001:task-001"
    assert first["recursion_limit"] == 8
    assert first != build("learner-002", "task-001")
    assert first != build("learner-001", "task-002")
    assert len(build("c" * 32, "t" * 64)["configurable"]["thread_id"]) <= 150


@pytest.mark.parametrize(
    ("customer_id", "thread_id"),
    [("", "task"), ("a:b", "c"), ("a", "b:c"), ("c" * 33, "t"), ("c", "t" * 65)],
)
def test_invalid_checkpoint_identifiers_are_rejected(customer_id, thread_id):
    with pytest.raises(ValueError):
        module().build_checkpoint_config(customer_id, thread_id)


@pytest.mark.asyncio
async def test_checkpoint_connection_reuses_read_only_schema_check_and_closes(monkeypatch):
    backend = module()
    connection = Connection()
    connect = Mock(return_value=connection)
    monkeypatch.setattr(backend.asyncmy, "connect", connect)
    async with backend.aftersales_checkpointer(**connection_options()) as saver:
        assert saver.conn is connection
        assert not connection.closed
    assert connection.closed
    assert connect.call_args.kwargs == dict(
        host="127.0.0.1",
        port=3306,
        user="root",
        password=PASSWORD,
        db=DATABASE,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=5,
    )
    statements = connection.test_cursor.statements
    assert len(statements) == 2
    assert all(sql.lstrip().upper().startswith("SELECT ") for sql, _ in statements)
    assert statements[0][1] == (DATABASE,)


@pytest.mark.asyncio
async def test_missing_schema_stops_before_running_graph_and_closes(monkeypatch):
    backend = module()
    connection = Connection(TABLES - {"checkpoint_writes"})
    monkeypatch.setattr(backend.asyncmy, "connect", Mock(return_value=connection))
    with pytest.raises(backend.AftersalesCheckpointError, match="schema"):
        async with backend.aftersales_checkpointer(**connection_options()):
            pytest.fail("Incomplete schema was accepted")
    assert connection.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("during_operation", [False, True])
async def test_driver_failures_hide_private_details(monkeypatch, during_operation):
    backend = module()
    connection = Connection()
    error = OperationalError(1045, PASSWORD)
    connect = Mock(return_value=connection) if during_operation else Mock(side_effect=error)
    monkeypatch.setattr(backend.asyncmy, "connect", connect)
    with pytest.raises(backend.AftersalesCheckpointError, match="1045") as caught:
        async with backend.aftersales_checkpointer(**connection_options()):
            raise error
    assert PASSWORD not in str(caught.value)
    assert caught.value.__suppress_context__
    if during_operation:
        assert connection.closed
