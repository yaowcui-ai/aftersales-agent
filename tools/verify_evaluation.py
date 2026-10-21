"""Recompute published outcomes without credentials, API requests, or model calls."""

import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(report_path):
    spec = importlib.util.spec_from_file_location(
        "delivery_evaluator", ROOT / "scripts/evaluate_delivery.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = {case["id"]: case for case in module.load_cases()}
    report = json.loads(report_path.read_text("utf-8"))
    assert report["mode"] == "live_llm" and report["complete"], "Incomplete or non-live report"
    assert report["source_unchanged"], "Source changed during evaluation"
    records = report["records"]
    assert len(records) == 144 and report["planned_attempts"] == 144
    expected = {(case, repetition) for case in cases for repetition in (1, 2, 3)}
    keys = [(row["case_id"], row["repetition"]) for row in records]
    assert len(set(keys)) == 144 and set(keys) == expected, "Missing/duplicate case repetition"
    jsonl = report_path.with_suffix(".jsonl")
    assert [json.loads(line) for line in jsonl.read_text("utf-8").splitlines() if line] == records
    for row in records:
        if "task" not in row or "evidence" not in row:
            assert not row["passed"], "Missing execution evidence cannot pass"
            continue
        passed, reasons = module.assess_case(cases[row["case_id"]], row["task"], row["evidence"])
        safe = "BUSINESS_EFFECT_BEFORE_APPROVAL" not in row.get("errors", [])
        assert row["passed"] == (passed and safe), f"Outcome mismatch: {row['case_id']}"
        assert row["reasons"] == reasons
    passed = sum(row["passed"] is True for row in records)
    assert report["summary"]["attempts"] == 144 and report["summary"]["passed"] == passed
    assert abs(report["summary"]["success_rate"] - passed / 144) < 1e-12
    for split, count in Counter(row["split"] for row in records).items():
        assert report["summary"]["by_split"][split]["attempts"] == count
    return {
        "report": report_path.name,
        "passed": passed,
        "total": 144,
        "rate": round(passed / 144 * 100, 1),
        "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
    }


if __name__ == "__main__":
    paths = [
        ROOT / "evaluation/results/historical-20261020.json",
        ROOT / "evaluation/results/latest.json",
    ]
    for path in paths:
        print(json.dumps(verify(path), ensure_ascii=False))
