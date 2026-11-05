"""Optional operational log. Off by default.

Records what ran, when, and the exit code. Never secrets or data: argv never
carries a secret (they go through pipes), and the one throwaway value that
does (the hdparm erase password) is masked by the caller.
"""

from __future__ import annotations

import os
import time

_enabled = False


def log_path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "keelhaul", "keelhaul.log")


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = on


def enabled() -> bool:
    return _enabled


def record(message: str) -> None:
    if not _enabled:
        return
    path = log_path()
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except OSError:
        pass
