"""User settings. Nothing secret is ever stored here."""

from __future__ import annotations

from PyQt6.QtCore import QSettings

DEFAULTS = {
    "theme": "copper",
    "escalation": "auto",       # auto, pkexec, sudo
    "throttle": True,
    "cpu_ceiling": 85,          # percent of total CPU, summed over a job's processes
    "rss_ceiling_mib": 2048,
    "load_ceiling": 0.0,        # 0 means ncpu * 1.5
    "sustain_seconds": 6,
    "recover_seconds": 3,
    "logging": False,
}


class Settings:
    def __init__(self) -> None:
        self._q = QSettings("Keelhaul", "Keelhaul")

    def get(self, key: str):
        default = DEFAULTS[key]
        val = self._q.value(key, default)
        if isinstance(default, bool):
            return val if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes")
        if isinstance(default, int):
            try:
                return int(val)
            except (TypeError, ValueError):
                return default
        if isinstance(default, float):
            try:
                return float(val)
            except (TypeError, ValueError):
                return default
        return str(val)

    def set(self, key: str, value) -> None:
        self._q.setValue(key, value)
        self._q.sync()
