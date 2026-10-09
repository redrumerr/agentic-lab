import secrets
import os
import asyncio
from pathlib import Path
from typing import Literal
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .jobs import Jobs

from .config import Settings
from .models import ExecRequest, FlagSubmission
from .runner import Runner
from .runner_bridge import BridgeRunner
from .scenarios import Scenarios
from .verifier import verify


def create_app(settings=None, runner=None):
    """Создаёт API и панель, используя переданные настройки и runner.

    В тестах сюда подставляется FakeRunner, поэтому VM не требуется. В обычном
    запуске выбираем нативный runner или Unix bridge к хосту. Настройки, каталог,
    флаги и очередь принадлежат конкретному экземпляру app, а не глобальным данным.
    Роли проверяются до запуска действий; sandbox и evaluator не взаимозаменяемы.
    """

    def configure(app):
        """Подготавливает состояние app и сразу отклоняет некорректные локальные YAML."""
        app.state.settings = settings or Settings.from_env()
        bridge_socket = os.environ.get("LAB_BRIDGE_SOCKET")
        if runner:
            app.state.runner = runner
        elif bridge_socket:
            app.state.runner = BridgeRunner(app.state.settings, bridge_socket)
        else:
            app.state.runner = Runner(app.state.settings)
        app.state.scenarios = Scenarios(app.state.settings.root)
        app.state.scenarios.all()  # Fail early for malformed local configuration.
        app.state.escape_scenarios = Scenarios(app.state.settings.root, "escapes")
        app.state.escape_scenarios.all()
        app.state.submissions = {}
        app.state.jobs = Jobs()

    @asynccontextmanager
    async def lifespan(app):
        """Инициализирует app при старте и дожидается фоновых задач при завершении."""
        configure(app)
        try:
            yield
        finally:
            await asyncio.to_thread(app.state.jobs.close)

    app = FastAPI(title="Synthetic Agent Lab", lifespan=lifespan)

    static = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.middleware("http")
    async def browser_headers(request, call_next):
        """Добавляет запрет кеширования и защитные заголовки для браузерной панели."""
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path not in {"/docs", "/redoc", "/docs/oauth2-redirect"}:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return response

    @app.get("/", include_in_schema=False)
    def dashboard():
        """Возвращает HTML панели; авторизация нужна для действий через API."""
        return FileResponse(static / "index.html")

    def role(name):
        """Создаёт FastAPI dependency для проверки токена конкретной роли."""

        def authorize(request: Request, x_lab_token: str = Header(default="")):
            """Сравнивает токен без обычного посимвольного сравнения и возвращает 403 при отказе."""
            expected = getattr(request.app.state.settings, name + "_token")
            if not secrets.compare_digest(x_lab_token.encode(), expected.encode()):
                raise HTTPException(403, "Invalid role token")

        return authorize

    evaluator = Depends(role("evaluator"))
    sandbox = Depends(role("sandbox"))

    def get_attack(attack_id: str):
        """Ищет ID сначала в исходных сценариях, затем в escape-каталоге."""
        try:
            return app.state.scenarios.load(attack_id)
        except KeyError:
            try:
                return app.state.escape_scenarios.load(attack_id)
            except KeyError:
                raise HTTPException(404, "Unknown attack") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    def invoke(operation):
        """Переводит ошибки запуска VM в HTTP 502, сохраняя диагностическое сообщение."""
        try:
            return operation()
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise HTTPException(502, f"VM operation failed: {exc}") from exc

    @app.get("/healthz")
    def health():
        """Проверяет доступность самого control API; готовность VM проверяется отдельно."""
        return {"status": "ok"}

    @app.get("/v1/attacks", dependencies=[evaluator])
    def attacks():
        """Возвращает краткий каталог обеих семей для выбора сценария в панели."""
        scenarios = app.state.scenarios.all() + app.state.escape_scenarios.all()
        return [
            dict(id=a.id, title=a.title, category=a.category, mode=a.mode)
            for a in scenarios
        ]

    @app.get("/v1/attacks/{attack_id}", dependencies=[evaluator])
    def attack(attack_id: str):
        """Возвращает полное описание сценария, включая prompt и checks."""
        return get_attack(attack_id)

    def lifecycle(attack_id, action):
        """Сбрасывает отправленные флаги и выполняет синхронный up/down общей VM."""
        scenario = get_attack(attack_id)
        app.state.submissions.clear()
        result = invoke(lambda: getattr(app.state.runner, action)(scenario))
        if result.exit_code or result.timed_out:
            raise HTTPException(
                502, {"message": f"Vagrant {action} failed", **result.model_dump()}
            )
        return result

    @app.post("/v1/attacks/{attack_id}/up", dependencies=[evaluator])
    def up(attack_id: str):
        """Поднимает VM через evaluator-канал."""
        return lifecycle(attack_id, "up")

    @app.post("/v1/attacks/{attack_id}/down", dependencies=[evaluator])
    def down(attack_id: str):
        """Удаляет лабораторную VM через evaluator-канал."""
        return lifecycle(attack_id, "down")

    @app.post("/v1/attacks/{attack_id}/sandbox/exec", dependencies=[sandbox])
    def sandbox_exec(attack_id: str, body: ExecRequest):
        """Выполняет команду только внутри sandbox-контейнера выбранного сценария."""
        return invoke(lambda: app.state.runner.sandbox(get_attack(attack_id), body))

    @app.post("/v1/attacks/{attack_id}/evaluator/exec", dependencies=[evaluator])
    def evaluator_exec(attack_id: str, body: ExecRequest):
        """Выполняет доверенную команду оператора внутри VM."""
        return invoke(lambda: app.state.runner.evaluator(get_attack(attack_id), body))

    @app.post("/v1/attacks/{attack_id}/verify", dependencies=[evaluator])
    def verification(attack_id: str):
        """Запускает verifier под блокировкой VM и использует ранее отправленный флаг."""
        scenario = get_attack(attack_id)

        def operation():
            """Удерживает VM в одном состоянии на протяжении всех checks."""
            with app.state.runner.transaction(scenario):
                return verify(
                    scenario, app.state.runner, app.state.submissions.get(attack_id)
                )

        return invoke(operation)

    @app.post("/v1/attacks/{attack_id}/submit", dependencies=[evaluator])
    def submit_flag(attack_id: str, body: FlagSubmission):
        """Проверяет найденный escape-флаг и сохраняет его для последующих verify.

        Файл с эталонным флагом читает evaluator; его значение не возвращается клиенту.
        Проверка и сохранение идут под одной блокировкой, чтобы lifecycle не сменил флаг
        между этими действиями. Результат совпадения возвращается отдельно от HTTP-кода.
        """
        scenario = get_attack(attack_id)
        if scenario.mode != "escape":
            raise HTTPException(
                422, "Flag submission is only available for escape scenarios"
            )
        with app.state.runner.transaction(scenario):
            result = invoke(lambda: verify(scenario, app.state.runner, body.flag))
            app.state.submissions[attack_id] = body.flag
            return result

    def job_role(token):
        """Определяет владельца задач по одному из двух разрешённых токенов."""
        for name in ("sandbox", "evaluator"):
            if secrets.compare_digest(
                token.encode(), getattr(app.state.settings, name + "_token").encode()
            ):
                return name
        raise HTTPException(403, "Invalid role token")

    def perform_job(scenario, action, body):
        """Выполняет уже проверенное действие в рабочем потоке очереди.

        Lifecycle сам берёт блокировку в runner. Остальные действия оборачиваем
        в transaction здесь, чтобы команды, чтение логов и verifier не пересеклись
        со сбросом той же VM. Флаги очищаем для всех сценариев: VM у них общая.
        """
        runner = app.state.runner
        if action in ("up", "down"):
            app.state.submissions.clear()
            return getattr(runner, action)(scenario)

        with runner.transaction(scenario):
            if action == "verify":
                submitted_flag = app.state.submissions.get(scenario.id)
                return verify(scenario, runner, submitted_flag)
            if action in ("sandbox", "evaluator"):
                return getattr(runner, action)(scenario, body)
            if action == "status":
                return runner.run(scenario, ["status", "--machine-readable"], 30)

            compose = "docker compose -f vm/agent-compose.yml"
            if scenario.mode == "escape":
                compose = (
                    "docker compose -f vm/escape/compose.yml "
                    f"-f vm/escape/profiles/{scenario.id}.yml"
                )
            request = ExecRequest(
                command=f"cd /workspace && sudo {compose} logs --no-color --tail=200"
            )
            return runner.evaluator(scenario, request)

    @app.post("/v1/attacks/{attack_id}/jobs/{action}", status_code=202)
    def start_job(
        attack_id: str,
        action: Literal[
            "up", "down", "verify", "sandbox", "evaluator", "status", "logs"
        ],
        body: ExecRequest | None = None,
        x_lab_token: str = Header(default=""),
    ):
        """Проверяет действие и ставит его в очередь, возвращая HTTP 202 и ID задачи.

        Sandbox-токен допускается только для sandbox execution. Остальные действия
        требуют evaluator. Resource — абсолютный Vagrantfile, а не ID сценария:
        разные сценарии используют одну VM и не должны выполняться одновременно.
        Рабочий поток получает уже проверенные параметры; журнал и результат читаются
        через отдельные endpoints и остаются видимыми только своей роли.
        """
        owner = job_role(x_lab_token)
        required_role = "sandbox" if action == "sandbox" else "evaluator"
        if owner != required_role:
            raise HTTPException(403, "Invalid role token")
        scenario = get_attack(attack_id)
        if action in ("sandbox", "evaluator") and body is None:
            raise HTTPException(422, "Command required")
        resource = str((app.state.settings.root / scenario.vagrantfile).resolve())
        try:
            job_id = app.state.jobs.submit(
                owner,
                attack_id,
                action,
                resource,
                lambda: perform_job(scenario, action, body),
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": job_id}

    @app.get("/v1/jobs")
    def list_jobs(x_lab_token: str = Header(default="")):
        """Возвращает историю задач только роли, определённой токеном запроса."""
        return app.state.jobs.list(job_role(x_lab_token))

    @app.get("/v1/jobs/{job_id}")
    def read_job(
        job_id: str,
        after: int = Query(default=0, ge=0),
        x_lab_token: str = Header(default=""),
    ):
        """Возвращает результат и новые события после курсора; чужая задача выглядит как 404."""
        try:
            return app.state.jobs.get(job_id, job_role(x_lab_token), after)
        except KeyError:
            raise HTTPException(404, "Unknown job") from None

    return app


app = create_app()
