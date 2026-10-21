import importlib
import os
from pathlib import Path

import pytest


def load_config(monkeypatch, project_root):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    monkeypatch.delenv("MYSQL_PASSWORD", raising=False)
    module = importlib.import_module("_project_config")
    monkeypatch.setattr(module, "PROJECT_ROOT", project_root)

    def unexpected_prompt(_prompt):
        pytest.fail("A configured password must not prompt for input.")

    monkeypatch.setattr(module, "getpass", unexpected_prompt)
    return module


def test_uses_fixed_project_env_instead_of_current_directory(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / ".env").write_text("MYSQL_PASSWORD=project-marker\n", encoding="utf-8")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / ".env").write_text("MYSQL_PASSWORD=wrong-marker\n", encoding="utf-8")
    monkeypatch.chdir(unrelated)
    config = load_config(monkeypatch, project_root)
    assert config.get_mysql_password() == "project-marker"


def test_process_env_takes_precedence(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("MYSQL_PASSWORD=file-marker\n", encoding="utf-8")
    config = load_config(monkeypatch, tmp_path)
    monkeypatch.setenv("MYSQL_PASSWORD", "process-marker")
    assert config.get_mysql_password() == "process-marker"


def test_preserves_literals_whitespace_and_bom(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        'MYSQL_PASSWORD="  literal:${UNRELATED_VALUE}:$value@#  "\n', encoding="utf-8-sig"
    )
    monkeypatch.setenv("UNRELATED_VALUE", "must-not-expand")
    config = load_config(monkeypatch, tmp_path)
    assert config.get_mysql_password() == "  literal:${UNRELATED_VALUE}:$value@#  "


@pytest.mark.parametrize("source", ["env", "file"])
def test_explicit_empty_password_is_configured(monkeypatch, tmp_path, source):
    config = load_config(monkeypatch, tmp_path)
    if source == "env":
        monkeypatch.setenv("MYSQL_PASSWORD", "")
    else:
        (tmp_path / ".env").write_text("MYSQL_PASSWORD=\n", encoding="utf-8")
    assert config.get_mysql_password() == ""


@pytest.mark.parametrize("contents", [None, "OTHER_SETTING=example\n", "MYSQL_PASSWORD\n"])
def test_only_missing_password_falls_back_to_hidden_prompt(monkeypatch, tmp_path, contents):
    if contents is not None:
        (tmp_path / ".env").write_text(contents, encoding="utf-8")
    config = load_config(monkeypatch, tmp_path)
    calls = []

    def prompt(message):
        calls.append(message)
        return "manual-marker"

    monkeypatch.setattr(config, "getpass", prompt)
    assert config.get_mysql_password() == "manual-marker"
    assert calls == ["MySQL password (hidden): "]


def test_password_read_does_not_print_or_load_other_values(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("UNRELATED_CONFIG_MARKER", raising=False)
    (tmp_path / ".env").write_text(
        "MYSQL_PASSWORD=file-marker\nUNRELATED_CONFIG_MARKER=private-marker\n", encoding="utf-8"
    )
    config = load_config(monkeypatch, tmp_path)
    assert config.get_mysql_password() == "file-marker"
    assert "MYSQL_PASSWORD" not in os.environ
    assert "UNRELATED_CONFIG_MARKER" not in os.environ
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
