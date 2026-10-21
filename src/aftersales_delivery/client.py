"""Small synchronous HTTP client shared by the delivery Streamlit application.

Writes are sent exactly once. A timeout can mean the server committed successfully;
callers must read saved state before deciding whether another action is appropriate.
"""

import re
from typing import Any
from urllib.parse import quote, urlsplit

import httpx


class DeliveryClientError(Exception):
    def __init__(self, code: str, message: str, status_code: int | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _safe_message(message: Any, fallback: str) -> str:
    """Keep a concise public API message; never expose tracebacks or credentials."""
    if not isinstance(message, str) or not message.strip():
        return fallback
    if re.search(
        r"traceback|https?://|mysql://|password|secret|token|bearer|api[_ -]?key", message, re.I
    ):
        return fallback
    return message.strip()[:300]


class DeliveryClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8090",
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        try:
            parsed = urlsplit(base_url)
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and not parsed.username
                and not parsed.password
            )
            parsed.port  # Validate malformed/out-of-range ports before opening a client.
            httpx.URL(base_url)
        except (ValueError, httpx.InvalidURL):
            valid = False
        if not valid:
            raise DeliveryClientError(
                "invalid_url", "API 地址应为不含账号密码的 HTTP 或 HTTPS 地址。"
            )
        self.token = token
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=120.0,
            trust_env=False,
            transport=transport,
            follow_redirects=False,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self._http.close()

    def _request(self, method: str, path: str, body: dict | None = None, *, params=None) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        fallback = "服务暂时无法完成请求。请先读取已保存状态，再决定下一步。"
        try:
            response = self._http.request(method, path, json=body, headers=headers, params=params)
        except httpx.TimeoutException:
            raise DeliveryClientError(
                "timeout", "请求超时。请先读取已保存状态，操作可能已成功。"
            ) from None
        except httpx.RequestError:
            raise DeliveryClientError(
                "connection_error", "无法连接服务。请检查 API 地址并读取已保存状态。"
            ) from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if not response.is_success:
            detail = data.get("detail") if isinstance(data, dict) else None
            code = f"http_{response.status_code}"
            message = fallback
            if isinstance(detail, dict):
                candidate = detail.get("code")
                if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", candidate):
                    code = candidate
                message = _safe_message(detail.get("message"), fallback)
            elif response.status_code == 401:
                message = "登录信息无效或已过期，请重新登录。"
            elif response.status_code == 403:
                message = "当前账号没有此操作权限。"
            raise DeliveryClientError(code, message, response.status_code)
        if not isinstance(data, (dict, list)):
            raise DeliveryClientError("invalid_response", "服务响应格式异常。请先读取已保存状态。")
        return data

    @staticmethod
    def _id(value: str) -> str:
        return quote(str(value), safe="")

    def login(self, username: str, password: str) -> dict:
        return self._request("POST", "/auth/login", {"username": username, "password": password})

    def me(self) -> dict:
        return self._request("GET", "/me")

    def send_message(self, thread_id: str, message: str) -> dict:
        return self._request("POST", f"/tasks/{self._id(thread_id)}/messages", {"message": message})

    def get_task(self, thread_id: str, *, customer_id: str | None = None) -> dict:
        return self._request(
            "GET",
            f"/tasks/{self._id(thread_id)}",
            params={"customer_id": customer_id} if customer_id else None,
        )

    def list_cases(self) -> list | dict:
        return self._request("GET", "/cases")

    def get_case(self, case_id: str) -> dict:
        return self._request("GET", f"/cases/{self._id(case_id)}")

    def list_approvals(self) -> list | dict:
        return self._request("GET", "/approvals")

    def decide(self, action_id: str, *, approved: bool, version: int) -> dict:
        return self._request(
            "POST",
            f"/approvals/{self._id(action_id)}/decision",
            {"approved": approved, "version": version},
        )

    def retry(self, action_id: str) -> dict:
        return self._request("POST", f"/approvals/{self._id(action_id)}/retry")

    def set_case_status(self, case_id: str, status: str, resolution: str) -> dict:
        return self._request(
            "POST",
            f"/cases/{self._id(case_id)}/status",
            {"status": status, "resolution": resolution},
        )
