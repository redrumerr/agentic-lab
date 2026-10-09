import base64
import hashlib
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from contextlib import contextmanager

from filelock import FileLock

from .jobs import log_sink
from .benchmark import escape_config
from .streaming import collect
from .models import ExecResult
from .scenarios import repo_path


def encoded_shell(command: str) -> str:
    """Кодирует пользовательскую команду для передачи через внешний SSH shell.

    Во внешний shell попадает только Base64. Декодирование и выполнение произойдут
    в выбранном контейнере или VM, где команда разрешена. Это сохраняет кавычки,
    переносы строк и Unicode и не даёт содержимому prompt стать командой хоста.
    """
    payload = base64.b64encode(command.encode()).decode("ascii")
    # Only base64 reaches the outer shell. Decoding happens in the shell that
    # owns execution (container for sandbox, VM for evaluator).
    return 'exec sh -c "$(printf %s ' + shlex.quote(payload) + ' | base64 -d)"'


class Runner:
    """Управляет одной Vagrant VM и разделяет sandbox execution и evaluator execution."""

    _guard = threading.Lock()
    _locks = {}

    def __init__(self, settings):
        """Сохраняет проверенные настройки хоста."""
        self.settings = settings

    def paths(self, attack):
        """Проверяет и возвращает пути к Vagrantfile и provisioning-скрипту сценария."""
        return (
            repo_path(self.settings.root, attack.vagrantfile),
            repo_path(self.settings.root, attack.setup_script),
        )

    def lock_for(self, attack):
        """Возвращает общую блокировку потоков для одного абсолютного Vagrantfile."""
        path, _ = self.paths(attack)
        with self._guard:
            return self._locks.setdefault(str(path), threading.RLock())

    @contextmanager
    def transaction(self, attack):
        """Блокирует общую VM на время составной операции вызывающего кода.

        RLock синхронизирует потоки внутри процесса, FileLock — разные процессы API.
        Имя файла зависит от абсолютного Vagrantfile, поэтому сценарии одной VM
        используют один lock. Повторный захват в том же потоке допустим для lifecycle.
        Сам run не берёт lock: границы операции определяют up/down, verifier и jobs.
        """
        path, _ = self.paths(attack)
        directory = self.settings.root / ".locks"
        directory.mkdir(exist_ok=True)
        name = hashlib.sha256(str(path).encode()).hexdigest()
        with (
            self.lock_for(attack),
            FileLock(
                directory / f"{name}.lock", timeout=self.settings.lifecycle_timeout
            ),
        ):
            yield

    def run(self, attack, args, timeout, environment=None):
        """Запускает Vagrant на хосте и возвращает ExecResult.

        Команда задаётся списком аргументов, без host shell. Environment выбирает
        Vagrantfile, provisioning и подготовленный box; токены control API удаляются.
        При активной фоновой задаче collect передаёт stdout/stderr по мере появления,
        иначе вывод собирается до завершения. Timeout уничтожает локальную группу
        Vagrant/SSH и дочерние процессы, а результат помечается timed_out.
        """
        path, setup = self.paths(attack)
        env = os.environ.copy()
        env.update(environment or {})
        # Vagrant/provisioning must never inherit control API credentials.
        env.pop("LAB_SANDBOX_TOKEN", None)
        env.pop("LAB_EVALUATOR_TOKEN", None)
        if attack.mode == "escape":
            box, version = escape_config(
                self.settings.root,
                env.get("LAB_VAGRANT_BOX"),
                env.get("LAB_OPENCLAW_VERSION"),
            )
            env.update(LAB_VAGRANT_BOX=box, LAB_OPENCLAW_VERSION=version)
        elif not env.get("LAB_VAGRANT_BOX"):
            env["LAB_VAGRANT_BOX"] = "bento/ubuntu-24.04"
        env.update(
            VAGRANT_CWD=str(path.parent),
            VAGRANT_VAGRANTFILE=path.name,
            LAB_SETUP_SCRIPT=str(setup),
            LAB_REPO_ROOT=str(self.settings.root),
            LAB_ESCAPE_PROFILE=attack.profile if attack.mode == "escape" else "",
        )
        process = subprocess.Popen(
            ["vagrant", *args],
            cwd=path.parent,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if log_sink.get() is not None:
            return collect(process, timeout, log_sink.get())
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # Kill the entire local Vagrant/SSH process group and reap it.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        return ExecResult(
            exit_code=124 if timed_out else process.returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            timed_out=timed_out,
        )

    def up(self, attack):
        """Поднимает окружение сценария под общей lifecycle-блокировкой.

        Для escape сначала удаляем прежнюю VM: агент мог изменить гостевую систему
        или Docker daemon, и reset контейнера не гарантирует чистый следующий прогон.
        Ошибка destroy останавливает запуск. Затем импортируем box и выполняем
        provisioning; подготовленный box позволяет пропустить установку и сборку.
        Исходные agent-сценарии сохраняют обычный up с повторным provisioning.
        """
        with self.transaction(attack):
            if attack.mode == "escape":
                # Container escapes can mutate the VM and its Docker daemon.
                # Re-import the clean box instead of trusting a previous reset.
                started = time.monotonic()
                emit = log_sink.get()
                if emit:
                    emit("system", "[lab] recreating clean escape VM from box\n")
                result = self.run(
                    attack, ["destroy", "-f"], self.settings.lifecycle_timeout
                )
                if result.exit_code or result.timed_out:
                    return result
                result = self.run(
                    attack,
                    ["up", "--provider=virtualbox", "--provision"],
                    self.settings.lifecycle_timeout,
                )
                timing = f"[lab] clean VM + provisioning: {time.monotonic() - started:.1f}s\n"
                result.stdout += "\n" + timing
                if emit:
                    emit("system", timing)
                return result
            return self.run(
                attack,
                ["up", "--provider=virtualbox", "--provision"],
                self.settings.lifecycle_timeout,
            )

    def down(self, attack):
        """Удаляет лабораторную VM под lifecycle-блокировкой; глобальный box-кеш остаётся."""
        with self.transaction(attack):
            return self.run(attack, ["destroy", "-f"], self.settings.lifecycle_timeout)

    def sandbox(self, attack, request):
        """Выполняет команду внутри sandbox-контейнера выбранного сценария.

        Проверяем фиксированное имя сервиса: YAML не может перенаправить sandbox-токен
        в privileged tool API или БД. Escape выбирает проверенный Compose-профиль.
        Timeout работает внутри контейнера, поэтому обрыв SSH не оставляет обычную
        дочернюю команду без ограничения времени. Внешнему SSH даём ещё 10 секунд.
        """
        service = attack.sandbox_service
        if (
            not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", service)
            or service != "sandbox"
        ):
            raise ValueError("Invalid sandbox service")
        seconds = request.timeout_seconds
        # timeout runs INSIDE the container, so killing SSH cannot leave a
        # normal child command running indefinitely in the remote container.
        compose = "docker compose -f vm/agent-compose.yml"
        if attack.mode == "escape":
            if attack.profile != attack.id:
                raise ValueError("Mismatched escape profile")
            repo_path(self.settings.root, f"vm/escape/profiles/{attack.id}.yml")
            compose = (
                "docker compose -f vm/escape/compose.yml -f vm/escape/profiles/"
                + attack.id
                + ".yml"
            )
        remote = (
            "cd /workspace && sudo "
            + compose
            + " exec -T "
            + service
            + f" timeout -k 2s {seconds}s sh -lc "
            + shlex.quote(encoded_shell(request.command))
        )
        result = self.run(attack, ["ssh", "-c", remote], seconds + 10)
        result.timed_out |= result.exit_code in (124, 137)
        return result

    def evaluator(self, attack, request):
        """Выполняет доверенную команду оператора в VM с внутренним timeout и запасом для SSH."""
        seconds = request.timeout_seconds
        remote = f"timeout -k 2s {seconds}s sh -lc " + shlex.quote(
            encoded_shell(request.command)
        )
        result = self.run(attack, ["ssh", "-c", remote], seconds + 10)
        result.timed_out |= result.exit_code in (124, 137)
        return result
