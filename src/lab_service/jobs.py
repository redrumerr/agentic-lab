"""Bounded, process-local operation history. Logs never cross role boundaries."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4

log_sink = ContextVar("lab_log_sink", default=None)


class Jobs:
    """Очередь с ограниченной историей в памяти одного процесса; данные разделены по ролям."""

    MAX_WORKERS = 4
    MAX_JOBS = 50
    MAX_EVENTS = 256
    MAX_EVENT_LENGTH = 4096

    def __init__(self):
        """Создаёт хранилище, блокировку данных и пул рабочих потоков."""
        self.lock = RLock()
        self.items = {}
        self.pool = ThreadPoolExecutor(
            max_workers=self.MAX_WORKERS, thread_name_prefix="lab-job"
        )

    def submit(self, role, attack_id, action, resource, operation):
        """Регистрирует задачу, если для этой VM ещё не выполняется другая операция.

        Проверка занятости и запись задачи защищены одной блокировкой: два запроса
        не могут одновременно пройти проверку свободной VM. При заполнении истории
        удаляется самая старая завершённая задача; работающие задачи сохраняются.
        operation — функция без аргументов, которая будет выполнена в рабочем потоке.
        Возвращаем ID сразу, а не ждём завершения Vagrant.
        """
        with self.lock:
            active = [j for j in self.items.values() if j["status"] == "running"]
            vm_busy = any(job["resource"] == resource for job in active)
            pool_full = len(active) >= self.MAX_WORKERS
            if pool_full or vm_busy:
                raise ValueError(
                    "Для этой VM уже выполняется операция. Дождитесь её завершения."
                )
            if len(self.items) >= self.MAX_JOBS:
                oldest = next(
                    key for key, j in self.items.items() if j["status"] != "running"
                )
                del self.items[oldest]
            job_id = uuid4().hex
            job = {
                "id": job_id,
                "role": role,
                "attack_id": attack_id,
                "action": action,
                "resource": resource,
                "status": "running",
                "result": None,
                "error": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "events": deque(maxlen=self.MAX_EVENTS),
                "cursor": 0,
            }
            self.items[job_id] = job
            self.pool.submit(self._work, job, operation)
            return job_id

    def _work(self, job, operation):
        """Выполняет operation и сохраняет журнал, результат либо текст ошибки.

        ContextVar передаёт функцию emit вниз до runner без глобального общего журнала.
        В finally восстанавливаем контекст, чтобы следующий запуск в том же потоке
        не писал в чужую задачу. completed означает завершение операции; verdict
        passed=false внутри результата verifier сам по себе не является ошибкой задачи.
        """

        def emit(stream, text):
            """Добавляет ограниченный блок журнала и увеличивает курсор под блокировкой."""
            with self.lock:
                job["cursor"] += 1
                job["events"].append(
                    {
                        "seq": job["cursor"],
                        "stream": stream,
                        "text": text[: self.MAX_EVENT_LENGTH],
                    }
                )

        token = log_sink.set(emit)
        try:
            emit("system", "Операция запущена\n")
            result = operation()
            if hasattr(result, "model_dump"):
                result = result.model_dump()
            with self.lock:
                job["result"] = result
                failed = result.get("exit_code", 0) or result.get("timed_out")
                job["status"] = "failed" if failed else "completed"
        except Exception as exc:
            with self.lock:
                job["error"] = str(exc)
                job["status"] = "failed"
        finally:
            log_sink.reset(token)

    def get(self, job_id, role, after=0):
        """Возвращает копию доступных данных и события после курсора, скрывая чужую роль."""
        with self.lock:
            job = self.items.get(job_id)
            if job is None or job["role"] != role:
                raise KeyError(job_id)
            result = {
                k: v for k, v in job.items() if k not in {"events", "resource", "role"}
            }
            result["events"] = [e.copy() for e in job["events"] if e["seq"] > after]
            result["truncated"] = bool(
                job["events"] and after < job["events"][0]["seq"] - 1
            )
            return result

    def list(self, role):
        """Возвращает краткую историю своей роли, от новых задач к старым."""
        with self.lock:
            result = []
            for job in reversed(list(self.items.values())):
                if job["role"] != role:
                    continue
                result.append(
                    {
                        "id": job["id"],
                        "attack_id": job["attack_id"],
                        "action": job["action"],
                        "status": job["status"],
                        "created_at": job["created_at"],
                    }
                )
            return result

    def close(self):
        """Дожидается выполняющихся задач и завершает пул потоков."""
        self.pool.shutdown(wait=True)
