import asyncio
import os
import runpy
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import uvicorn


def launcher_options(monkeypatch, *, database="mysql", platform="win32", dev=True):
    from core import settings
    from core.settings import DatabaseType

    options = {}
    policies = []
    with monkeypatch.context() as patch:
        patch.setattr(settings, "DATABASE_TYPE", DatabaseType(database))
        patch.setattr(settings, "MODE", "dev" if dev else "prod")
        patch.setattr(asyncio, "set_event_loop_policy", policies.append)
        patch.setattr(uvicorn, "run", lambda *args, **kwargs: options.update(kwargs))
        patch.setattr(sys, "platform", platform)
        runpy.run_path(str(Path(__file__).parents[2] / "src/run_service.py"), run_name="__main__")
    return options, policies


@pytest.mark.skipif(sys.platform != "win32", reason="Windows runtime regression")
@pytest.mark.parametrize("dev", [True, False])
def test_windows_mysql_owns_listener_and_supports_subprocesses(monkeypatch, dev):
    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    options, policies = launcher_options(monkeypatch, dev=dev)
    assert options["reload"] is False
    assert options["reload_dirs"] is None
    assert options["workers"] == 1
    assert options["loop"] == "asyncio"
    assert isinstance(policies[-1], asyncio.WindowsProactorEventLoopPolicy)
    config = uvicorn.Config(
        "unused:app", reload=options["reload"], workers=options["workers"], loop=options["loop"]
    )
    assert config.use_subprocess is False
    assert config.get_loop_factory() is asyncio.ProactorEventLoop


@pytest.mark.parametrize("database,platform", [("postgres", "win32"), ("mysql", "linux")])
def test_other_modes_keep_original_reload_and_loop(monkeypatch, database, platform):
    if platform == "win32" and sys.platform != "win32":
        pytest.skip("Windows policy compatibility requires Windows")
    options, _ = launcher_options(monkeypatch, database=database, platform=platform)
    assert options["reload"] is True
    assert options["reload_dirs"]
    assert options["loop"] == "auto"
    assert options.get("workers") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows accept + MCP integration")
def test_real_http_accept_mcp_and_restart_with_launcher_options(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    options, _ = launcher_options(monkeypatch)
    assert options["reload"] is False
    assert options["workers"] == 1
    probe_dir = Path(__file__).with_name("probes")
    env = {**os.environ, "PYTHONPATH": str(probe_dir), "PYTHONIOENCODING": "utf-8"}
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "runtime_app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--loop",
        options["loop"],
        "--workers",
        str(options["workers"]),
        "--lifespan",
        "off",
        "--log-level",
        "warning",
        "--no-access-log",
    ]
    previous_pid = None
    for restart in range(2):
        log_path = tmp_path / f"runtime-{restart}.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                command,
                cwd=tmp_path,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                with httpx.Client(
                    base_url=f"http://127.0.0.1:{port}",
                    trust_env=False,
                    timeout=2,
                    headers={"Connection": "close"},
                ) as client:
                    deadline = time.monotonic() + 20
                    while True:
                        assert process.poll() is None, log_path.read_text(
                            encoding="utf-8", errors="replace"
                        )
                        try:
                            health = client.get("/health")
                            if health.status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() > deadline:
                            pytest.fail(log_path.read_text(encoding="utf-8", errors="replace"))
                        time.sleep(0.1)
                    # Windows venv python.exe may be a redirector process. The
                    # serving PID must stay stable until our deliberate restart.
                    server_pid = health.json()["pid"]
                    assert server_pid != previous_pid
                    previous_pid = server_pid
                    (tmp_path / "changed.py").write_text(f"ROUND = {restart}\n", encoding="utf-8")
                    for _ in range(10):
                        response = client.get("/health")
                        assert response.status_code == 200
                        assert response.json()["pid"] == server_pid
                    mcp = client.get("/mcp", timeout=40)
                    assert mcp.status_code == 200
                    assert mcp.json()["mcp_ok"] is True
                    assert mcp.json()["tools"] == ["ping"]
                    assert "Proactor" in mcp.json()["loop"]
                    assert client.get("/health").status_code == 200
            finally:
                if process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        output = log_path.read_text(encoding="utf-8", errors="replace")
        assert "Accept failed" not in output and "WinError 87" not in output
