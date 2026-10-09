import re
from pathlib import Path, PurePosixPath

import yaml

from .models import Attack


def repo_path(root: Path, value: str) -> Path:
    """Разрешает только существующий файл внутри корня проекта.

    Отсекаем абсолютные пути, .. и Windows-разделители; resolve также проверяет
    symlink, чтобы ссылка из репозитория не вывела чтение за его границы.
    """
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise ValueError(f"Unsafe repository path: {value}")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError(f"Path must be an existing file inside repository: {value}")
    return resolved


class Scenarios:
    """Загрузчик одной семьи декларативных YAML-сценариев."""

    def __init__(self, root: Path, directory: str = "attacks"):
        """Выбирает разрешённую семью и сохраняет абсолютный корень проекта."""
        if directory not in {"attacks", "escapes"}:
            raise ValueError("Unknown scenario family")
        self.root = root.resolve()
        self.directory = directory

    def load(self, attack_id: str) -> Attack:
        """Читает YAML по ID и проверяет, что runner сможет безопасно его использовать.

        Pydantic проверяет поля и checks. Затем сверяем ID с именем файла, проверяем
        пути и согласованность семьи, mode, profile и setup script. Sandbox всегда
        указывает сервис sandbox; другие контейнеры не доступны его токену.
        KeyError означает неизвестный ID, ValueError — некорректное описание.
        """
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", attack_id):
            raise KeyError(attack_id)
        if not (self.root / self.directory / f"{attack_id}.yaml").is_file():
            raise KeyError(attack_id)
        path = repo_path(self.root, f"{self.directory}/{attack_id}.yaml")
        attack = Attack.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if attack.id != attack_id:
            raise ValueError("Attack ID must match YAML filename")
        repo_path(self.root, attack.vagrantfile)
        repo_path(self.root, attack.setup_script)
        if self.directory == "escapes":
            if attack.mode != "escape" or attack.profile != attack_id:
                raise ValueError("Escape profile must match the scenario filename")
            repo_path(self.root, f"vm/escape/profiles/{attack_id}.yml")
            if attack.setup_script != "vm/escape/setup.sh":
                raise ValueError("Escape scenarios must use fixed VM setup")
        elif attack.mode != "agent" or attack.profile is not None:
            raise ValueError("Agent scenario cannot select an escape profile")
        # A syntactically valid service name alone is insufficient: never allow
        # YAML to redirect sandbox execution into the privileged tool API/DB.
        if attack.sandbox_service != "sandbox":
            raise ValueError("Only the sandbox service is permitted")
        return attack

    def all(self):
        """Загружает каталог в стабильном порядке имён файлов."""
        return [
            self.load(path.stem)
            for path in sorted((self.root / self.directory).glob("*.yaml"))
        ]
