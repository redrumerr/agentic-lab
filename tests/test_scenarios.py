import shutil
from pathlib import Path

import pytest
import yaml

from lab_service.scenarios import Scenarios, repo_path

ROOT = Path(__file__).resolve().parents[1]


def test_all_five():
    attacks = Scenarios(ROOT).all()
    assert len(attacks) == 5
    for attack in attacks:
        assert repo_path(ROOT, attack.vagrantfile).is_relative_to(ROOT)
        assert repo_path(ROOT, attack.setup_script).is_relative_to(ROOT)
        assert "attack_success" in attack.metadata


@pytest.mark.parametrize("attack_id", ["../secret", "x/y", "x\\y", "C:\\secret", "/secret", "x\ny"])
def test_invalid_id(attack_id):
    with pytest.raises(KeyError):
        Scenarios(ROOT).load(attack_id)


@pytest.mark.parametrize("path", ["../outside", "vagrant/../pyproject.toml", "/etc/passwd", "C:\\tmp\\a", "vm\\setup.sh"])
def test_bad_paths(path):
    with pytest.raises(ValueError):
        repo_path(ROOT, path)


def test_symlink_escape(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("data")
    (root / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        repo_path(root, "link")


@pytest.mark.parametrize("field,value", [
    ("id", "wrong-name"), ("sandbox_service", "sandbox;id"),
    ("sandbox_service", "tool-api"), ("sandbox_service", "postgres"),
    ("setup_script", "../outside"), ("host_program", "/bin/sh"),
])
def test_invalid_yaml(tmp_path, field, value):
    for folder in ["attacks", "vagrant", "vm"]:
        (tmp_path / folder).mkdir()
    shutil.copy(ROOT / "vagrant/Vagrantfile", tmp_path / "vagrant/Vagrantfile")
    shutil.copy(ROOT / "vm/setup.sh", tmp_path / "vm/setup.sh")
    data = yaml.safe_load((ROOT / "attacks/context-poisoning.yaml").read_text())
    data[field] = value
    (tmp_path / "attacks/context-poisoning.yaml").write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        Scenarios(tmp_path).load("context-poisoning")


def test_compose_isolation():
    compose = yaml.safe_load((ROOT / "vm/agent-compose.yml").read_text())
    sandbox = compose["services"]["sandbox"]
    assert sandbox["user"] == "10001:10001"
    assert sandbox["read_only"] and sandbox["cap_drop"] == ["ALL"]
    assert sandbox["networks"] == ["sandbox-net"]
    assert compose["services"]["postgres"]["networks"] == ["target-net"]
    assert all(net["internal"] for net in compose["networks"].values())
    assert not sandbox.get("privileged") and not sandbox.get("network_mode")
    assert sandbox["volumes"] == ["sandbox-state:/state", "../fixtures:/fixtures:ro"]
    assert "LAB_EVALUATOR_TOKEN" not in sandbox["environment"]


def test_tmpfs_options_remain_one_mount():
    compose = yaml.safe_load((ROOT / "vm/agent-compose.yml").read_text())
    for name in ("sandbox", "tool-api"):
        assert compose["services"][name]["tmpfs"] == ["/tmp:size=32m,mode=1777"]


def test_host_compose_starts_only_ui():
    host = yaml.safe_load((ROOT / "compose.yaml").read_text())
    guest = yaml.safe_load((ROOT / "vm/agent-compose.yml").read_text())
    assert set(host["services"]) == {"ui"}
    assert set(guest["services"]) == {"sandbox", "tool-api", "postgres"}
    assert host["services"]["ui"]["ports"] == ["127.0.0.1:8000:8000"]
