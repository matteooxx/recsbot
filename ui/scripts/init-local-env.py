#!/usr/bin/env python3
"""Create a mode-0600 recsbot-ui environment file."""

from __future__ import annotations

import argparse
import getpass
import secrets
from pathlib import Path

import bcrypt


def _password_hash() -> str:
    while True:
        first = getpass.getpass("Initial admin password (8+ characters): ")
        second = getpass.getpass("Repeat password: ")
        if first != second:
            print("Passwords do not match.")
            continue
        if len(first) < 8:
            print("Password must contain at least 8 characters.")
            continue
        return bcrypt.hashpw(first.encode(), bcrypt.gensalt()).decode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="runtime/app.env")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output = Path(args.output)
    if output.exists() and not args.force:
        parser.error(f"{output} already exists; use --force to replace it")

    template = Path(__file__).resolve().parents[1] / "app.env.example"
    values = {
        "ADMIN_PASS_HASH": _password_hash(),
        "SECRET_KEY": secrets.token_urlsafe(48),
    }
    lines: list[str] = []
    for line in template.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in values:
            line = f"{key}={values[key]}"
        lines.append(line)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output.chmod(0o600)
    print(f"Wrote {output} with mode 0600")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
