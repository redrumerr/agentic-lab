"""Prepare a reusable VirtualBox box in a separate VM, without running attacks."""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from filelock import FileLock

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BOX = "bento/ubuntu-24.04"
DEFAULT_OPENCLAW_VERSION = "2026.9.6"
PREPARATION_TIMEOUT = 7200

# Изменение любого из этих файлов требует новой подготовки VM-образа.
BOX_INPUTS = (
    "vagrant/Vagrantfile",
    "vm/escape/common.sh",
    "vm/escape/prepare-box.sh",
    "vm/escape/Dockerfile",
    "vm/openclaw-adapter/adapter.py",
)


def build_box(base_box, name, output, version, root=None):
    """Создаёт и регистрирует подготовленный VirtualBox box в отдельной VM.

    base_box — исходная Ubuntu; name — имя новой регистрации; output — файл .box;
    version — версия OpenClaw для Docker-образа. Рабочая VM эксперимента не участвует:
    у builder свой VAGRANT_CWD и свои metadata в .cache/escape-boxes.

    Последовательность: provisioning, перезагрузка для новых kernel/headers,
    проверка готовности, упаковка, регистрация. Environment очищен от LAB-переменных
    оператора, чтобы токены и команда модели не попали в подготовленное окружение.
    В finally удаляется только builder VM. Готовый box и файл сохраняются;
    сбой cleanup печатает путь metadata для ручной диагностики.
    """
    root = (root or ROOT).resolve()
    if output.exists():
        raise ValueError(f"Refusing to overwrite {output}")
    cache = root / ".cache" / "escape-boxes"
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="builder-", dir=cache))
    output.parent.mkdir(parents=True, exist_ok=True)
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("LAB_")
    }
    env.update(
        VAGRANT_CWD=str(work),
        VAGRANT_VAGRANTFILE=str(root / "vagrant/Vagrantfile"),
        LAB_REPO_ROOT=str(root),
        LAB_SETUP_SCRIPT=str(root / "vm/escape/prepare-box.sh"),
        LAB_VAGRANT_BOX=base_box,
        LAB_BUILD_ESCAPE_BOX="1",
        LAB_ESCAPE_PROFILE="",
        LAB_OPENCLAW_VERSION=version,
        LAB_OPENCLAW_COMMAND="",
    )

    def run(*args):
        """Запускает один шаг Vagrant в builder и печатает длительность даже при ошибке."""
        started = time.monotonic()
        print(f"[box] starting: {' '.join(args)}", flush=True)
        try:
            subprocess.run(
                ["vagrant", *args],
                cwd=work,
                env=env,
                check=True,
                timeout=PREPARATION_TIMEOUT,
            )
        finally:
            print(f"[box] {args[0]}: {time.monotonic() - started:.1f}s", flush=True)

    try:
        run("up", "--provider=virtualbox", "--provision")
        run("reload", "--no-provision")
        run(
            "ssh",
            "-c",
            "test -d /lib/modules/$(uname -r)/build && sudo docker info >/dev/null",
        )
        run("package", "--output", str(output))
        run("box", "add", "--name", name, "--provider", "virtualbox", str(output))
    finally:
        # This CWD belongs only to the builder, not vagrant/'s benchmark VM.
        try:
            run("destroy", "-f")
        except (subprocess.SubprocessError, OSError) as exc:
            print(
                f"Builder cleanup failed: {exc}. Builder metadata: {work}", flush=True
            )
    print(
        f"\nBox registered: {name}\nBox file for your colleague: {output}", flush=True
    )


def escape_config(root, box_override=None, version_override=None):
    """Возвращает пару (box, версия OpenClaw) для escape-прогона.

    Читаем один JSON-снимок, чтобы не смешать имя от старой настройки с версией
    от новой. Явные environment overrides имеют приоритет; до init доступны
    базовый Ubuntu box и версия CLI по умолчанию. Повреждённую настройку считаем
    ошибкой: молча запускать другое окружение для бенчмарка нельзя.
    """
    state = root / ".runtime/benchmark.json"
    data = {}
    try:
        if state.exists():
            data = json.loads(state.read_text())
        box = box_override or data.get("box", "bento/ubuntu-24.04")
        version = version_override or data.get("openclaw_version", "2026.9.6")
        if not isinstance(box, str) or not box.strip() or len(box) > 256:
            raise ValueError("Invalid box name")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("Invalid OpenClaw version")
        return box, version
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError("Invalid benchmark state; run benchmark init again") from exc


def selected_box(root, override=None):
    """Возвращает только имя box; используется при проверке CLI перед запуском."""
    return escape_config(root, override)[0]


def box_registered(name):
    """Проверяет наличие имени среди локальных VirtualBox boxes без запуска VM."""
    result = subprocess.run(
        ["vagrant", "box", "list"], check=True, capture_output=True, text=True
    )
    return any(
        line.startswith(name + " (") and "(virtualbox," in line
        for line in result.stdout.splitlines()
    )


def _box_fingerprint(root, base_box, version):
    """Вычисляет хеш рецепта подготовки: файлов, базового box и версии CLI."""
    fingerprint = hashlib.sha256((base_box + "\n" + version + "\n").encode())
    for relative_path in BOX_INPUTS:
        contents = (root / relative_path).read_bytes()
        fingerprint.update(relative_path.encode() + b"\0" + contents)
    return fingerprint.hexdigest()


def _read_previous_state(state_path):
    """Читает прошлую настройку; отсутствующий или повреждённый JSON даёт пустой кеш."""
    try:
        return json.loads(state_path.read_text())
    except (OSError, ValueError):
        return {}


def _can_reuse_box(previous, fingerprint, name, output):
    """Проверяет рецепт, явные параметры пользователя и регистрацию box в Vagrant."""
    if not isinstance(previous, dict):
        return False
    if previous.get("fingerprint") != fingerprint:
        return False
    if name is not None and previous.get("box") != name:
        return False
    if output is not None and previous.get("box_file") != str(output.resolve()):
        return False

    box_name = previous.get("box")
    return isinstance(box_name, str) and box_registered(box_name)


def _save_state(state_path, data):
    """Атомарно заменяет настройки, чтобы runner не прочитал половину JSON."""
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=state_path.parent,
        prefix="benchmark-",
        suffix=".json",
        delete=False,
    ) as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
        temporary_path = Path(stream.name)
    os.replace(temporary_path, state_path)


def init_benchmark(
    root,
    base_box=DEFAULT_BOX,
    version=DEFAULT_OPENCLAW_VERSION,
    name=None,
    output=None,
    force=False,
):
    """Готовит box один раз и возвращает сохранённые настройки бенчмарка.

    Сначала сравниваем рецепт с предыдущей успешной сборкой. Если совпадают
    файлы и параметры, а box всё ещё зарегистрирован, дорогая сборка не нужна.
    force явно отключает это повторное использование.

    Файловая блокировка не позволяет двум init одновременно готовить один кеш.
    Новые настройки публикуются только после успешной сборки и регистрации:
    ошибка не лишает пользователя предыдущего рабочего box. Здесь не создаются
    флаг эксперимента, модельные сессии или учётные данные провайдера.
    """
    root = root.resolve()
    fingerprint = _box_fingerprint(root, base_box, version)
    locks_directory = root / ".locks"
    locks_directory.mkdir(exist_ok=True)
    state_path = root / ".runtime/benchmark.json"

    with FileLock(locks_directory / "benchmark-init.lock", timeout=PREPARATION_TIMEOUT):
        previous = _read_previous_state(state_path)
        if not force and _can_reuse_box(previous, fingerprint, name, output):
            print(f"Benchmark already initialized: {previous['box']}", flush=True)
            return previous

        # Уникальное имя сохраняет прошлую регистрацию, пока новая не готова.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        box_name = name or f"agentic-lab/escape-{stamp}"
        box_file = output or root / ".cache/escape-boxes" / f"escape-{stamp}.box"
        box_file = box_file.resolve()
        build_box(base_box, box_name, box_file, version, root=root)

        state = {
            "box": box_name,
            "fingerprint": fingerprint,
            "openclaw_version": version,
            "base_box": base_box,
            "box_file": str(box_file),
        }
        _save_state(state_path, state)

    print("Benchmark initialized. Run: uv run benchmark run docker-socket", flush=True)
    return state


def _make_parser():
    """Описывает три команды CLI и их параметры."""
    parser = argparse.ArgumentParser(
        description="Initialize and run local escape benchmarks."
    )
    commands = parser.add_subparsers(dest="action", required=True)

    initialize = commands.add_parser(
        "init", help="Prepare dependencies and a reusable clean VM box once"
    )
    initialize.add_argument("--base-box", default=DEFAULT_BOX)
    initialize.add_argument("--name")
    initialize.add_argument("--output", type=Path)
    initialize.add_argument(
        "--openclaw-version",
        default=os.environ.get("LAB_OPENCLAW_VERSION") or DEFAULT_OPENCLAW_VERSION,
    )
    initialize.add_argument(
        "--force",
        action="store_true",
        help="Build a new box even when current inputs match",
    )

    run = commands.add_parser(
        "run", help="Run OpenClaw on an escape scenario using the initialized box"
    )
    run.add_argument("scenario")
    run.add_argument("--keep-vm", action="store_true")
    commands.add_parser("list", help="List the seven local escape scenarios")
    return parser


def _run_benchmark(root, args, parser):
    """Проверяет init и передаёт запуск существующему OpenClaw runner.

    CLI отвечает за выбор подготовленного окружения. Сам цикл up → запуск
    агента → verifier → down остаётся в run_openclaw_escape.py. Передаём настройки
    аргументами и environment; shell не используется. Код завершения runner
    становится кодом завершения команды benchmark run.
    """
    state_path = root / ".runtime/benchmark.json"
    if not state_path.is_file():
        parser.error("Run benchmark init before running a benchmark")

    box_name = selected_box(root, os.environ.get("LAB_VAGRANT_BOX"))
    if not box_registered(box_name):
        parser.error("Prepared box is missing; run benchmark init again")

    state = json.loads(state_path.read_text())
    environment = os.environ.copy()
    environment["LAB_VAGRANT_BOX"] = box_name
    environment["LAB_OPENCLAW_VERSION"] = (
        os.environ.get("LAB_OPENCLAW_VERSION") or state["openclaw_version"]
    )
    command = [
        sys.executable,
        str(root / "scripts/run_openclaw_escape.py"),
        args.scenario,
    ]
    if args.keep_vm:
        command.append("--keep-vm")

    result = subprocess.run(command, cwd=root, env=environment)
    raise SystemExit(result.returncode)


def main(argv=None):
    """Разбирает CLI, проверяет инструменты хоста и выбирает init/run/list."""
    root = Path(os.environ.get("LAB_ROOT") or ROOT).resolve()
    parser = _make_parser()
    args = parser.parse_args(argv)

    try:
        # Каталог можно посмотреть до установки Vagrant/VirtualBox.
        if args.action == "list":
            from .scenarios import Scenarios

            scenarios = Scenarios(root, "escapes").all()
            print("\n".join(scenario.id for scenario in scenarios))
            return

        missing = [tool for tool in ("vagrant", "VBoxManage") if not shutil.which(tool)]
        if missing:
            parser.error("Install host tools first: " + ", ".join(missing))

        if args.action == "init":
            init_benchmark(
                root,
                base_box=args.base_box,
                version=args.openclaw_version,
                name=args.name,
                output=args.output,
                force=args.force,
            )
        else:
            _run_benchmark(root, args, parser)
    except (
        ValueError,
        KeyError,
        RuntimeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        parser.exit(1, f"Benchmark {args.action} failed: {exc}\n")


if __name__ == "__main__":
    main()
