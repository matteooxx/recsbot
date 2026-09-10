"""In-process settings overlay over app.env.

The process environment is the source of truth at container start. Runtime
changes take effect immediately and are written to app.env for the next start.

Public surface:
  get(key)                        -> str (overlay → env → "")
  set_many({key: value, ...})     -> None (in-process)
  persist({key: value, ...})      -> bool (writes to app.env + marker)

For secret keys returned to the UI we redact them to a length-only marker so the value
never round-trips through the browser unless the user explicitly re-enters it.
"""
from __future__ import annotations

import fcntl
import logging
import os
import re
import threading
from datetime import datetime

import config

log = logging.getLogger("recsbot-ui.settings")

ENV_PATH = os.environ.get("APP_ENV_PATH", "./runtime/app.env")
MARKER_PATH = os.path.join(os.path.dirname(ENV_PATH), ".env-changed")

# Keys exposed in /api/settings. Any key NOT in this list is not user-editable from the UI.
MANAGED_KEYS: tuple[str, ...] = (
    "STEAM_API_KEY",
    "STEAM_ID",
    "JELLYFIN_BASE_URL",
    "JELLYFIN_API_KEY",
    "JELLYFIN_USER_ID",
)

# Subset that is treated as secret (not echoed back to the UI).
SECRET_KEYS: frozenset[str] = frozenset({"STEAM_API_KEY", "JELLYFIN_API_KEY"})

_overlay: dict[str, str] = {}
_lock = threading.Lock()


def get(key: str) -> str:
    with _lock:
        if key in _overlay:
            return _overlay[key]
    # Fall back to config module attribute (which read os.environ at startup)
    val = getattr(config, key, "") or os.environ.get(key, "")
    return val or ""


def set_many(values: dict[str, str]) -> None:
    with _lock:
        for k, v in values.items():
            _overlay[k] = v or ""


def public_view() -> dict[str, dict[str, object]]:
    """Return all managed keys for the Settings UI. Secrets are length-only."""
    out: dict[str, dict[str, object]] = {}
    for k in MANAGED_KEYS:
        v = get(k)
        if k in SECRET_KEYS:
            out[k] = {"set": bool(v), "length": len(v)}
        else:
            out[k] = {"value": v}
    return out


_LOCK_PATH = ENV_PATH + ".lock"


def persist(values: dict[str, str]) -> bool:
    """Write the given keys into app.env on disk + touch the marker.

    The values dict may include only keys to update; existing keys are preserved.
    Returns True on success. Idempotent.

    File-locks via fcntl so two concurrent /api/settings PATCHes don't lose
    each other's changes (read-modify-write race).
    """
    try:
        # Acquire an exclusive flock on a sidecar lockfile. fcntl.flock is
        # advisory but every writer goes through this function, so good enough.
        with open(_LOCK_PATH, "w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    with open(ENV_PATH) as f:
                        lines = f.readlines()
                except FileNotFoundError:
                    lines = []
                seen: set[str] = set()
                for i, line in enumerate(lines):
                    m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=", line)
                    if not m:
                        continue
                    key = m.group(1)
                    if key in values:
                        lines[i] = f"{key}={values[key]}\n"
                        seen.add(key)
                for k, v in values.items():
                    if k not in seen:
                        lines.append(f"{k}={v}\n")
                tmp = ENV_PATH + ".tmp"
                with open(tmp, "w") as f:
                    f.writelines(lines)
                os.chmod(tmp, 0o600)
                os.replace(tmp, ENV_PATH)
                try:
                    with open(MARKER_PATH, "w") as f:
                        f.write(datetime.utcnow().isoformat())
                    os.chmod(MARKER_PATH, 0o644)
                except Exception as e:
                    log.warning("env-changed marker write failed: %s", e)
                return True
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        log.error("persist to %s failed: %s", ENV_PATH, e)
        return False
