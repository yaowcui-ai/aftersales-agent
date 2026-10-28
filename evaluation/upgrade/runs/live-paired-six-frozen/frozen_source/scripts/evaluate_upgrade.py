"""Upgrade evaluation entrypoint. Offline by default; only --live permits HTTP.

Every execution uses a fresh temporary SQLite file, including live model trials.
The structured B0 form runner does not claim natural-language understanding.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import math
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
CATALOG = ROOT / "evaluation/upgrade/cases.json"
REQUIRED = {"case_id", "initial_state", "user_turns", "expected_tools", "expected_db",
            "approval_required", "forbidden_actions", "split", "category"}


def condition_signature(case):
    """Ignore wording, identity and expectations when detecting duplicate inputs."""
    semantic = {"state": case["initial_state"], "steps": case["input_steps"]}
    return hashlib.sha256(json.dumps(semantic, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_cases(path=CATALOG):
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    if len(cases) < 120:
        raise ValueError("At least 120 independent scenarios are required")
    seen_ids, seen_conditions = set(), set()
    for case in cases:
        if not REQUIRED <= case.keys() or case["split"] not in {"dev", "validation", "test"}:
            raise ValueError("Invalid scenario schema or split")
        if not case["user_turns"] or not isinstance(case["expected_db"], dict):
            raise ValueError("Every case needs actual inputs and database assertions")
        signature = condition_signature(case)
        if case["case_id"] in seen_ids or signature in seen_conditions:
            raise ValueError(f"Duplicate case or business condition: {case['case_id']}")
        seen_ids.add(case["case_id"])
        seen_conditions.add(signature)
    return cases


def verify_manifest(cases, path=CATALOG):
    manifest = json.loads((ROOT / "evaluation/upgrade/manifest.json").read_text(encoding="utf-8"))
    actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    split_ids = {s: [c["case_id"] for c in cases if c["split"] == s] for s in ["dev", "validation", "test"]}
    return {"valid": actual == manifest["sha256"] and split_ids == manifest["splits"],
            "sha256": actual, "case_count": len(cases), "split_counts": dict(Counter(c["split"] for c in cases)),
            "categories": dict(Counter(c["category"] for c in cases)),
            "evidence_type": "scenario_catalog_quality_only"}


def input_plan(case):
    """B0 consumes only submitted form/test operations; never expected fields."""
    return copy.deepcopy(case["input_steps"])


class BudgetExceeded(RuntimeError):
    pass


class ModelBudget:
    """One process-wide ledger; reserve BEFORE every HTTP attempt, retries included."""

    def __init__(self, max_calls=60, max_tokens=120000):
        if not 1 <= max_calls <= 60 or not 1 <= max_tokens <= 120000:
            raise ValueError("Budget cannot exceed 60 HTTP calls / 120000 estimated tokens")
        self.max_calls, self.max_tokens = max_calls, max_tokens
        self.calls = self.estimated_tokens = self.reported_tokens = self.missing_usage = 0

    def reserve(self, estimated_tokens):
        if self.calls >= self.max_calls or self.estimated_tokens + estimated_tokens > self.max_tokens:
            raise BudgetExceeded("MODEL_BUDGET_EXHAUSTED")
        self.calls += 1
        self.estimated_tokens += estimated_tokens

    def record(self, usage):
        if not usage or usage.get("total_tokens") is None:
            self.missing_usage += 1
        else:
            self.reported_tokens += usage["total_tokens"]

    def summary(self):
        return {"http_calls": self.calls, "estimated_tokens_reserved": self.estimated_tokens,
                "reported_total_tokens": self.reported_tokens,
                "total_tokens": None if self.missing_usage else self.reported_tokens,
                "missing_usage_calls": self.missing_usage,
                "max_http_calls": self.max_calls, "max_estimated_tokens": self.max_tokens}


def select_cases(cases, limit=12, split=None):
    eligible = [c for c in cases if split is None or c["split"] == split]
    if limit is None:
        return eligible
    groups = {}
    for case in eligible:
        groups.setdefault(case["category"], []).append(case)
    chosen = []
    while groups and len(chosen) < limit:
        for category in list(groups):
            chosen.append(groups[category].pop(0))
            if not groups[category]:
                del groups[category]
            if len(chosen) == limit:
                break
    return chosen


async def snapshot(engine):
    from sqlalchemy import select
    from aftersales_delivery import db
    async with engine.connect() as conn:
        tables = {}
        for name, table in db.metadata.tables.items():
            tables[name] = [dict(row) for row in (await conn.execute(select(table))).mappings()]
    order = next(r for r in tables["orders"] if r["order_id"] == "ORDER-A")
    return {"order": order, "stock": tables["inventory"][0]["available"],
            "refund_count": len(tables["refunds"]), "reship_count": len(tables["reships"]),
            "case_count": len(tables["cases"]), "tables": tables,
            "action_status": tables["actions"][0]["status"] if tables["actions"] else None}


async def create_fixture(engine, case):
    from sqlalchemy import insert
    from aftersales_delivery import db
    # Import policy table registration without calling any production initializer.
    import aftersales_delivery.policies  # noqa: F401
    state = case["initial_state"]
    async with engine.begin() as conn:
        await conn.run_sync(db.metadata.create_all)
        await conn.execute(insert(db.orders).values(
            order_id="ORDER-A", customer_id=state["owner"], sku="SKU-A",
            quantity=state["quantity"], paid_cents=state["paid_cents"],
            refunded_cents=state["refunded_cents"], reshipped_quantity=state["reshipped_quantity"], status=state["status"]))
        await conn.execute(insert(db.inventory).values(sku="SKU-A", available=state["stock"]))
        table = db.metadata.tables["order_policies"]
        await conn.execute(insert(table).values(order_id="ORDER-A", aftersales_until=state["aftersales_until"],
            risk_level=state["risk_level"], manual_review_threshold_cents=state["manual_review_threshold_cents"]))
        for index, shipment in enumerate(state["shipments"]):
            await conn.execute(insert(db.shipments).values(shipment_id=f"SHIP-{index}", order_id="ORDER-A",
                carrier="沙箱物流", tracking_number=f"TRACK-{index}", status=shipment["status"],
                description=shipment["description"], updated_at=f"2026-10-28T00:00:{index:02}+00:00"))


class Sandbox:
    def __init__(self, store):
        self.store, self.trace, self.action, self.read_result = store, [], None, None
        self.errors = []
        self.human_review = False

    async def invoke(self, name, arguments):
        """Public and test-control calls share policy-enforced Store boundaries."""
        from sqlalchemy import event, update
        from aftersales_delivery import db
        from aftersales_delivery.business import BusinessError
        args = copy.deepcopy(arguments)
        started = time.perf_counter()
        record = {"tool": name, "args": copy.deepcopy(args)}
        try:
            if name in {"prepare_action", "prepare_concurrently"}:
                allowed = {"kind", "order_id", "description", "amount", "quantity"}
                if not args.keys() <= allowed:
                    raise BusinessError("INVALID_TOOL_ARGUMENTS")
                args["order"] = args.pop("order_id")
                async def prepare():
                    return await self.store.prepare("customer-a", "scenario-task", **args)
                if name == "prepare_concurrently":
                    values = await asyncio.gather(*(prepare() for _ in range(3)))
                    if len({v["action_id"] for v in values}) != 1:
                        raise BusinessError("DUPLICATE_ACTION")
                    value = values[0]
                else:
                    value = await prepare()
                self.action = value
                self.human_review |= value.get("manual_review", False)
            elif name in {"get_order", "get_shipments", "get_case"}:
                value = await getattr(self.store, name)("customer-a", **{
                    {"get_order": "order", "get_shipments": "order", "get_case": "case"}[name]: next(iter(args.values()))})
                self.read_result = value
            elif name in {"get_inventory", "list_orders"}:
                value = await getattr(self.store, name)(**args) if name == "get_inventory" else await self.store.list_orders("customer-a", **args)
                self.read_result = value
            elif name in {"approve", "reject"}:
                value = await self.store.decide(self._aid(), name == "approve", args.get("version", self.action["version"]), args.get("actor", "reviewer"), reason="隔离评测核验")
                self.action = value
            elif name in {"execute", "execute_lose_response", "execute_concurrently", "execute_fail_write"}:
                if name == "execute_concurrently":
                    values = await asyncio.gather(*(self.store.execute(self._aid()) for _ in range(args.get("count", 3))))
                    value = values[0]
                elif name == "execute_fail_write":
                    def fail(conn, cursor, statement, parameters, context, executemany):
                        if statement.lstrip().upper().startswith("INSERT INTO REFUNDS"):
                            raise RuntimeError("INJECTED_WRITE_FAILURE")
                    event.listen(self.store.engine.sync_engine, "before_cursor_execute", fail)
                    try:
                        value = await self.store.execute(self._aid())
                    finally:
                        event.remove(self.store.engine.sync_engine, "before_cursor_execute", fail)
                else:
                    value = await self.store.execute(self._aid())
                self.action = value
                if name == "execute_lose_response":
                    raise BusinessError("RESPONSE_LOST")
            elif name == "cancel":
                value = await self.store.cancel_action(args.get("customer", "customer-a"), self._aid())
                self.action = value
            elif name == "receive_return":
                value = await self.store.receive_return(self._aid(), args.get("actor", "reviewer"))
                self.action = value
            elif name.startswith("mutate_"):
                table, column, identity = {
                    "mutate_order": (db.orders, "order_id", "ORDER-A"),
                    "mutate_inventory": (db.inventory, "sku", "SKU-A"),
                    "mutate_policy": (db.metadata.tables["order_policies"], "order_id", "ORDER-A"),
                    "mutate_action": (db.actions, "action_id", self._aid()),
                }[name]
                async with self.store.engine.begin() as conn:
                    await conn.execute(update(table).where(table.c[column] == identity).values(**args))
                value = {"test_fixture_mutation": True}
            elif name == "read_unavailable":
                raise BusinessError("TOOL_UNAVAILABLE")
            elif name == "clarify":
                value = {"clarification_required": args["field"], "scope": "structured form validator"}
            else:
                raise BusinessError("UNKNOWN_TOOL")
            record["result"] = value
            return value
        except Exception as exc:
            code = exc.code if isinstance(exc, BusinessError) else ("INJECTED_WRITE_FAILURE" if str(exc) == "INJECTED_WRITE_FAILURE" else type(exc).__name__)
            self.errors.append(code)
            record["error"] = code
            return {"error": code}
        finally:
            record["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.trace.append(record)

    def _aid(self):
        if self.action is None:
            raise ValueError("ACTION_NOT_PREPARED")
        return self.action["action_id"]


def _lookup(value, dotted):
    for key in dotted.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def judge(case, before, after, sandbox, baseline="B0"):
    observations = {**after, "read_result": sandbox.read_result,
                    "read_result_length": len(sandbox.read_result) if isinstance(sandbox.read_result, list) else None,
                    "manual_review": sandbox.human_review}
    checks = []
    def check(name, expected, actual):
        checks.append({"name": name, "expected": expected, "actual": actual, "passed": expected == actual})
    for key, expected in case["expected_db"].items():
        check(f"db.{key}", expected, _lookup(observations, key))
    check("errors", case["expected_errors"], sandbox.errors)
    actual_tools = [{"tool": t["tool"], "args": t["args"]} for t in sandbox.trace]
    check("tool_names_and_arguments", case["expected_tools"], actual_tools)
    if baseline != "B0":
        # Predeclared model grading: extra safe observations are permitted; request
        # descriptions need valid text, monetary/quantity/identity args must match.
        # Keep exact trace fidelity visible as an unscored diagnostic.
        checks[-1]["scored"] = False
        from decimal import Decimal, InvalidOperation
        def normalize(step):
            args = dict(step["args"])
            if step["tool"] == "prepare_action":
                description = args.pop("description", None)
                args["valid_description"] = isinstance(description, str) and bool(description.strip()) and len(description) <= 4000
                args.setdefault("quantity", 1)
                args.setdefault("amount", None)
                if args["amount"] is not None:
                    try:
                        args["amount"] = str(Decimal(str(args["amount"])).quantize(Decimal(".01")))
                    except InvalidOperation:
                        pass
            return {"tool": step["tool"], "args": args}
        expected_core = [normalize(t) for t in case["expected_tools"]]
        actual_core = [normalize(t) for t in actual_tools]
        optional_reads = {"get_order", "get_shipments", "get_inventory", "list_orders", "get_case"}
        cursor = 0
        matched = True
        for item in actual_core:
            if cursor < len(expected_core) and item == expected_core[cursor]:
                cursor += 1
            elif item["tool"] not in optional_reads:
                matched = False
        check("required_tools_and_core_parameters", True, matched and cursor == len(expected_core))
    # Derive approval correctness from persisted causal audit, never reply keywords.
    events = after["tables"]["events"]
    for refund in after["tables"]["refunds"] + after["tables"]["reships"]:
        decisions = [e for e in events if e["action_id"] == refund["action_id"] and e["event_type"] == "decision" and e["details"].get("approved")]
        check(f"approval_before_effect.{refund['action_id']}", True, bool(decisions))
    check("same_customer_preserved", before["order"]["customer_id"], after["order"]["customer_id"])
    return checks


async def run_case(case, *, baseline="B0", mode="deterministic", model=None):
    from sqlalchemy.ext.asyncio import create_async_engine
    from aftersales_delivery.business import Store
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="aftersales-upgrade-eval-") as directory:
        path = Path(directory) / "sandbox.sqlite"
        engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}", connect_args={"timeout": 30})
        try:
            await create_fixture(engine, case)
            before = await snapshot(engine)
            sandbox = Sandbox(Store(engine))
            if baseline == "B0":
                for step in input_plan(case):
                    await sandbox.invoke(step["tool"], step["args"])
                model_metrics = {"http_calls": 0, "total_tokens": 0, "reported_total_tokens": 0}
                rounds = len(case["user_turns"])
            else:
                from upgrade_eval.models import run_model_baseline
                call_start = model.calls
                try:
                    model_metrics, rounds = await run_model_baseline(case, sandbox, baseline, mode, model)
                except BudgetExceeded:
                    raise
                except Exception as exc:
                    # Preserve safe failure evidence and the true HTTP count. Never
                    # serialize exception bodies that may contain provider secrets.
                    sandbox.errors.append("MODEL_OR_GRAPH_FAILURE")
                    model_metrics = {"http_calls": model.calls - call_start if mode == "live" else 0,
                                     "total_tokens": None if mode == "live" else 0,
                                     "error_type": type(exc).__name__}
                    rounds = model.calls - call_start
            after = await snapshot(engine)
            checks = judge(case, before, after, sandbox, baseline)
            return {"case_id": case["case_id"], "condition": case["condition"], "category": case["category"],
                    "split": case["split"], "baseline": baseline, "mode": mode,
                    "evidence_type": "deterministic_structured_form" if baseline == "B0" else ("real_model" if mode == "live" else "scripted_mock_model"),
                    "initial_db": before, "actual_db": after, "user_turns": case["user_turns"],
                    "trace": sandbox.trace, "checks": checks, "passed": all(c["passed"] for c in checks if c.get("scored", True)),
                    "human_review": sandbox.human_review,
                    "security_blocked": case["category"] == "security" and bool(sandbox.errors),
                    "model": model_metrics, "rounds": rounds,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3)}
        finally:
            await engine.dispose()


def summarize(runs):
    latencies = sorted(r["latency_ms"] for r in runs)
    quantile = lambda q: latencies[max(0, math.ceil(q * len(latencies)) - 1)] if latencies else None
    tool_checks = [c for r in runs for c in r["checks"] if c["name"] in {"tool_names_and_arguments", "required_tools_and_core_parameters"} and c.get("scored", True)]
    calls = [t for r in runs for t in r["trace"]]
    tokens = [r["model"].get("total_tokens") for r in runs]
    security_runs = [r for r in runs if r.get("category") == "security"]
    return {"independent_cases": len({r["case_id"] for r in runs}), "runs": len(runs),
            "passed": sum(r["passed"] for r in runs),
            "success_rate": sum(r["passed"] for r in runs) / len(runs) if runs else None,
            "latency_p50_ms": quantile(.5), "latency_p95_ms": quantile(.95),
            "tool_argument_accuracy": sum(c["passed"] for c in tool_checks) / len(tool_checks) if tool_checks else None,
            "invalid_calls": sum(t.get("error") in {"UNKNOWN_TOOL", "INVALID_TOOL_ARGUMENTS"} for t in calls),
            "tool_calls": len(calls), "rounds": sum(r["rounds"] for r in runs),
            "model_http_calls": sum(r["model"].get("http_calls", 0) for r in runs),
            "total_tokens": None if any(t is None for t in tokens) else sum(tokens),
            "human_review_ratio": sum(r["human_review"] for r in runs) / len(runs) if runs else None,
            "approval_actor_ratio": sum(any(t["tool"] in {"approve", "reject"} for t in r["trace"]) for r in runs) / len(runs) if runs else None,
            "security_block_rate": sum(r["security_blocked"] for r in security_runs) / len(security_runs) if security_runs else None}


def source_hashes():
    paths = [CATALOG, Path(__file__), ROOT / "scripts/upgrade_eval/models.py",
             ROOT / "src/aftersales_delivery/business.py", ROOT / "src/aftersales_delivery/policies.py",
             ROOT / "src/aftersales_delivery/agent.py", ROOT / "src/aftersales_delivery/db.py", ROOT / "uv.lock"]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}


async def main_async(args):
    cases = load_cases()
    quality = verify_manifest(cases)
    if not quality["valid"]:
        raise ValueError("Frozen catalog manifest mismatch; do not silently refreeze")
    if args.catalog_only:
        print(json.dumps(quality, ensure_ascii=False, indent=2))
        return 0
    if args.live and args.all:
        raise ValueError("Live trials require bounded stratified selection, not --all")
    if args.live and args.baseline == "B0" and not args.compare:
        raise ValueError("B0 never calls a model; omit --live")
    eligible = cases
    if args.case_ids:
        ids = set(args.case_ids.split(","))
        eligible = [c for c in cases if c["case_id"] in ids]
        if {c["case_id"] for c in eligible} != ids:
            raise ValueError("Unknown --case-ids")
    baselines = ["B0", "B1", "B2", "B3"] if args.compare else [args.baseline]
    if args.compare or args.baseline != "B0":
        from upgrade_eval.models import model_eligible
        eligible = [c for c in eligible if model_eligible(c)]
    selected = select_cases(eligible, None if args.all else args.limit, args.split)
    if not selected:
        raise ValueError("No eligible cases selected")
    model = None
    if args.compare or args.baseline != "B0":
        from upgrade_eval.models import LiveModel, ScriptedModel
        model = LiveModel(ModelBudget(args.max_model_calls, args.max_tokens)) if args.live else ScriptedModel()
    metadata = {"started_at": datetime.now(UTC).isoformat(), "source_sha256": source_hashes(),
                "catalog": quality, "baselines": baselines,
                "mode": "live" if args.live else "deterministic", "selected_case_ids": [c["case_id"] for c in selected],
                "coverage_fraction": len(selected) / len(cases), "split": args.split or "all",
                "budget": {"max_http_calls": args.max_model_calls, "max_estimated_tokens": args.max_tokens},
                "model_parameters": {"model": getattr(model, "model_name", None), "temperature": 0, "max_output_tokens": 800,
                                     "input_estimate": "ceil(UTF-8 request bytes / 2) + output cap; not billed usage"},
                "scoring_protocol": "DB + persisted approval + required tool core args; safe extra reads allowed for model arms; exact full trace retained as diagnostic",
                "scope": "Temporary SQLite; Store policy boundary. No real payments, no MySQL/MCP/browser evidence."}
    destination = Path(args.output) if args.output else ROOT / "evaluation/upgrade/runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    runs = []
    for case in selected:
        count = 3 if args.repeat_core and case["core_repeat"] else 1
        for repeat in range(count):
            for baseline in baselines:
                try:
                    result = await run_case(case, baseline=baseline, mode=metadata["mode"], model=model)
                except BudgetExceeded:
                    metadata["stop_reason"] = "MODEL_BUDGET_EXHAUSTED"
                    break
                result["repetition"] = repeat + 1
                runs.append(result)
                (destination / f"{case['case_id']}-{baseline}-{repeat + 1}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            if metadata.get("stop_reason"):
                break
        if metadata.get("stop_reason"):
            break
    report = {"metadata": metadata, "summary": summarize(runs),
              "by_baseline": {b: summarize([r for r in runs if r["baseline"] == b]) for b in baselines},
              "executed_case_ids": sorted({r["case_id"] for r in runs}),
              "not_executed_case_ids": [c["case_id"] for c in cases if c["case_id"] not in {r["case_id"] for r in runs}],
              "model_budget": model.budget.summary() if args.live else {"http_calls": 0, "total_tokens": 0},
              "failed_cases": [r["case_id"] for r in runs if not r["passed"]]}
    (destination / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(destination), **report["summary"]}, ensure_ascii=False, indent=2))
    return 0 if runs and all(r["passed"] for r in runs) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-only", action="store_true")
    parser.add_argument("--all", action="store_true", help="Offline only: execute all frozen scenarios")
    parser.add_argument("--live", action="store_true", help="Explicitly authorize bounded model HTTP calls")
    parser.add_argument("--baseline", choices=["B0", "B1", "B2", "B3"], default="B0")
    parser.add_argument("--compare", action="store_true", help="Paired B0/B1/B2/B3 on identical fixtures, requests and budgets")
    parser.add_argument("--case-ids", help="Explicit frozen IDs, comma separated; avoids unreported convenience sampling")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--split", choices=["dev", "validation", "test"])
    parser.add_argument("--repeat-core", action="store_true")
    parser.add_argument("--max-model-calls", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, default=120000)
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
