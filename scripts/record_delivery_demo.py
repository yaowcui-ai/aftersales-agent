"""Verify the real Streamlit workflow and optionally record a local Edge WebM.

Run with an isolated Playwright installation; this script never installs packages,
changes the application, or approves an action outside the task it just created.
"""

import argparse
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
EDGE = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
PENDING = {"waiting_approval", "pending_approval", "awaiting_approval", "pending"}


class DemoFailure(RuntimeError):
    """A deliberately public error that contains no request secrets."""


class Api:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")
        self.http = build_opener(ProxyHandler({}))
        self.token = None

    def request(self, method, path, body=None):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with self.http.open(request, timeout=15) as response:
                return json.load(response)
        except HTTPError as error:
            raise DemoFailure(f"Local API returned HTTP {error.code}.") from None
        except (URLError, OSError, ValueError):
            raise DemoFailure("Local API was unavailable or returned invalid JSON.") from None

    def login(self, password):
        result = self.request(
            "POST", "/auth/login", {"username": "customer1", "password": password}
        )
        self.token = result.get("token")
        if not self.token:
            raise DemoFailure("Customer API authentication did not return a token.")

    def task(self, thread_id):
        return self.request("GET", f"/tasks/{quote(thread_id, safe='')}")


def local_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise argparse.ArgumentTypeError("Use a local HTTP URL for this demo.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("URL must not contain credentials, query or fragment.")
    return value.rstrip("/")


def options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", type=local_url, default="http://127.0.0.1:8510")
    parser.add_argument("--api-url", type=local_url, default="http://127.0.0.1:8090")
    parser.add_argument("--output", type=Path, default=ROOT / "output/delivery/demo")
    parser.add_argument(
        "--duration", type=int, default=185, help="Minimum video seconds (default 185)."
    )
    parser.add_argument(
        "--no-video", action="store_true", help="Fast UI smoke test and screenshots."
    )
    parser.add_argument(
        "--query-only", action="store_true", help="Read-only customer query; no approval."
    )
    parser.add_argument("--edge", type=Path, default=EDGE)
    args = parser.parse_args()
    if args.duration < 0 or args.duration > 600:
        parser.error("--duration must be between 0 and 600 seconds")
    return args


def run(args):
    # Keep --help and Python syntax checks usable without the optional package.
    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError:
        raise DemoFailure("Playwright is missing; use the isolated Playwright runner.") from None
    if not args.edge.is_file():
        raise DemoFailure("Microsoft Edge executable was not found.")
    try:
        credentials = json.loads((ROOT / "output/delivery/credentials.txt").read_text("utf-8-sig"))
        if not all(isinstance(credentials.get(name), str) for name in ("customer1", "reviewer")):
            raise ValueError()
    except (OSError, ValueError):
        raise DemoFailure(
            "Local generated customer/reviewer credentials are unavailable."
        ) from None

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = args.output.resolve() / f"demo-{stamp}-{uuid4().hex[:6]}"
    destination.mkdir(parents=True, exist_ok=False)
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "ui_url": args.url,
        "api_url": args.api_url,
        "mode": "query_only" if args.query_only else "logistics_approval",
        "video_requested": not args.no_video,
        "success": False,
        "steps": [],
        "screenshots": [],
        "page_error_count": 0,
    }
    started = time.monotonic()
    page = None
    context = None
    browser = None
    video = None
    recording_started = None
    playwright = None

    def step(name):
        report["steps"].append(
            {"name": name, "elapsed_seconds": round(time.monotonic() - started, 2)}
        )
        print(f"Demo: {name}", flush=True)

    def screenshot(name):
        # Locator screenshots never include request headers or browser storage.
        path = destination / f"{name}.png"
        page.screenshot(path=str(path), full_page=True)
        report["screenshots"].append(str(path))

    def pause(seconds):
        if not args.no_video:
            page.wait_for_timeout(seconds * 1000)

    def clean_page():
        expect(page.get_by_test_id("stException")).to_have_count(0)

    def login(username, section):
        expect(page.get_by_role("heading", name="登录", exact=True)).to_be_visible(timeout=30_000)
        page.get_by_role("textbox", name="用户名", exact=True).fill(username)
        try:
            page.get_by_label("密码", exact=True).fill(credentials[username])
        except Exception:
            # Playwright may include fill arguments in its call log; never print those.
            raise DemoFailure("The password field could not be completed.") from None
        page.get_by_role("button", name="登录", exact=True).click()
        expect(page.get_by_role("heading", name=section, exact=True)).to_be_visible(timeout=45_000)
        expect(page.get_by_text(f"当前账号：{username}", exact=True)).to_be_visible()
        clean_page()

    def logout():
        page.get_by_role("button", name="退出登录", exact=True).click()
        expect(page.get_by_role("heading", name="登录", exact=True)).to_be_visible(timeout=30_000)

    def restore(thread_id):
        page.get_by_text("恢复已有任务", exact=True).click()
        page.get_by_role("textbox", name="已保存的任务编号", exact=True).fill(thread_id)
        page.get_by_role("button", name="打开任务", exact=True).click()
        expect(page.get_by_text(re.compile(rf"^任务编号：{re.escape(thread_id)}"))).to_be_visible(
            timeout=45_000
        )

    api = Api(args.api_url)
    try:
        api.request("GET", "/health")
        api.login(credentials["customer1"])
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(
            executable_path=str(args.edge), headless=True, args=["--no-proxy-server"]
        )
        context_options = {
            "viewport": {"width": 1440, "height": 1000},
            "locale": "zh-CN",
            "reduced_motion": "reduce",
        }
        if not args.no_video:
            context_options.update(
                record_video_dir=str(destination),
                record_video_size={"width": 1280, "height": 900},
            )
        try:
            context = browser.new_context(**context_options)
            page = context.new_page()
            recording_started = time.monotonic()
        except Exception:
            raise DemoFailure(
                "Browser context failed; for video ensure Playwright ffmpeg is installed."
            ) from None
        page.set_default_timeout(20_000)
        page.on(
            "pageerror",
            lambda _: report.__setitem__("page_error_count", report["page_error_count"] + 1),
        )
        video = page.video
        page.goto(args.url, wait_until="domcontentloaded", timeout=45_000)
        expect(page.get_by_role("heading", name="售后服务工作台", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="登录", exact=True)).to_be_visible()
        screenshot("01-login")
        step("real_login_page_loaded")
        pause(8)

        login("customer1", "我的售后对话")
        screenshot("02-customer-workspace")
        step("customer_authenticated_in_ui")
        pause(10)
        prompt = (
            "请查询订单 DEMO-001 的最新物流，仅查询并解释物流情况，不要创建售后申请。"
            if args.query_only
            else "请查询订单 DEMO-001 的物流，如果有异常，请帮我提出物流工单申请。"
        )
        chat = page.get_by_placeholder("描述您的售后问题", exact=True)
        chat.fill(prompt)
        chat.press("Enter")
        caption = page.get_by_text(re.compile(r"^任务编号：delivery-"))
        expect(caption).to_be_visible(timeout=180_000)
        match = re.search(r"任务编号：(delivery-[0-9a-f-]{36})", caption.inner_text())
        if not match:
            raise DemoFailure("The UI did not show a valid generated task ID.")
        thread_id = match.group(1)
        report["thread_id"] = thread_id
        task = api.task(thread_id)
        if task.get("customer_id") != "learner-001":
            raise DemoFailure("The created task has an unexpected customer identity.")
        action = task.get("proposal")
        if not args.query_only:
            if not action or action.get("status") not in PENDING:
                raise DemoFailure("The real model did not produce a pending logistics proposal.")
            if (
                action.get("thread_id") != thread_id
                or action.get("customer_id") != "learner-001"
                or action.get("kind") != "logistics"
                or action.get("order_id") != "DEMO-001"
            ):
                raise DemoFailure("Proposal identity or type did not match this demo task.")
            report["action_id"] = action["action_id"]
            expect(
                page.get_by_text(
                    "等待审批：当前仅保存了申请，尚未执行退款、补发或创建实际工单。", exact=True
                )
            ).to_be_visible()
        elif action:
            raise DemoFailure("A query-only demonstration unexpectedly produced an action.")
        elif task.get("status") != "answered" or not task.get("final_answer"):
            raise DemoFailure("The real model did not complete the query-only response.")
        clean_page()
        screenshot("03-customer-query-or-proposal")
        step("customer_real_model_response_received")
        pause(18)
        page.get_by_text("工具轨迹与运行指标", exact=True).click()
        page.get_by_text("工具轨迹与运行指标", exact=True).scroll_into_view_if_needed()
        screenshot("04-real-tool-trace")
        pause(16)
        page.get_by_role("heading", name="售后服务工作台", exact=True).scroll_into_view_if_needed()
        logout()
        login("reviewer", "待审批申请")
        step("reviewer_authenticated_in_ui")

        if action and not args.query_only:
            action_id = action["action_id"]
            own_box = (
                page.get_by_test_id("stExpander")
                .filter(has=page.locator("summary").filter(has_text=action_id))
                .filter(has=page.get_by_role("button", name="批准", exact=True))
            )
            expect(own_box).to_have_count(1)
            expect(own_box).to_contain_text("DEMO-001")
            expect(own_box).to_contain_text("learner-001")
            own_box.scroll_into_view_if_needed()
            screenshot("05-reviewer-own-proposal")
            pause(22)
            # The sole write outside customer chat is scoped to the exact action
            # read back from this freshly created, authenticated customer task.
            own_box.get_by_role("button", name="批准", exact=True).click()
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                task = api.task(thread_id)
                saved = task.get("proposal") or {}
                if (
                    saved.get("status") == "completed"
                    and saved.get("result")
                    and task.get("status") in {"completed", "reply_failed", "human_required"}
                ):
                    break
                if saved.get("status") in {"failed", "rejected"}:
                    raise DemoFailure("The demo's approved action did not complete.")
                page.wait_for_timeout(1000)
            else:
                raise DemoFailure("The demo action did not complete before the timeout.")
            report["task_status"] = task.get("status")
            report["action_status"] = saved["status"]
            report["case_id"] = saved["result"].get("case_id")
            if task.get("status") != "completed":
                raise DemoFailure(
                    "Business result was saved, but the final customer reply did not complete."
                )
            expect(page.get_by_text("最近审批 / 恢复结果", exact=True)).to_be_visible(
                timeout=90_000
            )
            clean_page()
            screenshot("06-approval-persisted-result")
            step("only_demo_action_approved_and_persisted")
            pause(18)
        else:
            screenshot("05-reviewer-workspace-read-only")
            pause(18)

        page.get_by_role("tab", name="工单进度", exact=True).click()
        expect(page.get_by_role("heading", name="售后工单", exact=True)).to_be_visible()
        if report.get("case_id"):
            case_box = page.get_by_test_id("stExpander").filter(
                has=page.locator("summary").filter(has_text=report["case_id"])
            )
            expect(case_box).to_have_count(1)
            case_box.locator("summary").click()
            case_box.scroll_into_view_if_needed()
        screenshot("07-reviewer-case-progress")
        step("real_case_progress_viewed_without_editing")
        pause(18)
        logout()
        login("customer1", "我的售后对话")
        restore(thread_id)
        if action and not args.query_only:
            expect(page.get_by_text("业务执行结果已保存", exact=True)).to_be_visible()
        clean_page()
        screenshot("08-customer-restored-task")
        step("saved_customer_task_restored_after_new_login")
        pause(18)
        page.get_by_role("tab", name="工单进度", exact=True).click()
        expect(page.get_by_role("heading", name="售后工单", exact=True)).to_be_visible()
        if report.get("case_id"):
            customer_case = page.get_by_test_id("stExpander").filter(
                has=page.locator("summary").filter(has_text=report["case_id"])
            )
            expect(customer_case).to_have_count(1)
            customer_case.locator("summary").click()
            customer_case.scroll_into_view_if_needed()
        screenshot("09-customer-cases")
        step("customer_case_list_verified")
        if report["page_error_count"]:
            raise DemoFailure("The browser emitted JavaScript page errors.")
        if not args.no_video:
            remaining = args.duration - (time.monotonic() - recording_started)
            if remaining > 0:
                page.wait_for_timeout(int(remaining * 1000))
        report["success"] = True
        step("ui_workflow_verified")
        context.close()
        context = None
        if video:
            video_path = destination / "aftersales-delivery-demo.webm"
            video.save_as(str(video_path))
            original = Path(video.path())
            if original != video_path and original.is_file():
                original.unlink()
            report["video"] = str(video_path)
            report["recording_seconds"] = round(time.monotonic() - recording_started, 2)
        browser.close()
        browser = None
    except Exception as error:
        report["success"] = False
        if page:
            try:
                screenshot("failure-state")
            except Exception:
                pass
        report["error"] = (
            str(error)
            if isinstance(error, DemoFailure)
            else f"UI assertion or browser operation failed ({type(error).__name__})."
        )
        if isinstance(error, AssertionError):
            details = str(error)[:3000]
            for secret in (*credentials.values(), api.token):
                if secret:
                    details = details.replace(secret, "[redacted]")
            report["assertion_detail"] = details
        raise DemoFailure(report["error"]) from None
    finally:
        if context:
            try:
                context.close()
            except Exception:
                pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["finished_at"] = datetime.now(UTC).isoformat()
        (destination / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )
        print(f"Sanitized demo report: {destination / 'report.json'}", flush=True)
    return report


if __name__ == "__main__":
    try:
        run(options())
    except DemoFailure as error:
        print(f"Demo failed: {error}", file=sys.stderr)
        sys.exit(1)
