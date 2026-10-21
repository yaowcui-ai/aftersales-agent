"""Exercise the real isolated deployment, optionally restarting only its API container."""

import argparse
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def run(base_url, restart=False, compose_override=None):
    credentials = json.loads(
        (ROOT / "output/delivery/credentials.txt").read_text(encoding="utf-8-sig")
    )
    evidence = {"base_url": base_url, "real_model_and_http_mcp": True, "cases": []}
    with httpx.Client(base_url=base_url, trust_env=False, timeout=120) as client:

        def request(method, path, user=None, body=None):
            headers = {"Authorization": "Bearer " + tokens[user]} if user else {}
            return client.request(method, path, headers=headers, json=body)

        tokens = {}
        for username, password in credentials.items():
            response = client.post("/auth/login", json={"username": username, "password": password})
            assert response.status_code == 200, "LOGIN_FAILED"
            tokens[username] = response.json()["token"]
        del credentials
        assert request("GET", "/cases").status_code == 401
        run_id = uuid4().hex[:10]
        descriptions = [
            ("logistics", "请为订单 DEMO-001 申请物流异常工单，描述：模拟演示核实派送地址。", True),
            ("refund", "请为订单 DEMO-004 申请模拟退款10.00元，原因：差价退款。", True),
            ("reship", "请为订单 DEMO-001 申请模拟补发一件，原因：收到商品破损。", True),
            ("logistics", "请为订单 DEMO-002 申请物流异常工单，描述：模拟拒绝流程验证。", False),
        ]
        for index, (kind, message, approve) in enumerate(descriptions):
            thread = f"deploy-{run_id}-{index}"
            response = request(
                "POST", f"/tasks/{thread}/messages", "customer1", {"message": message}
            )
            assert response.status_code == 200, "MESSAGE_FAILED"
            task = response.json()
            assert task["status"] == "waiting_approval", "PROPOSAL_MISSING"
            action = task["proposal"]
            assert action["kind"] == kind and action["status"] == "waiting_approval"
            assert request("GET", f"/tasks/{thread}", "customer2").status_code == 404
            route = f"/approvals/{action['action_id']}/decision"
            decision = {"approved": approve, "version": action["version"]}
            assert request("POST", route, "customer1", decision).status_code == 403
            assert (
                request("POST", route, "reviewer", {**decision, "version": 999}).status_code == 409
            )
            if index == 0 and restart:
                command = [
                    "docker",
                    "compose",
                    "--env-file",
                    ".env.delivery",
                    "-f",
                    "compose.delivery.yaml",
                ]
                if compose_override:
                    command.extend(["-f", compose_override])
                command.extend(["restart", "api"])
                subprocess.run(
                    command,
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=True,
                    timeout=90,
                )
                for _ in range(60):
                    try:
                        if client.get("/health", timeout=2).status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(1)
                restored = request("GET", f"/tasks/{thread}", "customer1")
                assert (
                    restored.status_code == 200
                    and restored.json()["proposal"]["action_id"] == action["action_id"]
                )
                evidence["api_container_restart_restored_same_pending_action"] = True
            result = request("POST", route, "reviewer", decision)
            if result.status_code == 503:
                result = request("POST", f"/approvals/{action['action_id']}/retry", "reviewer")
            assert result.status_code == 200, "APPROVAL_OR_REPLY_FAILED"
            completed = result.json()
            assert completed["status"] == ("completed" if approve else "rejected")
            assert request("POST", route, "reviewer", decision).json() == completed, (
                "CACHED_REPLAY_CHANGED"
            )
            if approve:
                case_id = completed["proposal"]["result"]["case_id"]
                assert case_id in completed["final_answer"]
                assert request("GET", f"/cases/{case_id}", "customer2").status_code == 404
                if index == 0:
                    for status in ["processing", "resolved", "closed"]:
                        changed = request(
                            "POST",
                            f"/cases/{case_id}/status",
                            "reviewer",
                            {"status": status, "resolution": "模拟：已核实派送地址并完成处理。"},
                        )
                        assert changed.status_code == 200 and changed.json()["status"] == status
                events = request("GET", f"/actions/{action['action_id']}/events", "customer1")
                assert events.status_code == 200
                assert (
                    len([event for event in events.json() if event["event_type"] == "execute"]) == 1
                )
            evidence["cases"].append(completed)
            print(f"DEPLOYMENT_CASE_OK: {kind}, status={completed['status']}", flush=True)
    evidence["passed"] = True
    path = ROOT / "output/delivery/docker-end-to-end.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"DEPLOYMENT_VERIFIED: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--restart-api", action="store_true")
    parser.add_argument("--compose-override")
    args = parser.parse_args()
    try:
        run(args.base_url, args.restart_api, args.compose_override)
    except Exception as error:
        print(f"DEPLOYMENT_VERIFICATION_FAILED: {type(error).__name__}")
        raise SystemExit(1) from None
