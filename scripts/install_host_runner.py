"""Install a user-level host runner so Compose can control VirtualBox safely."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
UNIT = UNIT_DIR / "agentic-lab-host-runner.service"


def main():
    """Устанавливает Python-окружение и пользовательский systemd unit host runner.

    Сервис запускает Vagrant от пользователя хоста; Docker-панель обращается к нему
    через Unix socket. Unit содержит абсолютный путь к проекту, поэтому после
    перемещения каталога установщик нужно запустить снова. Системные пакеты
    Vagrant/VirtualBox этот скрипт не устанавливает.
    """
    subprocess.run(["uv", "sync", "--locked"], cwd=ROOT, check=True)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    UNIT.write_text(f"""[Unit]
Description=Agentic Lab Vagrant host runner
After=default.target

[Service]
Type=simple
WorkingDirectory={ROOT}
ExecStart={ROOT / ".venv/bin/python"} {ROOT / "scripts/serve_host_runner.py"}
Restart=on-failure
UMask=0077

[Install]
WantedBy=default.target
""")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "agentic-lab-host-runner.service"],
        check=True,
    )
    subprocess.run(
        ["systemctl", "--user", "is-active", "agentic-lab-host-runner.service"],
        check=True,
    )
    print("Host runner ready. Start the panel with: docker compose up --build")


if __name__ == "__main__":
    main()
