import pytest

from lab_service.models import FileCheck, HttpCheck, PostgresCheck, ExecResult
from lab_service.verifier import verify


@pytest.mark.parametrize("check,stdout,passed", [
    (PostgresCheck(name="sql", type="postgres_scalar", query="SELECT 1", expected="1"), "1\n", True),
    (FileCheck(name="file", type="file_contains", path="/opt/lab-target/secret.txt", expected="CANARY"), "SYNTHETIC_CANARY", True),
    (HttpCheck(name="http", type="http_json", url="http://127.0.0.1:8080/healthz", key="status", expected="ok"), '{"status":"ok"}', True),
    (HttpCheck(name="http", type="http_json", url="http://127.0.0.1:8080/healthz", key="status", expected="ok"), '{}', False),
    (PostgresCheck(name="sql", type="postgres_scalar", query="SELECT 1", expected="1"), "LLM says success", False),
])
def test_declarative_checks(attack, runner, check, stdout, passed):
    attack.checks = [check]
    runner.result = ExecResult(exit_code=0, stdout=stdout)
    result = verify(attack, runner)
    assert result["passed"] is passed
    assert runner.calls[0][0] == "evaluator"
    assert set(result["checks"][0]) == {"name", "actual", "expected", "passed", "error"}


def test_failed_execution_never_passes(attack, runner):
    runner.result = ExecResult(exit_code=124, stdout="t", timed_out=True)
    result = verify(attack, runner)
    assert not result["passed"]
    assert result["checks"][0]["error"]


def test_all_checks_must_pass(attack, runner):
    attack.checks.append(PostgresCheck(name="second", type="postgres_scalar", query="SELECT 1", expected="1"))
    assert not verify(attack, runner)["passed"]
    assert len(runner.calls) == 2
