import importlib
import sys
from pathlib import Path


def test_payload_conflict_has_a_clear_exit_without_echoing_request_details(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    backend = importlib.import_module("business.cases")
    assert hasattr(backend, "CaseRequestConflictError"), "Request conflict type is missing"
    launcher = importlib.import_module("create_case_with_request")

    async def conflicting_request(*_):
        raise backend.CaseRequestConflictError("private request contents")

    monkeypatch.setattr(launcher, "create_and_read_back", conflicting_request)
    monkeypatch.setattr(launcher, "get_mysql_password", lambda: "dummy-private-password")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "create_case_with_request.py",
            "--user",
            "offline",
            "--customer-id",
            "learner-001",
            "--order-id",
            "DEMO-001",
            "--request-id",
            "REQ-001",
        ],
    )

    assert launcher.main() == 1
    assert capsys.readouterr().out.strip() == "CASE_REQUEST_CONFLICT"
