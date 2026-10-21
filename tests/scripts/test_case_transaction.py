import argparse
import importlib
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


@pytest_asyncio.fixture
async def exercise(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    module = importlib.import_module("learn_case_create")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def attach_database(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("ATTACH DATABASE ? AS aftersales_agent", (str(tmp_path / "cases.db"),))
        cursor.close()

    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("""
                    CREATE TABLE aftersales_agent.orders (
                        order_id TEXT PRIMARY KEY, customer_id TEXT,
                        paid_amount NUMERIC, status TEXT, created_at TEXT, delivered_at TEXT
                    )
                """)
            )
            await connection.execute(
                text("""
                    INSERT INTO aftersales_agent.orders VALUES
                        ('DEMO-001', 'learner-001', 199.00, 'shipped', '2026-10-16', NULL)
                """)
            )
            await connection.execute(
                text("""
                    CREATE TABLE aftersales_agent.cases (
                        case_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                        case_type TEXT NOT NULL, description TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'open', resolution TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (order_id) REFERENCES orders(order_id)
                    )
                """)
            )
            await connection.execute(
                text("""
                    INSERT INTO aftersales_agent.cases
                        (case_id, order_id, case_type, description)
                    VALUES ('CASE-CONTROL', 'DEMO-001', 'logistics_exception', 'Existing case')
                """)
            )
        yield module, engine
    finally:
        await engine.dispose()


def case_args(case_id: str, simulate_failure: bool) -> argparse.Namespace:
    return argparse.Namespace(
        case_id=case_id,
        order_id="DEMO-001",
        customer_id="learner-001",
        description="Check delivery information.",
        simulate_failure=simulate_failure,
    )


@pytest.mark.asyncio
async def test_normal_case_creation_commits_on_exit(exercise):
    module, engine = exercise
    await module.create_case(engine, case_args("CASE-NORMAL", False))
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT status FROM aftersales_agent.cases WHERE case_id = 'CASE-NORMAL'")
        )
        assert result.scalar_one() == "open"


@pytest.mark.asyncio
async def test_failure_after_insert_rolls_back_only_the_new_transaction(exercise, capsys):
    module, engine = exercise
    with pytest.raises(RuntimeError, match="Simulated failure"):
        await module.create_case(engine, case_args("CASE-ROLLBACK", True))
    assert "INSERT_FINISHED_BEFORE_SIMULATED_FAILURE" in capsys.readouterr().out
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT case_id FROM aftersales_agent.cases ORDER BY case_id")
        )
        assert result.scalars().all() == ["CASE-CONTROL"]
