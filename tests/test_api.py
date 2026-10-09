import pytest
from pydantic import ValidationError

from lab_service.config import Settings
from lab_service.models import ExecResult

BASE = "/v1/attacks/context-poisoning"
E = {"X-Lab-Token": "e" * 32}
S = {"X-Lab-Token": "s" * 32}


@pytest.mark.parametrize("route,headers,status", [
    ("sandbox/exec", S, 200), ("sandbox/exec", E, 403),
    ("evaluator/exec", E, 200), ("evaluator/exec", S, 403),
    ("sandbox/exec", {}, 403), ("evaluator/exec", {}, 403),
])
def test_role_separation(client, route, headers, status):
    assert client.post(f"{BASE}/{route}", headers=headers, json={"command": "id"}).status_code == status


def test_sandbox_only_runner(client, runner):
    assert client.post(BASE + "/sandbox/exec", headers=S, json={"command": "id"}).status_code == 200
    assert [call[0] for call in runner.calls] == ["sandbox"]


@pytest.mark.parametrize("route", ["up", "down", "verify"])
def test_evaluator_only_operations(client, route):
    assert client.post(f"{BASE}/{route}", headers=S).status_code == 403
    assert client.post(f"{BASE}/{route}", headers=E).status_code == 200


@pytest.mark.parametrize("path", ["/v1/attacks", BASE])
def test_discovery_evaluator_only(client, path):
    assert client.get(path, headers=S).status_code == 403
    assert client.get(path, headers=E).status_code == 200


def test_missing_attack(client):
    assert client.get("/v1/attacks/no-such-attack", headers=E).status_code == 404


def test_invalid_request(client):
    assert client.post(BASE + "/sandbox/exec", headers=S,
                       json={"command": "id", "timeout_seconds": 0}).status_code == 422


def test_lifecycle_failure(client, runner):
    runner.result = ExecResult(exit_code=1, stderr="box unavailable")
    response = client.post(BASE + "/up", headers=E)
    assert response.status_code == 502
    assert "box unavailable" in response.text


def test_missing_vagrant(client, runner):
    def fail(_):
        raise FileNotFoundError("vagrant not installed")
    runner.up = fail
    assert client.post(BASE + "/up", headers=E).status_code == 502


def test_equal_tokens_rejected():
    with pytest.raises(ValidationError):
        Settings(sandbox_token="x" * 32, evaluator_token="x" * 32)


def test_health(client):
    assert client.get("/healthz").json() == {"status": "ok"}
