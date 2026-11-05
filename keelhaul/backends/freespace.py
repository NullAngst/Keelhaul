"""Free-Space Wipe backends: sfill (secure-delete), dd random fill fallback."""

from __future__ import annotations

import os
import secrets


def sfill_argv(mount: str) -> list[str]:
    # -l -l: one random pass. More passes buy nothing on modern disks.
    return ["sfill", "-v", "-l", "-l", mount]


def fill_path(mount: str) -> str:
    return os.path.join(mount, f".keelhaul-fill.{secrets.token_hex(4)}")


def dd_argv(path: str) -> list[str]:
    # Exits 1 with "No space left on device" when the fill is complete.
    return ["dd", "if=/dev/urandom", f"of={path}", "bs=4M", "status=progress", "conv=fsync"]


def avail_bytes(mount: str) -> int:
    st = os.statvfs(mount)
    return st.f_bavail * st.f_frsize


def free_bytes_root(mount: str) -> int:
    """Free space including root-reserved blocks."""
    st = os.statvfs(mount)
    return st.f_bfree * st.f_frsize


def needs_root(mount: str) -> bool:
    return not os.access(mount, os.W_OK)
