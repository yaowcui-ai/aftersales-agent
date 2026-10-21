import importlib
import os
import sys


def module():
    return importlib.import_module("aftersales.mcp_client")


def parameters(customer_id="learner-001"):
    return module().build_order_server_parameters(
        host="127.0.0.1",
        port=3306,
        username="root",
        password="PRIVATE_PASSWORD_MARKER",
        customer_id=customer_id,
    )


def test_child_parameters_keep_credentials_out_of_command_arguments():
    result = parameters()
    assert result.command == sys.executable
    assert result.args == ["-m", "aftersales.mcp_server"]
    assert "PRIVATE_PASSWORD_MARKER" not in " ".join([result.command, *result.args])
    assert result.env["AFTERSALES_MYSQL_PASSWORD"] == "PRIVATE_PASSWORD_MARKER"
    assert result.env["AFTERSALES_MYSQL_USER"] == "root"
    assert result.env["AFTERSALES_CUSTOMER_ID"] == "learner-001"


def test_each_customer_gets_independent_child_context_without_changing_parent(monkeypatch):
    monkeypatch.setenv("AFTERSALES_CUSTOMER_ID", "parent-context")
    first = parameters("learner-001")
    second = parameters("learner-002")
    assert first.env is not second.env
    assert first.env["AFTERSALES_CUSTOMER_ID"] == "learner-001"
    assert second.env["AFTERSALES_CUSTOMER_ID"] == "learner-002"
    assert os.environ["AFTERSALES_CUSTOMER_ID"] == "parent-context"


def test_nested_errors_report_types_without_process_environment_details(capsys):
    marker = "PRIVATE_ENV_MARKER"
    error = ExceptionGroup(marker, [RuntimeError(marker), ValueError(marker)])
    module().report_mcp_error(error)
    output = capsys.readouterr().out
    assert "RuntimeError" in output and "ValueError" in output
    assert marker not in output
