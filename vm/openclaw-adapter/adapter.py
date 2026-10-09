"""Explicit CLI bridge. Prompt is always one argv item, never a shell string."""

import argparse
import json
import os
import shlex
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main():
    """Держит sandbox запущенным либо передаёт prompt настроенной CLI-программе.

    --serve ожидает сигнал завершения контейнера. В режиме запуска нужен ровно
    один источник prompt: аргумент или файл. OPENCLAW_COMMAND разбирается в argv,
    prompt добавляется одним последним аргументом без shell. Если команды нет,
    пишем placeholder transcript; инструменты и модель в этом режиме не вызываются.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    args = parser.parse_args()
    if args.serve:
        signal.signal(signal.SIGTERM, lambda *_: exit(0))
        while True:
            signal.pause()
    if (args.prompt is None) == (args.prompt_file is None):
        parser.error("provide exactly one of --prompt or --prompt-file")
    prompt = (
        args.prompt if args.prompt is not None else Path(args.prompt_file).read_text()
    )
    command = os.environ.get("OPENCLAW_COMMAND", "")
    if command:
        return subprocess.run(
            [*shlex.split(command), prompt], timeout=7000, check=False
        ).returncode
    with Path("/state/transcript.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "prompt": prompt,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "mode": "placeholder",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    print("Placeholder: prompt recorded; no tools invoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
