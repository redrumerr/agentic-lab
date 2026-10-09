"""Compatibility entry point for benchmark init; use uv run benchmark init."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lab_service.benchmark import main

if __name__ == "__main__":
    main(["init", *sys.argv[1:]])
