from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
Service = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")]


class StrictModel(BaseModel):
    """Базовая схема запросов и YAML: неизвестные поля считаются ошибкой."""

    model_config = ConfigDict(extra="forbid")


class PostgresCheck(StrictModel):
    """Сравнение строкового результата read-only SQL с expected."""

    name: str
    type: Literal["postgres_scalar"]
    query: str = Field(min_length=1, max_length=10000)
    expected: str


class FlagSubmissionCheck(StrictModel):
    """Точное сравнение отправленного флага с VM-only файлом."""

    name: str
    type: Literal["flag_submission"]
    expected: str = "exact VM-only flag"


class FileCheck(StrictModel):
    """Поиск expected в разрешённом файле гостевой VM."""

    name: str
    type: Literal["file_contains"]
    path: str = Field(pattern=r"^/opt/lab-target/[a-zA-Z0-9_.-]+$")
    expected: str


class HttpCheck(StrictModel):
    """Сравнение значения JSON-ключа локального mock API с expected."""

    name: str
    type: Literal["http_json"]
    url: str = Field(pattern=r"^http://127\.0\.0\.1:8080/[a-zA-Z0-9/_-]*$")
    key: str
    expected: object


# Поле type в YAML определяет, какую из четырёх схем проверки нужно применить.
Check = Annotated[
    PostgresCheck | FileCheck | HttpCheck | FlagSubmissionCheck,
    Field(discriminator="type"),
]


class Attack(StrictModel):
    """Полное описание сценария; согласованность путей и профиля проверяет Scenarios."""

    mode: Literal["agent", "escape"] = "agent"
    profile: Identifier | None = None
    id: Identifier
    title: str
    category: str
    prompt: str
    vagrantfile: str
    setup_script: str
    sandbox_service: Service
    checks: list[Check] = Field(min_length=1)
    metadata: dict[str, str]


class ExecRequest(StrictModel):
    """Команда и её timeout; единая схема для sandbox и evaluator."""

    command: str = Field(min_length=1, max_length=65536)
    timeout_seconds: int = Field(default=30, ge=1, le=7200)

    @field_validator("command")
    @classmethod
    def no_nul(cls, value):
        """Запрещает NUL, который нельзя передать аргументом процесса."""
        if "\0" in value:
            raise ValueError("NUL is not supported")
        return value


class ExecResult(BaseModel):
    """Единый результат локального, гостевого или переданного через bridge процесса."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class FlagSubmission(StrictModel):
    """Ограничивает размер и формат флага перед обращением к verifier."""

    flag: str = Field(min_length=6, max_length=256, pattern=r"^flag\{[a-zA-Z0-9_-]+\}$")
