import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class Settings(BaseModel):
    """Настройки хоста: корень проекта, раздельные роли токенов и timeout lifecycle."""

    root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])
    sandbox_token: str = Field(min_length=24)
    evaluator_token: str = Field(min_length=24)
    lifecycle_timeout: int = Field(default=900, ge=1, le=3600)

    @model_validator(mode="after")
    def different_tokens(self):
        """Запрещает одинаковые токены двух ролей и приводит корень к абсолютному пути."""
        if self.sandbox_token == self.evaluator_token:
            raise ValueError("Sandbox and evaluator tokens must differ")
        self.root = self.root.resolve()
        return self

    @classmethod
    def from_env(cls):
        """Читает настройки из окружения процесса и проверяет их через Pydantic.

        .env здесь не загружается. Launcher или оболочка должны задать токены заранее.
        LAB_ROOT нужен, если Python package установлен отдельно от каталога сценариев.
        """
        values = dict(
            sandbox_token=os.environ.get("LAB_SANDBOX_TOKEN", ""),
            evaluator_token=os.environ.get("LAB_EVALUATOR_TOKEN", ""),
        )
        if os.environ.get("LAB_ROOT"):
            values["root"] = Path(os.environ["LAB_ROOT"])
        return cls(**values)
