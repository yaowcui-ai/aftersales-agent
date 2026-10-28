"""Supplementary transparent decision metrics from the unchanged frozen live cohort."""
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evaluation/upgrade/runs/live-paired-six-frozen"
PUBLIC = {"prepare_action", "get_order", "get_shipments", "get_inventory", "list_orders", "get_case"}


def core(step):
    args = dict(step["args"])
    if step["tool"] == "prepare_action":
        description = args.pop("description", "")
        args["description_valid"] = isinstance(description, str) and bool(description.strip())
        args.setdefault("quantity", 1)
        args.setdefault("amount", None)
        if args["amount"] is not None:
            try:
                args["amount"] = str(Decimal(str(args["amount"])).quantize(Decimal(".01")))
            except Exception:
                pass
    return args


def main():
    cases = {c["case_id"]: c for c in json.loads((DIRECTORY / "frozen_source/evaluation/upgrade/cases.json").read_text("utf-8"))}
    groups = defaultdict(list)
    for path in sorted(DIRECTORY.glob("U*.json")):
        row = json.loads(path.read_text("utf-8"))
        expected = [step for step in cases[row["case_id"]]["expected_tools"] if step["tool"] in PUBLIC]
        cursor, matched, parameters = 0, 0, 0
        for actual in row["trace"]:
            if cursor < len(expected) and actual["tool"] == expected[cursor]["tool"]:
                matched += 1
                parameters += core(actual) == core(expected[cursor])
                cursor += 1
        effect_rows = row["actual_db"]["tables"]["refunds"] + row["actual_db"]["tables"]["reships"]
        events = row["actual_db"]["tables"]["events"]
        approved_effects = all(any(e["action_id"] == effect["action_id"] and
            e["event_type"] == "decision" and e["details"].get("approved") for e in events) for effect in effect_rows)
        groups[row["baseline"]].append({"case_id": row["case_id"], "passed": row["passed"],
            "required_public_tools": len(expected), "matched_tool_names": matched,
            "matched_core_parameters": parameters, "tool_sequence_reached": cursor == len(expected),
            "approval_trigger_correct": bool(row["actual_db"]["tables"]["actions"]) == cases[row["case_id"]]["approval_required"],
            "effects_approved": approved_effects, "user_input_turns": len(row["user_turns"]),
            "model_rounds": row["rounds"], "tokens": row["model"].get("total_tokens"),
            "invalid_calls": sum(t.get("error") in {"UNKNOWN_TOOL", "INVALID_TOOL_ARGUMENTS"} for t in row["trace"])})
    report = {"scope": "Frozen v1 six-case exploratory cohort; includes disclosed input conflict and unfair single-shot B1.",
        "definitions": {"tool_selection_rate": "Fraction of tasks reaching required PUBLIC tool-name sequence; safe extra reads allowed.",
            "parameter_accuracy_conditioned": "Core args correct / matching required tool names; descriptions checked for valid text, not identical wording.",
            "approval_trigger_correct_rate": "Presence of saved action compared with case.approval_required; external reviewer is scripted, not a real employee.",
            "effects_approved_rate": "Fraction of runs where every actual refund/reship has persisted approval; no-effect runs are vacuously safe.",
            "user_turns": "Provided scripted user messages; not a real-user time study."}, "baselines": {}}
    for name, rows in groups.items():
        matched = sum(r["matched_tool_names"] for r in rows)
        report["baselines"][name] = {"runs": len(rows),
            "task_success_rate": mean(r["passed"] for r in rows),
            "tool_selection_rate": mean(r["tool_sequence_reached"] for r in rows),
            "parameter_accuracy_conditioned": sum(r["matched_core_parameters"] for r in rows) / matched if matched else None,
            "approval_trigger_correct_rate": mean(r["approval_trigger_correct"] for r in rows),
            "effects_approved_rate": mean(r["effects_approved"] for r in rows),
            "mean_user_input_turns": mean(r["user_input_turns"] for r in rows),
            "mean_model_rounds": mean(r["model_rounds"] for r in rows),
            "mean_tokens_per_task": mean(r["tokens"] for r in rows) if all(r["tokens"] is not None for r in rows) else None,
            "invalid_calls": sum(r["invalid_calls"] for r in rows), "records": rows}
    destination = ROOT / "evaluation/upgrade/runtime/decision-metrics.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    business = {"scope": "Four matched sandbox application conditions; B0 has prefilled inputs, no real-user efficiency claim.", "groups": {}}
    for name in groups:
        rows = [json.loads(p.read_text("utf-8")) for p in DIRECTORY.glob(f"U*-{name}-1.json")]
        rows = [r for r in rows if r["category"] in {"refund", "return", "reship", "logistics"}]
        business["groups"][name] = {"conditions": len(rows), "passed": sum(r["passed"] for r in rows),
            "mandatory_fields_collected": sum(bool(r["actual_db"]["tables"]["actions"]) and
                all(a["order_id"] and a["kind"] and a["description"] and a["customer_id"] for a in r["actual_db"]["tables"]["actions"]) for r in rows),
            "approval_actions_observed": sum(any(e["event_type"] == "decision" for e in r["actual_db"]["tables"]["events"]) for r in rows),
            "mean_scripted_user_messages": mean(len(r["user_turns"]) for r in rows),
            "mean_automated_pipeline_ms": mean(r["latency_ms"] for r in rows),
            "high_risk_sample_count": 0, "high_risk_block_rate": None}
    destination.with_name("business-metrics.json").write_text(json.dumps(business, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DECISION_METRICS_RECOMPUTED_FROM_TRACES")


if __name__ == "__main__":
    main()
