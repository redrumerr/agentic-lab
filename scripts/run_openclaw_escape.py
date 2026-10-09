"""Run the configured OpenClaw CLI inside an escape scenario and score its output."""

import argparse
import os
from pathlib import Path
import re
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lab_service.config import Settings
from lab_service.models import ExecRequest
from lab_service.runner import Runner
from lab_service.scenarios import Scenarios
from lab_service.verifier import verify


def main():
    """Запускает настроенный OpenClaw внутри выбранной escape VM и проверяет флаг.

    Сначала up создаёт чистое окружение. Prompt передаётся адаптеру как один аргумент,
    а последний flag{...} из stdout сравнивается с эталоном через evaluator.
    CLI и модель должны быть настроены заранее; установка пакета их не заменяет.
    После запуска down выполняется в finally, если не задан --keep-vm.
    Ошибка начального up происходит до этого блока и требует ручной проверки VM.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario", choices=[s.id for s in Scenarios(ROOT, "escapes").all()]
    )
    parser.add_argument("--keep-vm", action="store_true")
    args = parser.parse_args()
    if not os.environ.get("LAB_OPENCLAW_COMMAND", "").strip():
        parser.error("set LAB_OPENCLAW_COMMAND to the configured OpenClaw CLI command")
    runner = Runner(
        Settings(root=ROOT, sandbox_token="s" * 32, evaluator_token="e" * 32)
    )
    attack = Scenarios(ROOT, "escapes").load(args.scenario)
    up = runner.up(attack)
    if up.exit_code or up.timed_out:
        raise SystemExit(f"VM setup failed: {up.stderr[-2000:]}")
    try:
        command = "python3 /app/adapter.py --prompt " + shlex.quote(attack.prompt)
        result = runner.sandbox(
            attack, ExecRequest(command=command, timeout_seconds=7200)
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        candidates = re.findall(r"flag\{[a-zA-Z0-9_-]+\}", result.stdout)
        verdict = verify(attack, runner, candidates[-1] if candidates else None)
        print(
            f"OpenClaw exit={result.exit_code}; submitted={bool(candidates)}; passed={verdict['passed']}"
        )
        if result.exit_code or not verdict["passed"]:
            raise SystemExit(1)
    finally:
        if not args.keep_vm:
            down = runner.down(attack)
            if down.exit_code or down.timed_out:
                print(f"VM teardown failed: {down.stderr[-1000:]}", file=sys.stderr)


if __name__ == "__main__":
    main()
