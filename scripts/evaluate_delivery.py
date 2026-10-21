"""Fixed-case live evaluation and a separately labelled SQLite fault benchmark.

No default model calls. --live explicitly invokes the running delivery API, which
must use the same project-owned database as this runner's delivery configuration.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
KINDS = {"logistics", "refund", "reship", "query", "missing", "unauthorized", "refusal"}


def run_metadata():
    """Freeze reproducibility information without copying configuration secrets."""
    from aftersales_delivery.config import project_values

    source_paths = [
        f"src/aftersales_delivery/{name}.py"
        for name in [
            "agent",
            "api",
            "business",
            "mcp_server",
            "db",
            "transport",
        ]
    ] + ["uv.lock"]
    hashes = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in source_paths}
    values = project_values()
    models = {}
    for key in ["COMPATIBLE_MODEL", "DEFAULT_MODEL"]:
        candidate = values.get(key)
        if (
            isinstance(candidate, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", candidate)
            and not candidate.startswith(("sk-", "http:", "https:"))
        ):
            models[key] = candidate
    packages = {}
    for name, distribution in [
        ("mcp", "mcp"),
        ("langgraph", "langgraph"),
        ("langchain_core", "langchain-core"),
    ]:
        try:
            packages[name] = version(distribution)
        except PackageNotFoundError:
            packages[name] = None
    return {
        "framework_baseline": "58f6a059f9787db1a68946030b35dd61023662fa",
        "source_sha256": hashes,
        "model_names": models,
        "packages": packages,
    }


def validate_cases(dataset):
    if not isinstance(dataset, list) or len(dataset) != 48:
        raise ValueError("Expected exactly 48 fixed evaluation cases")
    if Counter(case.get("split") for case in dataset) != {"dev": 32, "heldout": 16}:
        raise ValueError("Expected 32 dev and 16 heldout cases")
    if len({case.get("id") for case in dataset}) != 48:
        raise ValueError("Case IDs must be unique")
    for case in dataset:
        if not re.fullmatch(r"[DH][0-9]{2}", case.get("id", "")) or case.get("kind") not in KINDS:
            raise ValueError("Invalid case identity or kind")
        expected = case.get("expected")
        if not isinstance(expected, dict) or expected.get("outcome") not in {
            "completed",
            "answered",
            "rejected",
        }:
            raise ValueError("Every case requires an explicit expected outcome")
        if case.get("fixture") not in {"DEMO-001", "DEMO-002", "DEMO-003", "DEMO-004"}:
            raise ValueError("Unknown fixture")
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            raise ValueError("Every case requires a prompt")
        if expected["outcome"] in {"completed", "rejected"} and expected.get("action_kind") not in {
            "logistics",
            "refund",
            "reship",
        }:
            raise ValueError("Action cases require an action kind")
        if expected.get("action_kind") == "refund" and expected.get("amount") != "10.00":
            raise ValueError("Fixed refund amount must be exactly 10.00")
        if expected.get("action_kind") in {"refund", "reship"} and expected.get("quantity") != 1:
            raise ValueError("Fixed action quantity must be one")
        if expected["outcome"] == "answered" and not expected.get("answer_contains_any"):
            raise ValueError("Read-only cases require a semantic answer expectation")


def load_cases(path=None):
    path = Path(path) if path else ROOT / "evaluation" / "delivery" / "cases.json"
    dataset = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_cases(dataset)
    return dataset


async def clone_fixture(engine, case, run_id, repetition):
    """Append a new order/SKU/shipment set; never reset seed or historical rows."""
    from sqlalchemy import insert, select

    from aftersales_delivery.db import inventory, orders, shipments

    suffix = f"{run_id}-{case['id']}-{repetition}"
    fixture = {
        "order_id": f"EV-{suffix}",
        "sku": f"EVSKU-{suffix}",
        "thread_id": f"delivery-eval-{suffix}",
        "customer_id": "learner-001",
    }
    if len(fixture["order_id"]) > 80 or len(fixture["thread_id"]) > 64:
        raise ValueError("Evaluation fixture identity is too long")
    async with engine.begin() as conn:
        source = dict(
            (await conn.execute(select(orders).where(orders.c.order_id == case["fixture"])))
            .mappings()
            .one()
        )
        source.update(
            order_id=fixture["order_id"], sku=fixture["sku"], refunded_cents=0, reshipped_quantity=0
        )
        source["customer_id"] = "learner-002" if case["kind"] == "unauthorized" else "learner-001"
        await conn.execute(insert(orders).values(**source))
        await conn.execute(insert(inventory).values(sku=fixture["sku"], available=3))
        original_shipments = (
            (await conn.execute(select(shipments).where(shipments.c.order_id == case["fixture"])))
            .mappings()
            .all()
        )
        for index, row in enumerate(original_shipments):
            shipment = dict(row)
            shipment.update(
                shipment_id=f"EVSHIP-{suffix}-{index}",
                order_id=fixture["order_id"],
                tracking_number=f"EVTRACK-{suffix}-{index}",
            )
            await conn.execute(insert(shipments).values(**shipment))
    return fixture


async def evidence_for(engine, fixture):
    from sqlalchemy import select

    from aftersales_delivery.db import actions, cases, inventory, orders, refunds, reships

    async with engine.connect() as conn:
        evidence = {}
        for name, table in [
            ("actions", actions),
            ("cases", cases),
            ("refunds", refunds),
            ("reships", reships),
        ]:
            evidence[name] = [
                dict(row)
                for row in (
                    await conn.execute(select(table).where(table.c.order_id == fixture["order_id"]))
                ).mappings()
            ]
        evidence["order"] = dict(
            (await conn.execute(select(orders).where(orders.c.order_id == fixture["order_id"])))
            .mappings()
            .one()
        )
        evidence["available"] = (
            await conn.execute(
                select(inventory.c.available).where(inventory.c.sku == fixture["sku"])
            )
        ).scalar_one()
        return evidence


def assess_case(case, task, evidence):
    """Require semantic output AND durable effects; HTTP/task status alone fails."""
    reasons = []
    expected = case["expected"]
    proposal = task.get("proposal") or {}
    answer = task.get("final_answer") or ""
    outcome = expected["outcome"]
    if task.get("status") != outcome:
        reasons.append("unexpected_task_status")
    if not isinstance(answer, str) or not answer.strip():
        reasons.append("missing_final_answer")
    for key in ["actions", "cases", "refunds", "reships", "order", "available"]:
        if key not in evidence:
            reasons.append(f"missing_evidence_{key}")
    if reasons and not evidence:
        return False, reasons
    action_rows, case_rows = evidence.get("actions", []), evidence.get("cases", [])
    refund_rows, reship_rows = evidence.get("refunds", []), evidence.get("reships", [])
    order = evidence.get("order", {})
    if outcome == "answered":
        if proposal or action_rows or case_rows or refund_rows or reship_rows:
            reasons.append("read_only_case_created_business_records")
        if not any(word in answer for word in expected["answer_contains_any"]):
            reasons.append("answer_semantics_not_matched")
        if case["kind"] == "query":
            order_id = order.get("order_id")
            calls = [
                call
                for message in task.get("messages", [])
                for call in message.get("tool_calls", [])
            ]
            if not any(
                call.get("name") in {"get_order", "get_shipments"}
                and call.get("args", {}).get("order_id") == order_id
                for call in calls
            ):
                reasons.append("query_missing_matching_read_tool")

            def contains_order(value):
                if isinstance(value, dict):
                    return value.get("order_id") == order_id or any(
                        contains_order(item) for item in value.values()
                    )
                if isinstance(value, list):
                    return any(contains_order(item) for item in value)
                return False

            grounded = False
            for message in task.get("messages", []):
                if message.get("type") == "tool":
                    try:
                        grounded = grounded or contains_order(
                            json.loads(message.get("content", ""))
                        )
                    except (ValueError, TypeError):
                        pass
            if not grounded:
                reasons.append("query_missing_successful_tool_evidence")
        if case["kind"] == "unauthorized" and any(
            word in answer for word in ["399.00", "399元", "399 元", "SKU-002"]
        ):
            reasons.append("foreign_order_detail_leaked")
    else:
        kind = expected["action_kind"]
        if (
            proposal.get("kind") != kind
            or len(action_rows) != 1
            or action_rows[0].get("kind") != kind
        ):
            reasons.append("action_kind_or_count_mismatch")
        if action_rows and proposal.get("action_id") != action_rows[0]["action_id"]:
            reasons.append("action_identity_mismatch")
        if kind == "refund" and proposal.get("amount") != expected["amount"]:
            reasons.append("refund_amount_mismatch")
        if kind == "reship" and proposal.get("quantity") != expected["quantity"]:
            reasons.append("reship_quantity_mismatch")
        if outcome == "rejected":
            if proposal.get("status") != "rejected" or any(
                row.get("status") != "rejected" for row in action_rows
            ):
                reasons.append("rejection_not_persisted")
            if case_rows or refund_rows or reship_rows:
                reasons.append("rejected_action_wrote_business_records")
        else:
            result = proposal.get("result") or {}
            if proposal.get("status") != "completed" or len(case_rows) != 1:
                reasons.append("business_not_completed_once")
            if len(case_rows) == 1:
                case_id = case_rows[0]["case_id"]
                if result.get("case_id") != case_id or case_id not in answer:
                    reasons.append("final_answer_or_result_missing_actual_case")
                if evidence.get("api_case", {}).get("case_id") != case_id:
                    reasons.append("case_api_not_verified")
            if result.get("simulated") is not True:
                reasons.append("missing_simulation_marker")
            if kind == "refund":
                if (
                    len(refund_rows) != 1
                    or sum(row["amount_cents"] for row in refund_rows) != 1000
                    or order.get("refunded_cents") != 1000
                    or reship_rows
                ):
                    reasons.append("durable_refund_mismatch")
                if result.get("amount") != "10.00":
                    reasons.append("refund_result_amount_mismatch")
            elif kind == "reship":
                if (
                    len(reship_rows) != 1
                    or sum(row["quantity"] for row in reship_rows) != 1
                    or order.get("reshipped_quantity") != 1
                    or evidence.get("available") != 2
                    or refund_rows
                ):
                    reasons.append("durable_reship_mismatch")
                if result.get("quantity") != 1:
                    reasons.append("reship_result_quantity_mismatch")
            elif refund_rows or reship_rows:
                reasons.append("logistics_changed_money_or_stock")
    if outcome != "completed" or expected.get("action_kind") == "logistics":
        if (
            order.get("refunded_cents") != 0
            or order.get("reshipped_quantity") != 0
            or evidence.get("available") != 3
        ):
            reasons.append("unexpected_order_or_inventory_change")
    return not reasons, reasons


def summarize(records):
    def compact(items):
        count = len(items)
        passed = sum(row.get("passed") is True for row in items)
        return {
            "attempts": count,
            "passed": passed,
            "success_rate": passed / count if count else None,
        }

    summary = compact(records)
    summary["mean_elapsed_seconds"] = (
        sum(row.get("elapsed_seconds", 0) for row in records) / len(records) if records else None
    )
    summary["by_kind"] = {
        kind: compact([row for row in records if row["kind"] == kind])
        for kind in sorted({row["kind"] for row in records})
    }
    summary["by_split"] = {
        split: compact([row for row in records if row["split"] == split])
        for split in sorted({row["split"] for row in records})
    }
    for key in ["model_calls", "model_attempts", "input_tokens", "output_tokens", "total_tokens"]:
        observed = [row.get("metrics", {}).get(key) for row in records]
        numeric = [
            value
            for value in observed
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        summary[key] = {
            "observed_total": sum(numeric) if numeric else None,
            "missing_attempts": len(records) - len(numeric),
        }
    return summary


async def fault_benchmark(directory):
    """Controlled SQLite persistence comparison, not a model quality baseline.

    Naive ledger inserts once per attempt and has no persisted approval/idempotency.
    Guarded Store receives the same refund intention and delivery failure schedule.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from aftersales_delivery.business import BusinessError, Store
    from aftersales_delivery.db import initialize

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    scenarios = []
    for scenario in ["response_lost", "rejected", "concurrent", "restart", "failed_reply"]:
        db_path = directory / f"fault-{scenario}-{uuid4().hex[:8]}.sqlite"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", connect_args={"timeout": 20})
        try:
            await initialize(engine)
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "CREATE TABLE naive_refunds (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, amount_cents INTEGER NOT NULL)"
                    )
                )

            async def naive_write():
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO naive_refunds (order_id, amount_cents) VALUES ('DEMO-004', 1000)"
                        )
                    )

            store = Store(engine)
            action = await store.prepare(
                "learner-001",
                f"fault-{scenario}",
                "refund",
                "DEMO-004",
                "评测：固定差价退款",
                "10.00",
            )
            await store.decide(action["action_id"], scenario != "rejected", 1, "reviewer")
            if scenario == "concurrent":
                await asyncio.gather(naive_write(), naive_write())
                outputs = await asyncio.gather(
                    store.execute(action["action_id"]), store.execute(action["action_id"])
                )
                stable = outputs[0]["result"] == outputs[1]["result"]
            elif scenario == "rejected":
                await naive_write()
                try:
                    await store.execute(action["action_id"])
                    stable = False
                except BusinessError as error:
                    stable = error.code == "NOT_APPROVED"
            else:
                await naive_write()
                first = await store.execute(action["action_id"])
                # Failure occurs after COMMIT: the response or final model reply was lost.
                try:
                    raise TimeoutError("simulated committed response lost")
                except TimeoutError:
                    pass
                if scenario == "restart":
                    await engine.dispose()
                    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
                    store = Store(engine)
                await naive_write()
                second = await store.execute(action["action_id"])
                stable = first["result"] == second["result"]
            async with engine.connect() as conn:
                naive_count = (
                    await conn.execute(text("SELECT COUNT(*) FROM naive_refunds"))
                ).scalar_one()
                guarded_count = (
                    await conn.execute(text("SELECT COUNT(*) FROM refunds"))
                ).scalar_one()
                guarded_cents = (
                    await conn.execute(
                        text("SELECT refunded_cents FROM orders WHERE order_id='DEMO-004'")
                    )
                ).scalar_one()
            scenarios.append(
                {
                    "scenario": scenario,
                    "naive": {"refund_count": naive_count, "refunded_cents": naive_count * 1000},
                    "guarded": {"refund_count": guarded_count, "refunded_cents": guarded_cents},
                    "passed": stable and guarded_count == (0 if scenario == "rejected" else 1),
                }
            )
        finally:
            await engine.dispose()
    return {
        "mode": "engineering_fault_benchmark",
        "llm_used": False,
        "baseline_llm_executed": False,
        "scenarios": scenarios,
        "passed": all(row["passed"] for row in scenarios),
        "limitations": "SQLite controlled write-ledger baseline only; no LLM baseline, real network fault injection or MySQL process crash is measured here.",
    }


class EvaluationAPIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


async def request_api(client, method, path, token=None, body=None):
    import httpx

    try:
        response = await client.request(
            method, path, headers={"Authorization": f"Bearer {token}"} if token else {}, json=body
        )
    except httpx.TimeoutException:
        raise EvaluationAPIError("REQUEST_TIMEOUT") from None
    except httpx.HTTPError:
        raise EvaluationAPIError("CONNECTION_ERROR") from None
    try:
        payload = response.json()
    except ValueError:
        raise EvaluationAPIError("INVALID_RESPONSE") from None
    if not response.is_success:
        code = (
            payload.get("detail", {}).get("code")
            if isinstance(payload, dict) and isinstance(payload.get("detail"), dict)
            else None
        )
        raise EvaluationAPIError(
            code
            if isinstance(code, str) and re.fullmatch(r"[A-Z_a-z0-9-]{1,80}", code)
            else f"HTTP_{response.status_code}"
        )
    return payload


def write_results(output_dir, stem, report):
    """Atomically refresh readable snapshots after every fsynced JSONL attempt."""
    target = output_dir / f"{stem}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    summary = report.get("summary", {})
    lines = [
        "# 售后 Agent 评测记录",
        "",
        f"模式：{report['mode']}",
        f"开始时间：{report['started_at']}",
        "",
        "真实 LLM 基线：尚未执行；离线故障对照不能替代模型基线。",
        "",
    ]
    if summary:
        lines += [
            f"已记录 {summary['attempts']} 次，成功 {summary['passed']} 次，成功率 {summary['success_rate']:.1%}。",
            f"平均端到端耗时：{summary['mean_elapsed_seconds']:.3f} 秒。",
            "",
            "| 分组 | 尝试 | 成功 | 成功率 |",
            "|---|---:|---:|---:|",
        ]
        for grouping in ["by_split", "by_kind"]:
            for label, values in summary[grouping].items():
                lines.append(
                    f"| {label} | {values['attempts']} | {values['passed']} | {values['success_rate']:.1%} |"
                )
        lines += [
            "",
            "Token 仅累加服务实际返回的计量字段；缺失值保持未知，不按零补齐。最终回复调用是否被计入，以服务端 metrics 为准。",
            "",
            "| 计量字段 | 已观测总量 | 缺失尝试 |",
            "|---|---:|---:|",
        ]
        for field in [
            "model_calls",
            "model_attempts",
            "input_tokens",
            "output_tokens",
            "total_tokens",
        ]:
            item = summary[field]
            lines.append(f"| {field} | {item['observed_total']} | {item['missing_attempts']} |")
    if "fault_benchmark" in report:
        lines += [
            "",
            "离线部分仅验证固定数据集结构与真实 SQLite 故障恢复机制；未调用 LLM，也未对48条提示词计算模型成功率。",
            "",
            "| 故障 | 简单写入次数 | 受控写入次数 | 检查通过 |",
            "|---|---:|---:|---|",
        ]
        for row in report["fault_benchmark"]["scenarios"]:
            lines.append(
                f"| {row['scenario']} | {row['naive']['refund_count']} | {row['guarded']['refund_count']} | {row['passed']} |"
            )
    (output_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def live_case(client, engine, case, fixture, repetition, customer_token, reviewer_token):
    started = time.perf_counter()
    record = {
        "case_id": case["id"],
        "kind": case["kind"],
        "split": case["split"],
        "repetition": repetition,
        "fixture": fixture,
        "passed": False,
        "errors": [],
        "metrics": {},
    }
    task = {}
    try:
        prompt = case["prompt"].format(order_id=fixture["order_id"])
        try:
            task = await request_api(
                client,
                "POST",
                f"/tasks/{fixture['thread_id']}/messages",
                customer_token,
                {"message": prompt},
            )
        except EvaluationAPIError as error:
            record["errors"].append(error.code)
            task = await request_api(
                client, "GET", f"/tasks/{fixture['thread_id']}", customer_token
            )
        before = await evidence_for(engine, fixture)
        proposal = task.get("proposal") or {}
        expected = case["expected"]
        if expected["outcome"] in {"completed", "rejected"}:
            record["pre_approval_evidence"] = before
            if any(before[name] for name in ["cases", "refunds", "reships"]):
                record["errors"].append("BUSINESS_EFFECT_BEFORE_APPROVAL")
            snapshot_matches = (
                proposal.get("kind") == expected["action_kind"]
                and proposal.get("order_id") == fixture["order_id"]
                and proposal.get("customer_id") == fixture["customer_id"]
                and ("amount" not in expected or proposal.get("amount") == expected["amount"])
                and ("quantity" not in expected or proposal.get("quantity") == expected["quantity"])
            )
            if snapshot_matches and proposal.get("status") == "waiting_approval":
                try:
                    task = await request_api(
                        client,
                        "POST",
                        f"/approvals/{proposal['action_id']}/decision",
                        reviewer_token,
                        {
                            "approved": expected["outcome"] != "rejected",
                            "version": proposal["version"],
                        },
                    )
                except EvaluationAPIError as error:
                    record["errors"].append(error.code)
                    task = await request_api(
                        client, "GET", f"/tasks/{fixture['thread_id']}", customer_token
                    )
                    if task.get("status") == "reply_failed":
                        # Explicitly retry the approved original action, never send the application twice.
                        record["recovery_attempted"] = True
                        try:
                            task = await request_api(
                                client,
                                "POST",
                                f"/approvals/{proposal['action_id']}/retry",
                                reviewer_token,
                            )
                        except EvaluationAPIError as recovery_error:
                            record["errors"].append(recovery_error.code)
                            task = await request_api(
                                client, "GET", f"/tasks/{fixture['thread_id']}", customer_token
                            )
        # Read durable final state even after a successful POST, rather than trusting response status.
        task = await request_api(client, "GET", f"/tasks/{fixture['thread_id']}", customer_token)
        evidence = await evidence_for(engine, fixture)
        case_id = ((task.get("proposal") or {}).get("result") or {}).get("case_id")
        if case_id:
            evidence["api_case"] = await request_api(
                client, "GET", f"/cases/{case_id}", customer_token
            )
        passed, reasons = assess_case(case, task, evidence)
        record.update(
            passed=passed and "BUSINESS_EFFECT_BEFORE_APPROVAL" not in record["errors"],
            reasons=reasons,
            task=task,
            evidence=evidence,
        )
    except EvaluationAPIError as error:
        record["errors"].append(error.code)
    except Exception as error:
        # Exception text can contain database URLs, tokens or model payloads.
        record["errors"].append(f"LOCAL_{type(error).__name__}")
    record["metrics"] = task.get("metrics", {})
    record["status"] = task.get("status", "unknown")
    record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return record


async def run(args):
    dataset = load_cases(args.cases)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC).isoformat()
    stem = "evaluation-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]
    report = {
        "mode": "live_llm" if args.live else "offline_engineering",
        "started_at": started_at,
        "dataset_cases": 48,
        "dataset_split": {"dev": 32, "heldout": 16},
        "baseline_llm_executed": False,
        "complete": False,
        "records": [],
        "metadata": run_metadata(),
    }
    if not args.live:
        with tempfile.TemporaryDirectory(prefix="delivery-evaluation-") as temporary:
            report["fault_benchmark"] = await fault_benchmark(temporary)
        report["complete"] = True
        write_results(output_dir, stem, report)
        print(f"Offline engineering checks: {report['fault_benchmark']['passed']}; LLM not called.")
        print(f"Saved {output_dir / (stem + '.json')}")
        return 0 if report["fault_benchmark"]["passed"] else 1
    import httpx
    from sqlalchemy.ext.asyncio import create_async_engine

    from aftersales_delivery.config import get_config

    credentials = json.loads(Path(args.credentials).read_text(encoding="utf-8-sig"))
    if not all(
        isinstance(credentials.get(key), str) and credentials[key]
        for key in ["customer1", "customer2", "reviewer"]
    ):
        raise ValueError(
            "Credential file must contain generated customer1/customer2/reviewer passwords"
        )
    parsed = urlsplit(args.base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("API address must be HTTP(S) without embedded credentials")
    config = get_config()
    selected = [case for case in dataset if args.split == "all" or case["split"] == args.split]
    report.update(
        planned_attempts=len(selected) * args.repeat,
        repeat=args.repeat,
        split=args.split,
        base_url=args.base_url,
        model_mode=config.model_mode,
        concurrency=getattr(args, "concurrency", 1),
    )
    engine = create_async_engine(config.db_url, pool_pre_ping=True)
    try:
        async with httpx.AsyncClient(
            base_url=args.base_url.rstrip("/"),
            timeout=120.0,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            tokens = {}
            for username in ["customer1", "reviewer"]:
                login = await request_api(
                    client,
                    "POST",
                    "/auth/login",
                    body={"username": username, "password": credentials[username]},
                )
                tokens[username] = login["token"]
            del credentials
            run_id = uuid4().hex[:12]
            report["run_id"] = run_id
            write_results(output_dir, stem, report)
            semaphore = asyncio.Semaphore(report["concurrency"])

            async def execute_one(case, repetition):
                async with semaphore:
                    try:
                        fixture = await clone_fixture(engine, case, run_id, repetition)
                        return await live_case(
                            client,
                            engine,
                            case,
                            fixture,
                            repetition,
                            tokens["customer1"],
                            tokens["reviewer"],
                        )
                    except Exception as error:
                        return {
                            "case_id": case["id"],
                            "kind": case["kind"],
                            "split": case["split"],
                            "repetition": repetition,
                            "passed": False,
                            "status": "fixture_or_execution_failed",
                            "errors": [f"LOCAL_{type(error).__name__}"],
                            "metrics": {},
                            "elapsed_seconds": 0,
                        }

            tasks = [
                asyncio.create_task(execute_one(case, repetition))
                for repetition in range(1, args.repeat + 1)
                for case in selected
            ]
            try:
                with (output_dir / f"{stem}.jsonl").open("a", encoding="utf-8") as incremental:
                    # Only this coroutine writes files; worker completion order cannot
                    # race or overwrite another attempt's incremental record/snapshot.
                    for completed in asyncio.as_completed(tasks):
                        row = await completed
                        incremental.write(json.dumps(row, ensure_ascii=False) + "\n")
                        incremental.flush()
                        os.fsync(incremental.fileno())
                        report["records"].append(row)
                        report["summary"] = summarize(report["records"])
                        write_results(output_dir, stem, report)
                        print(
                            f"{len(report['records'])}/{report['planned_attempts']} {row['case_id']} repeat={row['repetition']} passed={row['passed']} status={row['status']}",
                            flush=True,
                        )
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await engine.dispose()
    report["complete"] = True
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["source_unchanged"] = report["metadata"].get("source_sha256") == run_metadata().get(
        "source_sha256"
    )
    write_results(output_dir, stem, report)
    print(f"Saved {output_dir / (stem + '.json')}")
    return (
        0 if report["source_unchanged"] and all(row["passed"] for row in report["records"]) else 1
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live",
        action="store_true",
        help="Call the running real-model API; appends isolated fixtures",
    )
    mode.add_argument(
        "--offline",
        action="store_true",
        help="Validate cases and run SQLite engineering faults (default)",
    )
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--concurrency", type=int, default=1, help="Bound active attempts to 1..4 (default: 1)"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--credentials", default=str(ROOT / "output/delivery/credentials.txt"))
    parser.add_argument("--cases", default=str(ROOT / "evaluation/delivery/cases.json"))
    parser.add_argument("--output-dir", default=str(ROOT / "output/delivery"))
    parser.add_argument("--split", choices=["all", "dev", "heldout"], default="all")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 100:
        parser.error("--repeat must be between 1 and 100")
    if not 1 <= args.concurrency <= 4:
        parser.error("--concurrency must be between 1 and 4")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print(
            "Interrupted; completed attempts remain in incremental JSONL and snapshots.",
            file=sys.stderr,
        )
        return 130
    except Exception as error:
        print(
            f"Evaluation stopped: {type(error).__name__}. Check project delivery configuration and saved records.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
