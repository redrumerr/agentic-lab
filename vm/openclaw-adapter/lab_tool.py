import argparse
import json
import os
import urllib.error
import urllib.request


def main():
    """Вызывает один синтетический HTTP-инструмент с marker и возвращает его ответ/ошибку."""
    parser = argparse.ArgumentParser(description="Synthetic local tools only")
    parser.add_argument(
        "action", choices=["send-message", "tool-misuse", "privileged-action"]
    )
    parser.add_argument("--marker", required=True)
    args = parser.parse_args()
    request = urllib.request.Request(
        os.environ.get("TOOL_API_URL", "http://tool-api:8080") + "/" + args.action,
        data=json.dumps({"marker": args.marker}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(response.read().decode())
    except urllib.error.HTTPError as exc:
        print(exc.read().decode())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
