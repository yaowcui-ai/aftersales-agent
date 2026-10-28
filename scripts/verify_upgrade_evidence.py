"""Rejudge saved upgrade traces and database snapshots; no model or database calls."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def verify(directory):
    summary = json.loads((directory / "summary.json").read_text("utf-8"))
    frozen = directory / "frozen_source"
    evaluator = frozen / "scripts/evaluate_upgrade.py"
    if not evaluator.exists():
        evaluator = ROOT / "scripts/evaluate_upgrade.py"
    spec = importlib.util.spec_from_file_location("frozen_" + directory.name.replace("-", "_"), evaluator)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    case_path = frozen / "evaluation/upgrade/cases.json"
    if not case_path.exists():
        case_path = ROOT / "evaluation/upgrade/cases.json"
    cases = {case["case_id"]: case for case in json.loads(case_path.read_text("utf-8"))}
    records = []
    for path in sorted(directory.glob("U*.json")):
        row = json.loads(path.read_text("utf-8"))
        read_result = None
        errors = []
        manual = False
        for entry in row["trace"]:
            if entry.get("error"):
                errors.append(entry["error"])
            elif entry["tool"] in {"get_order", "get_shipments", "get_inventory", "list_orders", "get_case"}:
                read_result = entry.get("result")
            if isinstance(entry.get("result"), dict):
                manual |= bool(entry["result"].get("manual_review"))
        if row["model"].get("error_type"):
            errors.append("MODEL_OR_GRAPH_FAILURE")
        sandbox = SimpleNamespace(trace=row["trace"], read_result=read_result,
                                  errors=errors, human_review=manual)
        checks = module.judge(cases[row["case_id"]], row["initial_db"], row["actual_db"], sandbox, row["baseline"])
        assert checks == row["checks"], f"Rejudged checks differ: {directory.name}/{path.name}"
        passed = all(check["passed"] for check in checks if check.get("scored", True))
        assert passed == row["passed"], f"Outcome differs: {path.name}"
        records.append(row)
    actual = module.summarize(records)
    assert actual == summary["summary"], f"Summary differs: {directory.name}"
    if frozen.exists():
        for name, expected in summary["metadata"]["source_sha256"].items():
            file = frozen / name
            if file.exists():
                assert hashlib.sha256(file.read_bytes()).hexdigest() == expected, name
    return {"run": directory.name, "records": len(records), "passed": actual["passed"],
            "http_calls": actual["model_http_calls"], "tokens": actual["total_tokens"]}


def main():
    for name in ("v2-b0-final", "v2-fair-mock-final", "live-paired-six-frozen"):
        print(json.dumps(verify(ROOT / "evaluation/upgrade/runs" / name)))
    print("UPGRADE_EVIDENCE_REJUDGED_PASS")


if __name__ == "__main__":
    main()
