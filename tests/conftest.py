from contextlib import nullcontext
from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient

from lab_service.app import create_app
from lab_service.config import Settings
from lab_service.models import ExecResult
from lab_service.scenarios import Scenarios

ROOT = Path(__file__).resolve().parents[1]


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.result = ExecResult(exit_code=0, stdout="t\n")

    def transaction(self, attack):
        return nullcontext()

    def record(self, name, attack, request=None):
        self.calls.append((name, attack, request))
        return self.result

    def up(self, attack):
        return self.record("up", attack)

    def down(self, attack):
        return self.record("down", attack)

    def sandbox(self, attack, request):
        return self.record("sandbox", attack, request)

    def evaluator(self, attack, request):
        return self.record("evaluator", attack, request)


@pytest.fixture
def settings(tmp_path):
    shutil.copytree(ROOT / "attacks", tmp_path / "attacks")
    for name in ("vagrant/Vagrantfile", "vm/setup.sh"):
        target = tmp_path / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return Settings(root=tmp_path, sandbox_token="s" * 32, evaluator_token="e" * 32)


@pytest.fixture
def attack():
    return Scenarios(ROOT).load("context-poisoning")


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def client(settings, runner):
    with TestClient(create_app(settings, runner)) as client:
        yield client
