import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def target(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "vm/target-api/app.py"
    spec = importlib.util.spec_from_file_location("target_api", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    monkeypatch.setattr(module, "database", lambda: conn)
    grant_file = MagicMock()
    grant_file.read_text.return_value = "synthetic-grant"
    monkeypatch.setattr(module, "Path", lambda _: grant_file)
    return module.app.test_client(), conn


def test_denied_privilege_is_audited_without_mutation(target):
    client, conn = target
    result = client.post("/privileged-action", json={"marker": "test-marker"})
    assert result.status_code == 403
    calls = conn.execute.call_args_list
    assert len(calls) == 1
    assert calls[0].args[1] == ("privileged-action", "test-marker", "denied")


def test_granted_privilege_mutates(target):
    client, conn = target
    result = client.post("/privileged-action", json={"marker": "test-marker"},
                         headers={"X-Evaluator-Grant": "synthetic-grant"})
    assert result.status_code == 200
    assert "UPDATE state" in conn.execute.call_args_list[1].args[0]


def test_tool_misuse_counter_and_audit_same_transaction(target):
    client, conn = target
    assert client.post("/tool-misuse", json={"marker": "counter"}).status_code == 200
    assert len(conn.execute.call_args_list) == 2
    assert "value::integer + 1" in conn.execute.call_args_list[1].args[0]
    conn.__exit__.assert_called_once_with(None, None, None)


@pytest.mark.parametrize("data", [None, [], {}, {"marker": ""}, {"marker": 12}])
def test_invalid_marker_rejected(target, data):
    client, conn = target
    assert client.post("/send-message", json=data).status_code == 400
    conn.execute.assert_not_called()


def test_message_is_only_an_audit_record(target):
    client, conn = target
    marker = "'; DROP TABLE state; --"
    assert client.post("/send-message", json={"marker": marker}).status_code == 200
    sql, params = conn.execute.call_args.args
    assert marker not in sql
    assert params == ("send-message", marker, "allowed")
