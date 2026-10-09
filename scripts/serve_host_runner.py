"""Run Vagrant/VirtualBox on the host for the Compose-hosted control panel."""

from pathlib import Path
import signal
import threading
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lab_service.config import Settings
from lab_service.runner import Runner
from lab_service.runner_bridge import make_server


def main():
    """Запускает Unix-сервер для Vagrant на хосте и очищает socket при выходе.

    Здесь нет HTTP-авторизации: доступ ограничен правами Unix socket. Тестовые
    значения Settings нужны только для создания runner и не выдаются модели.
    Shutdown должен выполняться в другом потоке, иначе serve_forever может зависнуть.
    """
    settings = Settings(root=ROOT, sandbox_token="s" * 32, evaluator_token="e" * 32)
    socket_path = ROOT / ".runtime" / "host-runner.sock"
    server = make_server(ROOT, socket_path, Runner(settings))

    def shutdown(signum, frame):
        """Останавливает serve_forever из другого потока, чтобы избежать deadlock."""
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    try:
        print(f"Host runner listening: {socket_path}", flush=True)
        server.serve_forever()
    finally:
        server.server_close()
        socket_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
