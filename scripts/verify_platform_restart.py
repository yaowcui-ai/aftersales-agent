"""Real Docker MCP outage + API process replacement; sandbox only, bounded model reply."""
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ["docker", "compose", "--env-file", ".env.delivery", "-f", "compose.upgrade.yaml"]


def docker(*args):
    result = subprocess.run([*DOCKER, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if result.returncode:
        raise RuntimeError("DOCKER_OPERATION_FAILED")
    return result.stdout.strip()


def container_state():
    cid = docker("ps", "-q", "api")
    result = subprocess.run(["docker", "inspect", "--format", "{{json .State}}", cid],
                            capture_output=True, text=True, timeout=15)
    result.check_returncode()
    state = json.loads(result.stdout)
    return {"container_id": cid, "pid": state["Pid"], "started_at": state["StartedAt"]}


def main():
    credentials = json.loads((ROOT.parent / "aftersales-agent/output/delivery/credentials.txt").read_text("utf-8-sig"))
    run = "restart-" + uuid4().hex[:20]
    report = {"run_id": run, "time_utc": datetime.now(UTC).isoformat(), "success": False,
              "scope": "Real isolated Docker MySQL, independent MCP outage, API process replacement",
              "simulated_business": True, "steps": []}
    output = ROOT / "evaluation/upgrade/runtime" / (run + ".json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url="http://127.0.0.1:18100", timeout=150, trust_env=False) as client:
        headers = {}
        for name in ("customer1", "reviewer"):
            response = client.post("/auth/login", json={"username": name, "password": credentials[name]})
            response.raise_for_status()
            headers[name] = {"Authorization": "Bearer " + response.json()["token"]}
        before_order = client.get("/orders/UP-PAID", headers=headers["customer1"]).json()
        response = client.post("/requests", headers=headers["customer1"], json={
            "request_id": run, "kind": "refund", "order_id": "UP-PAID", "amount": "0.10",
            "description": "沙箱故障注入：MCP断开及API进程重启，原动作恢复。"})
        response.raise_for_status()
        task = response.json()
        aid, thread = task["proposal"]["action_id"], task["thread_id"]
        report["container_before"] = container_state()
        try:
            docker("stop", "mcp")
            failed = client.post(f"/approvals/{aid}/decision", headers=headers["reviewer"],
                json={"approved": True, "version": 1, "reason": "故障注入前已核验冻结参数"})
            assert failed.status_code >= 400
            saved = client.get(f"/tasks/{thread}", headers=headers["customer1"])
            saved.raise_for_status()
            state = saved.json()
            assert state["proposal"]["status"] == "approved" and state["next_nodes"]
            assert client.get("/orders/UP-PAID", headers=headers["customer1"]).json()["refunded_amount"] == before_order["refunded_amount"]
            frozen = state["proposal"]["approval_review"]["snapshot_hash"]
            report["steps"].append({"phase": "mcp_outage_after_approval", "http_status": failed.status_code,
                                    "response": failed.json(), "saved_task": state})
            docker("up", "-d", "--no-deps", "--no-build", "--force-recreate", "--wait", "api", "mcp")
            report["container_after"] = container_state()
            assert report["container_before"]["container_id"] != report["container_after"]["container_id"]
            restored = client.get(f"/tasks/{thread}", headers=headers["customer1"]).json()
            assert restored["proposal"]["approval_review"]["snapshot_hash"] == frozen
            result = client.post(f"/approvals/{aid}/retry", headers=headers["reviewer"])
            report["steps"].append({"phase": "recover_original_action", "http_status": result.status_code,
                                    "response": result.json()})
            result.raise_for_status()
            task = result.json()
            assert task["status"] == "completed"
            repeated = client.post(f"/approvals/{aid}/retry", headers=headers["reviewer"])
            repeated.raise_for_status()
            assert repeated.json()["proposal"]["result"] == task["proposal"]["result"]
            after_order = client.get("/orders/UP-PAID", headers=headers["customer1"]).json()
            from decimal import Decimal
            assert Decimal(after_order["refunded_amount"]) - Decimal(before_order["refunded_amount"]) == Decimal("0.10")
            report.update(success=True, action_id=aid, thread_id=thread,
                before_order=before_order, after_order=after_order,
                model_reply_calls=task["metrics"].get("reply_calls"),
                model_tokens=task["metrics"].get("total_tokens"),
                final_result=task["proposal"]["result"],
                idempotent_replay=True, frozen_snapshot_preserved=True)
            assert report["model_reply_calls"] <= 3
            print("REAL_PROCESS_RESTART_RECOVERY_PASS")
        except Exception as error:
            report["failure_type"] = type(error).__name__
            print("REAL_PROCESS_RESTART_RECOVERY_FAILED: " + type(error).__name__)
            raise
        finally:
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            docker("up", "-d", "--no-deps", "--no-build", "--wait", "api", "mcp")


if __name__ == "__main__":
    main()
