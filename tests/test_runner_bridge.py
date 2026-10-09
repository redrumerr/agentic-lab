import json
import socket
import threading
import shutil

from lab_service.jobs import log_sink
from lab_service.models import ExecResult
from lab_service.runner_bridge import BridgeRunner, make_server
from lab_service.scenarios import Scenarios
from conftest import ROOT


def test_bridge_forwards_vagrant_operation_and_live_log(settings, attack):
    calls = []
    class Backend:
        def run(self, scenario, args, timeout, environment=None):
            calls.append((scenario.id, args, timeout, environment))
            log_sink.get()( "stdout", "VM ready\n")
            return ExecResult(exit_code=0, stdout="VM ready\n")
    path = settings.root / ".runtime" / "host-runner.sock"
    server = make_server(settings.root, path, Backend())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    events = []
    token = log_sink.set(lambda stream, value: events.append((stream, value)))
    try:
        result = BridgeRunner(settings, path).up(attack)
        assert result.stdout == "VM ready\n"
        assert events == [("stdout", "VM ready\n")]
        assert calls[0][:2] == ("context-poisoning", ["up", "--provider=virtualbox", "--provision"])
    finally:
        log_sink.reset(token)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_bridge_rejects_unapproved_vagrant_args(settings):
    path = settings.root / ".runtime" / "host-runner.sock"
    class Backend:
        def run(self, *args, **kwargs):
            raise AssertionError("must never execute")
    server = make_server(settings.root, path, Backend())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(path))
            with client.makefile("rwb") as stream:
                stream.write((json.dumps({"attack_id": "context-poisoning", "args": ["plugin", "install", "anything"], "timeout": 30, "environment": {}}) + "\n").encode())
                stream.flush()
                assert json.loads(stream.readline())["type"] == "error"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_bridge_selects_initialized_escape_box_without_host_state(settings, monkeypatch):
    for directory in ("escapes", "vm/escape/profiles"):
        shutil.copytree(ROOT / directory, settings.root / directory)
    shutil.copyfile(ROOT / "vm/escape/setup.sh", settings.root / "vm/escape/setup.sh")
    calls = []

    class Backend:
        def run(self, scenario, args, timeout, environment=None):
            calls.append((args, environment))
            return ExecResult(exit_code=0)

    path = settings.root / ".runtime/host-runner.sock"
    server = make_server(settings.root, path, Backend())
    (path.parent / "benchmark.json").write_text(json.dumps({"box": "lab/prepared", "openclaw_version": "2026.9.7"}))
    monkeypatch.setenv("LAB_VAGRANT_BOX", "")
    monkeypatch.setenv("LAB_OPENCLAW_VERSION", "")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = BridgeRunner(settings, path).up(Scenarios(settings.root, "escapes").load("docker-socket"))
        assert result.exit_code == 0
        assert [args[0] for args, _ in calls] == ["destroy", "up"]
        for _, environment in calls:
            assert environment["LAB_VAGRANT_BOX"] == "lab/prepared"
            assert environment["LAB_OPENCLAW_VERSION"] == "2026.9.7"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
