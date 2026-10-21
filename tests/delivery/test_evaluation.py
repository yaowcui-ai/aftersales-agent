"""Evaluation conclusions must come from evidence, not a successful HTTP status."""

import importlib.util
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def runner():
    path = ROOT / "scripts" / "evaluate_delivery.py"
    assert path.is_file(), "Delivery evaluation runner has not been implemented"
    spec = importlib.util.spec_from_file_location("delivery_evaluation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_dataset_has_48_unique_cases_and_32_16_split():
    module = runner()
    dataset = module.load_cases()
    assert len(dataset) == 48
    assert Counter(case["split"] for case in dataset) == {"dev": 32, "heldout": 16}
    assert len({case["id"] for case in dataset}) == 48
    assert {case["kind"] for case in dataset} == {
        "logistics",
        "refund",
        "reship",
        "query",
        "missing",
        "unauthorized",
        "refusal",
    }


def test_dataset_validator_rejects_duplicate_and_missing_expectations():
    module = runner()
    dataset = module.load_cases()
    dataset[1] = dict(dataset[0])
    with pytest.raises(ValueError):
        module.validate_cases(dataset)
    dataset = module.load_cases()
    dataset[0].pop("expected")
    with pytest.raises(ValueError):
        module.validate_cases(dataset)


def test_success_requires_semantics_and_business_effects_not_status_alone():
    module = runner()
    expected = {"outcome": "completed", "action_kind": "refund", "amount": "10.00", "quantity": 1}
    task = {"status": "completed", "proposal": {"kind": "refund", "status": "completed"}}
    passed, reasons = module.assess_case({"kind": "refund", "expected": expected}, task, {})
    assert not passed
    assert reasons


def test_aggregation_counts_every_attempt_and_keeps_unknown_tokens_unknown():
    module = runner()
    result = module.summarize(
        [
            {
                "case_id": "a",
                "split": "dev",
                "kind": "refund",
                "passed": True,
                "elapsed_seconds": 2.0,
                "metrics": {"input_tokens": 10, "output_tokens": 5, "model_calls": 2},
            },
            {
                "case_id": "a",
                "split": "dev",
                "kind": "refund",
                "passed": False,
                "elapsed_seconds": 4.0,
                "metrics": {},
            },
        ]
    )
    assert result["attempts"] == 2
    assert result["passed"] == 1
    assert result["success_rate"] == 0.5
    assert result["mean_elapsed_seconds"] == 3.0
    assert result["input_tokens"]["observed_total"] == 10
    assert result["input_tokens"]["missing_attempts"] == 1
    assert result["by_split"]["dev"]["success_rate"] == 0.5


@pytest.mark.asyncio
async def test_offline_fault_benchmark_is_real_engineering_test(tmp_path):
    module = runner()
    result = await module.fault_benchmark(tmp_path)
    assert result["mode"] == "engineering_fault_benchmark"
    assert result["llm_used"] is False
    assert result["baseline_llm_executed"] is False
    scenarios = {row["scenario"]: row for row in result["scenarios"]}
    assert {
        "response_lost",
        "rejected",
        "concurrent",
        "restart",
        "failed_reply",
    } <= scenarios.keys()
    assert scenarios["response_lost"]["naive"]["refund_count"] == 2
    assert scenarios["response_lost"]["guarded"]["refund_count"] == 1
    assert scenarios["rejected"]["guarded"]["refund_count"] == 0
    assert scenarios["restart"]["guarded"]["refund_count"] == 1


@pytest.mark.asyncio
async def test_fixture_clones_are_append_only_and_independent(tmp_path):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine

    from aftersales_delivery.db import initialize, inventory, orders

    module = runner()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fixtures.sqlite'}")
    try:
        await initialize(engine)
        case = next(case for case in module.load_cases() if case["kind"] == "refund")
        a = await module.clone_fixture(engine, case, "run-a", 1)
        b = await module.clone_fixture(engine, case, "run-a", 2)
        assert a["order_id"] != b["order_id"]
        assert a["sku"] != b["sku"]
        assert len(a["thread_id"]) <= 64
        async with engine.connect() as conn:
            seed = (
                (await conn.execute(select(orders).where(orders.c.order_id == "DEMO-004")))
                .mappings()
                .one()
            )
            assert seed["refunded_cents"] == 0
            for fixture in [a, b]:
                stock = (
                    await conn.execute(
                        select(inventory.c.available).where(inventory.c.sku == fixture["sku"])
                    )
                ).scalar_one()
                assert stock == 3
    finally:
        await engine.dispose()


def test_query_requires_matching_read_tool_trace():
    module = runner()
    case = next(case for case in module.load_cases() if case["kind"] == "query")
    evidence = {
        "actions": [],
        "cases": [],
        "refunds": [],
        "reships": [],
        "available": 3,
        "order": {"order_id": "EV-1", "refunded_cents": 0, "reshipped_quantity": 0},
    }
    task = {
        "status": "answered",
        "proposal": None,
        "final_answer": "物流正在运输。",
        "messages": [],
    }
    passed, reasons = module.assess_case(case, task, evidence)
    assert not passed
    assert "query_missing_matching_read_tool" in reasons


@pytest.mark.asyncio
async def test_live_case_checks_http_contract_against_durable_refund(tmp_path):
    import json

    import httpx
    from sqlalchemy.ext.asyncio import create_async_engine

    from aftersales_delivery.business import Store
    from aftersales_delivery.db import initialize

    module = runner()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'live-fixture.sqlite'}")
    await initialize(engine)
    store = Store(engine)
    case = next(case for case in module.load_cases() if case["kind"] == "refund")
    fixture = await module.clone_fixture(engine, case, "contract", 1)
    state, requests = {}, []

    async def respond(request):
        requests.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/messages"):
            assert request.headers["authorization"] == "Bearer customer-token"
            assert fixture["order_id"] in json.loads(request.content)["message"]
            action = await store.prepare(
                "learner-001", fixture["thread_id"], "refund", fixture["order_id"], "差价", "10.00"
            )
            state.update(status="waiting_approval", proposal=action, final_answer=None)
        elif path.endswith("/decision"):
            assert request.headers["authorization"] == "Bearer reviewer-token"
            assert json.loads(request.content) == {"approved": True, "version": 1}
            action = state["proposal"]
            await store.decide(action["action_id"], True, 1, "reviewer")
            action = await store.execute(action["action_id"])
            state.update(
                status="completed",
                proposal=action,
                final_answer=f"模拟退款10.00元，工单{action['result']['case_id']}",
            )
        elif path.startswith("/cases/"):
            return httpx.Response(
                200, json=await store.get_case("learner-001", path.split("/")[-1])
            )
        state["metrics"] = {"model_calls": 2, "input_tokens": 100, "output_tokens": 40}
        return httpx.Response(200, json=dict(state))

    try:
        async with httpx.AsyncClient(
            base_url="http://test", transport=httpx.MockTransport(respond)
        ) as client:
            result = await module.live_case(
                client, engine, case, fixture, 1, "customer-token", "reviewer-token"
            )
        assert result["passed"] is True
        assert len([request for request in requests if request.method == "POST"]) == 2
        assert len(result["evidence"]["refunds"]) == 1
        assert result["evidence"]["order"]["refunded_cents"] == 1000
    finally:
        await engine.dispose()


def test_run_metadata_records_hashes_model_names_versions_and_no_secrets(monkeypatch):
    import json

    module = runner()
    monkeypatch.setattr(
        "aftersales_delivery.config.project_values",
        lambda: {
            "COMPATIBLE_MODEL": "deepseek-chat",
            "DEFAULT_MODEL": "openai-compatible",
            "OPENAI_API_KEY": "private-model-key",
            "MYSQL_PASSWORD": "private-database-password",
            "DELIVERY_SIGNING_KEY": "private-signing-key",
        },
    )
    metadata = module.run_metadata()
    assert metadata["framework_baseline"] == "58f6a059f9787db1a68946030b35dd61023662fa"
    assert metadata["model_names"] == {
        "COMPATIBLE_MODEL": "deepseek-chat",
        "DEFAULT_MODEL": "openai-compatible",
    }
    assert len(metadata["source_sha256"]) == 7
    assert all(len(value) == 64 for value in metadata["source_sha256"].values())
    assert set(metadata["packages"]) == {"mcp", "langgraph", "langchain_core"}
    assert "private-" not in json.dumps(metadata)


@pytest.mark.asyncio
async def test_concurrent_run_bounds_work_authenticates_once_and_preserves_every_record(
    tmp_path, monkeypatch
):
    import asyncio
    import json
    from argparse import Namespace
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import create_async_engine

    from aftersales_delivery.db import initialize

    module = runner()
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'concurrent.sqlite'}"
    engine = create_async_engine(db_url)
    await initialize(engine)
    await engine.dispose()
    monkeypatch.setattr(
        "aftersales_delivery.config.get_config",
        lambda: SimpleNamespace(db_url=db_url, model_mode="fake_test"),
    )
    monkeypatch.setattr(module, "run_metadata", lambda: {"test_only": True})
    logins, fixture_ids, active = [], [], {"now": 0, "maximum": 0}
    first_wave = asyncio.Event()

    async def fake_request(client, method, path, token=None, body=None):
        assert path == "/auth/login"
        logins.append(body["username"])
        return {"token": f"fake-{body['username']}"}

    async def fake_case(client, engine, case, fixture, repetition, customer_token, reviewer_token):
        assert customer_token == "fake-customer1"
        assert reviewer_token == "fake-reviewer"
        active["now"] += 1
        active["maximum"] = max(active["maximum"], active["now"])
        fixture_ids.append((fixture["order_id"], fixture["thread_id"]))
        if active["now"] == 3:
            first_wave.set()
        await asyncio.wait_for(first_wave.wait(), timeout=3)
        await asyncio.sleep(0.02)
        active["now"] -= 1
        return {
            "case_id": case["id"],
            "kind": case["kind"],
            "split": case["split"],
            "repetition": repetition,
            "fixture": fixture,
            "passed": True,
            "status": "test_only",
            "elapsed_seconds": 0.02,
            "metrics": {},
        }

    monkeypatch.setattr(module, "request_api", fake_request)
    monkeypatch.setattr(module, "live_case", fake_case)
    credentials = tmp_path / "credentials.txt"
    credentials.write_text(
        json.dumps({key: "private-password" for key in ["customer1", "customer2", "reviewer"]}),
        encoding="utf-8",
    )
    output = tmp_path / "results"
    args = Namespace(
        cases=None,
        output_dir=str(output),
        live=True,
        repeat=2,
        split="all",
        base_url="http://test",
        credentials=str(credentials),
        concurrency=3,
    )
    assert await module.run(args) == 0
    assert logins == ["customer1", "reviewer"]
    assert active["maximum"] == 3
    assert len(fixture_ids) == len(set(fixture_ids)) == 96
    report = json.loads(next(output.glob("*.json")).read_text(encoding="utf-8"))
    jsonl = [
        json.loads(line)
        for line in next(output.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()
    ]
    assert report["concurrency"] == 3
    assert report["complete"] is True
    assert len(report["records"]) == len(jsonl) == 96
    assert {row["case_id"] for row in jsonl} == {case["id"] for case in module.load_cases()}
    assert "private-password" not in json.dumps(report)
