"""Local launcher; credentials are shown only in the operator's terminal."""

import os
import secrets
import uvicorn


def main():
    """Запускает панель с одним worker и выдаёт токены оператору в терминале.

    Если токены уже заданы в environment, используем их; иначе создаём случайные
    на этот запуск. Один worker нужен потому, что история Jobs хранится в памяти
    процесса. Токены не записываются в файлы и не передаются модели автоматически.
    """
    for name in ("LAB_EVALUATOR_TOKEN", "LAB_SANDBOX_TOKEN"):
        if not os.environ.get(name):
            os.environ[name] = secrets.token_hex(32)
        print(f"{name}={os.environ[name]}", flush=True)
    print(
        "Панель: http://127.0.0.1:8000 — вставьте токены выше в форму подключения.",
        flush=True,
    )
    uvicorn.run(
        "lab_service.app:app",
        host=os.environ.get("LAB_UI_BIND", "127.0.0.1"),
        port=8000,
        workers=1,
    )


if __name__ == "__main__":
    main()
