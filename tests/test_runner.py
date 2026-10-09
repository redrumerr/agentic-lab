import base64
import os
import shlex
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from lab_service.models import ExecRequest, ExecResult
from lab_service.runner import Runner, encoded_shell
from lab_service.scenarios import Scenarios


@pytest.mark.parametrize("command", ["id", "echo '$(touch /tmp/NOPE)' ; echo a", 'printf "%s" "привет"', "echo one\necho two"])
def test_base64_roundtrip(command):
    wrapped = encoded_shell(command)
    assert base64.b64encode(command.encode()).decode() in wrapped
    assert command not in wrapped
    result = subprocess.run(["sh", "-c", wrapped], capture_output=True)
    expected = subprocess.run(["sh", "-c", command], capture_output=True)
    assert (result.returncode, result.stdout) == (expected.returncode, expected.stdout)


def test_sandbox_wrapper(settings, attack, monkeypatch):
    runner = Runner(settings)
    run = Mock(return_value=ExecResult(exit_code=0))
    monkeypatch.setattr(runner, "run", run)
    command = "echo 'hello'; $(printf harmless)"
    runner.sandbox(attack, ExecRequest(command=command))
    remote = run.call_args.args[1][2]
    assert command not in remote
    argv = shlex.split(remote)
    assert argv[:12] == ["cd", "/workspace", "&&", "sudo", "docker", "compose", "-f", "vm/agent-compose.yml", "exec", "-T", "sandbox", "timeout"]
    assert argv[-1] == encoded_shell(command)


def test_lifecycle_paths_and_environment(settings, attack, monkeypatch):
    process = Mock(pid=123, returncode=0)
    process.communicate.return_value = (b"ok", b"")
    popen = Mock(return_value=process)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setenv("LAB_EVALUATOR_TOKEN", "do-not-forward")
    runner = Runner(settings)
    runner.up(attack)
    kwargs = popen.call_args.kwargs
    assert kwargs["env"]["VAGRANT_VAGRANTFILE"] == "Vagrantfile"
    assert kwargs["cwd"] == settings.root / "vagrant"
    assert kwargs["env"]["LAB_SETUP_SCRIPT"] == str(settings.root / "vm/setup.sh")
    assert "LAB_EVALUATOR_TOKEN" not in kwargs["env"]
    runner.down(attack)
    assert popen.call_args.args[0] == ["vagrant", "destroy", "-f"]


def test_timeout_kills_process_group(settings, attack, monkeypatch):
    process = Mock(pid=123)
    process.communicate.side_effect = [subprocess.TimeoutExpired("vagrant", 1), (b"partial", b"")]
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    kill = Mock()
    monkeypatch.setattr(os, "killpg", kill)
    result = Runner(settings).run(attack, ["up"], 1)
    assert result.timed_out and result.exit_code == 124
    kill.assert_called_once_with(123, signal.SIGKILL)
    assert process.communicate.call_count == 2


def test_shared_lifecycle_lock(settings, attack, monkeypatch):
    first, second = Runner(settings), Runner(settings)
    other = Scenarios(settings.root).load("tool-misuse")
    assert first.lock_for(attack) is second.lock_for(other)
    active, maximum = 0, 0
    guard = threading.Lock()
    def run(*args):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.03)
        with guard:
            active -= 1
        return ExecResult(exit_code=0)
    monkeypatch.setattr(first, "run", run)
    monkeypatch.setattr(second, "run", run)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(first.up, attack), pool.submit(second.down, other)]
        for future in futures:
            future.result()
    assert maximum == 1
