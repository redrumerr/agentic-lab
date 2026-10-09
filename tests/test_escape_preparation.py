import os
import json
from pathlib import Path
import subprocess

import pytest

from lab_service.config import Settings
from lab_service.models import ExecResult
from lab_service.runner import Runner
from lab_service.scenarios import Scenarios

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("failure", [None, "exit", "timeout"])
def test_escape_up_recreates_vm_and_stops_on_failed_destroy(monkeypatch, failure):
    runner = Runner(Settings(root=ROOT, sandbox_token="s" * 32, evaluator_token="e" * 32))
    # Keep lifecycle lock files out of the real repository.
    from contextlib import nullcontext
    monkeypatch.setattr(runner, "transaction", lambda _: nullcontext())
    calls = []

    def run(attack, args, timeout):
        calls.append(args)
        if args[0] == "destroy" and failure:
            return ExecResult(exit_code=1 if failure == "exit" else 124, timed_out=failure == "timeout")
        return ExecResult(exit_code=0)

    monkeypatch.setattr(runner, "run", run)
    result = runner.up(Scenarios(ROOT, "escapes").load("docker-socket"))
    assert calls[0] == ["destroy", "-f"]
    if failure:
        assert len(calls) == 1 and result.exit_code
    else:
        assert calls[1] == ["up", "--provider=virtualbox", "--provision"]
        assert "clean VM + provisioning:" in result.stdout


def image_attempt(tmp_path, *, cached, version, adapter):
    dockerfile = tmp_path / "Dockerfile"
    adapter_file = tmp_path / "adapter.py"
    log = tmp_path / "docker.log"
    dockerfile.write_text("FROM node:24-bookworm-slim\n")
    adapter_file.write_text(adapter)
    script = '''
set -euo pipefail
source "$COMMON"
cat() { command cat "$TEST_DOCKERFILE" "$TEST_ADAPTER"; }
docker() {
  printf '%s %s\\n' "$LAB_ESCAPE_IMAGE" "$*" >> "$TEST_LOG"
  if [[ $1 == image ]]; then return "$CACHE_MISS"; fi
}
lab_image
'''
    env = {**os.environ, "COMMON": str(ROOT / "vm/escape/common.sh"),
           "TEST_DOCKERFILE": str(dockerfile), "TEST_ADAPTER": str(adapter_file),
           "TEST_LOG": str(log), "CACHE_MISS": "0" if cached else "1", "LAB_OPENCLAW_VERSION": version}
    subprocess.run(["bash", "-c", script], env=env, check=True, capture_output=True, text=True)
    return log.read_text().splitlines()


def test_prepared_image_reuse_and_invalidation(tmp_path):
    paths = []
    for i in range(4):
        path = tmp_path / str(i)
        path.mkdir()
        paths.append(path)
    hit = image_attempt(paths[0], cached=True, version="2026.9.6", adapter="old")
    miss = image_attempt(paths[1], cached=False, version="2026.9.6", adapter="old")
    changed_source = image_attempt(paths[2], cached=True, version="2026.9.6", adapter="new")
    changed_version = image_attempt(paths[3], cached=True, version="2026.9.7", adapter="old")
    assert len(hit) == 1  # Warm path cannot execute a build.
    assert len(miss) == 2 and miss[1].endswith("compose -f vm/escape/compose.yml build sandbox")
    assert hit[0].split()[0] == miss[0].split()[0]
    assert len({rows[0].split()[0] for rows in (hit, changed_source, changed_version)}) == 3


def builder_module():
    from lab_service import benchmark
    return benchmark


@pytest.mark.parametrize("fail_step", [None, "up", "reload", "ssh", "package"])
def test_builder_isolated_from_benchmark_and_cleans_up(monkeypatch, tmp_path, fail_step):
    module = builder_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setenv("LAB_EVALUATOR_TOKEN", "must-not-reach-builder")
    monkeypatch.setenv("LAB_OPENCLAW_COMMAND", "must-not-configure-a-model")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[1] == fail_step:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(module.subprocess, "run", run)
    output = tmp_path / "prepared.box"
    if fail_step:
        with pytest.raises(subprocess.CalledProcessError):
            module.build_box("bento/ubuntu-24.04", "lab/prepared", output, "2026.9.6")
    else:
        module.build_box("bento/ubuntu-24.04", "lab/prepared", output, "2026.9.6")
        assert calls[-2][0][1:3] == ["box", "add"]
    assert calls[-1][0] == ["vagrant", "destroy", "-f"]
    if not fail_step:
        assert [args[1] for args, _ in calls] == ["up", "reload", "ssh", "package", "box", "destroy"]
    for _, kwargs in calls:
        env = kwargs["env"]
        assert kwargs["cwd"] != tmp_path / "vagrant"
        assert kwargs["cwd"].parent == tmp_path / ".cache/escape-boxes"
        assert env["VAGRANT_CWD"] == str(kwargs["cwd"])
        assert env["LAB_SETUP_SCRIPT"].endswith("vm/escape/prepare-box.sh")
        assert env["LAB_BUILD_ESCAPE_BOX"] == "1"
        assert "LAB_EVALUATOR_TOKEN" not in env
        assert env["LAB_OPENCLAW_COMMAND"] == ""


def test_builder_preserves_existing_output(monkeypatch, tmp_path):
    module = builder_module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    output = tmp_path / "prepared.box"
    output.write_bytes(b"existing box")
    with pytest.raises(ValueError, match="overwrite"):
        module.build_box("base", "lab/prepared", output, "2026.9.6")
    assert output.read_bytes() == b"existing box"
    assert not (tmp_path / ".cache").exists()


def test_builder_can_replace_obsolete_kernel_headers(tmp_path):
    log = tmp_path / "apt.log"
    script = '''
set -euo pipefail
source "$COMMON"
uname() { printf 'obsolete-test-kernel'; }
docker() { return 0; }
systemctl() { return 0; }
command() {
  if [[ $1 == -v ]]; then return 0; fi
  builtin command "$@"
}
apt-get() { printf '%s\\n' "$*" >> "$TEST_LOG"; }
lab_dependencies all
'''
    subprocess.run(["bash", "-c", script], env={**os.environ,
                   "COMMON": str(ROOT / "vm/escape/common.sh"), "TEST_LOG": str(log)},
                   capture_output=True, check=True)
    installed = log.read_text()
    assert "linux-image-generic linux-headers-generic" in installed
    assert "linux-headers-obsolete-test-kernel" not in installed


def init_root(tmp_path):
    for name in ("vagrant/Vagrantfile", "vm/escape/common.sh", "vm/escape/prepare-box.sh",
                 "vm/escape/Dockerfile", "vm/openclaw-adapter/adapter.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    return tmp_path


def test_init_reuses_registered_box_and_rebuilds_changed_inputs(monkeypatch, tmp_path):
    module = builder_module()
    root = init_root(tmp_path)
    calls = []
    monkeypatch.setattr(module, "box_registered", lambda _: True)
    monkeypatch.setattr(module, "build_box", lambda *args, **kwargs: calls.append((args, kwargs)))
    first = module.init_benchmark(root, name="lab/prepared")
    assert module.init_benchmark(root) == first
    assert len(calls) == 1
    assert module.escape_config(root) == ("lab/prepared", "2026.9.6")
    assert module.escape_config(root, "lab/override", "2026.9.7") == ("lab/override", "2026.9.7")
    (root / "vm/openclaw-adapter/adapter.py").write_text("changed adapter")
    second = module.init_benchmark(root, name="lab/new")
    assert len(calls) == 2
    assert second["fingerprint"] != first["fingerprint"]
    assert module.selected_box(root) == "lab/new"


def test_init_rebuilds_missing_box_and_does_not_publish_failed_build(monkeypatch, tmp_path):
    module = builder_module()
    root = init_root(tmp_path)
    monkeypatch.setattr(module, "build_box", lambda *args, **kwargs: None)
    first = module.init_benchmark(root, name="lab/first")
    monkeypatch.setattr(module, "box_registered", lambda _: False)
    calls = []
    monkeypatch.setattr(module, "build_box", lambda *args, **kwargs: calls.append(args))
    module.init_benchmark(root, name="lab/second")
    assert len(calls) == 1
    before = (root / ".runtime/benchmark.json").read_bytes()

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["vagrant", "up"])

    monkeypatch.setattr(module, "build_box", fail)
    with pytest.raises(subprocess.CalledProcessError):
        module.init_benchmark(root, force=True)
    assert (root / ".runtime/benchmark.json").read_bytes() == before


def test_runner_uses_initialized_box_and_version(monkeypatch, tmp_path):
    import shutil
    from unittest.mock import Mock
    from lab_service.benchmark import init_benchmark
    module = builder_module()
    root = init_root(tmp_path)
    monkeypatch.setattr(module, "build_box", lambda *args, **kwargs: None)
    init_benchmark(root, name="lab/prepared", version="2026.9.7")
    for directory in ("escapes", "vm/escape/profiles"):
        shutil.copytree(ROOT / directory, root / directory)
    (root / "vm/escape/setup.sh").write_text("setup")
    process = Mock(pid=123, returncode=0)
    process.communicate.return_value = (b"ok", b"")
    popen = Mock(return_value=process)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setenv("LAB_VAGRANT_BOX", "")
    monkeypatch.setenv("LAB_OPENCLAW_VERSION", "")
    settings = Settings(root=root, sandbox_token="s" * 32, evaluator_token="e" * 32)
    runner = Runner(settings)
    runner.run(Scenarios(root, "escapes").load("docker-socket"), ["status"], 30)
    env = popen.call_args.kwargs["env"]
    assert env["LAB_VAGRANT_BOX"] == "lab/prepared"
    assert env["LAB_OPENCLAW_VERSION"] == "2026.9.7"


def test_cli_run_requires_init(monkeypatch, tmp_path, capsys):
    module = builder_module()
    monkeypatch.setenv("LAB_ROOT", str(tmp_path))
    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/tool")
    with pytest.raises(SystemExit) as exc:
        module.main(["run", "docker-socket"])
    assert exc.value.code == 2
    assert "benchmark init" in capsys.readouterr().err
