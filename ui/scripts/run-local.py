#!/usr/bin/env python3
"""Load app.env without shell evaluation, then start Gunicorn."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="runtime/app.env")
    parser.add_argument("--bind", default="127.0.0.1:5001")
    args = parser.parse_args()

    env_path = Path(args.env)
    if not env_path.is_file():
        parser.error(f"{env_path} does not exist; run scripts/init-local-env.py")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value)
    os.environ["APP_ENV_PATH"] = str(env_path.resolve())

    os.execvp(
        "gunicorn",
        [
            "gunicorn",
            "--workers",
            "1",
            "--threads",
            "16",
            "--worker-class",
            "gthread",
            "--bind",
            args.bind,
            "--timeout",
            "600",
            "app:app",
        ],
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
