"""Run local real-service browser checks without printing sandbox credentials."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", action="store_true", help="Opt in to bounded real-model Agent write flow")
    parser.add_argument("--url", default="http://127.0.0.1:18520")
    args = parser.parse_args()
    from urllib.parse import urlsplit
    assert urlsplit(args.url).hostname in {"127.0.0.1", "localhost"}
    path = ROOT / "output/delivery/credentials.txt"
    if not path.exists():
        path = ROOT.parent / "aftersales-agent/output/delivery/credentials.txt"
    credentials = json.loads(path.read_text("utf-8-sig"))
    env = dict(os.environ, E2E_BASE_URL=args.url, E2E_CUSTOMER_USERNAME="customer1",
        E2E_CUSTOMER_PASSWORD=credentials["customer1"], E2E_ADMIN_USERNAME="reviewer",
        E2E_ADMIN_PASSWORD=credentials["reviewer"], E2E_AGENT_WRITE="1" if args.agent else "0")
    saved = ROOT / "frontend/evidence/real-agent-chat/report.json"
    if saved.exists():
        task = json.loads(saved.read_text("utf-8"))
        env["E2E_RESTORE_THREAD"] = task.get("thread_id", "")
        env["E2E_RESTORE_CASE"] = task["steps"][-1]["task"]["proposal"]["result"]["case_id"]
    spec = "e2e/agent-chat.spec.ts" if args.agent else "e2e/platform.spec.ts"
    command = ["npx.cmd" if os.name == "nt" else "npx", "playwright", "test", spec, "--reporter=json"]
    run = subprocess.run(command, cwd=ROOT / "frontend", capture_output=True, text=True,
                         encoding="utf-8", env=env, timeout=300)
    text = run.stdout
    for secret in credentials.values():
        text = text.replace(secret, "[REDACTED]")
    report = json.loads(text)
    output = ROOT / "evaluation/upgrade/runtime" / ("production-agent-browser.json" if args.agent else "production-browser-read.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report.get("stats", {})))
    if run.returncode:
        print(text[-1800:])
    return run.returncode


if __name__ == "__main__":
    sys.exit(main())
