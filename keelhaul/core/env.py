"""Environment for child processes and tool lookup.

Inside an AppImage, AppRun prepends the bundle to PATH and points
LD_LIBRARY_PATH and PYTHONHOME at bundled copies. System binaries like
cryptsetup must not load those, and root cannot read a user's FUSE mount
at all, so every tool is resolved from the system PATH with the bundle
stripped out.
"""

from __future__ import annotations

import os
import shutil

_STRIP_VARS = (
    "LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH",
    "PYTHONSTARTUP", "PYTHONUSERBASE", "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "GTK_PATH",
    "GDK_PIXBUF_MODULE_FILE", "GSETTINGS_SCHEMA_DIR", "XDG_DATA_DIRS_APPIMAGE",
)

_FALLBACK_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _bundle_root() -> str | None:
    root = os.environ.get("APPDIR")
    return os.path.realpath(root) if root else None


def system_path() -> str:
    """PATH without AppImage entries, with sbin dirs added for root tools."""
    bundle = _bundle_root()
    keep = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        if bundle and os.path.realpath(entry).startswith(bundle):
            continue
        if entry not in keep:
            keep.append(entry)
    for entry in _FALLBACK_PATH.split(":"):
        if entry not in keep:
            keep.append(entry)
    return os.pathsep.join(keep)


def which(name: str) -> str | None:
    """Find a system binary, never one inside the AppImage."""
    return shutil.which(name, path=system_path())


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_VARS}
    env["PATH"] = system_path()
    # Parsed output must not be localized.
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    if extra:
        env.update(extra)
    return env


def distro_family() -> str:
    """Package manager family for install hints: apt, dnf, zypper, pacman, apk, emerge."""
    for tool in ("apt-get", "dnf", "zypper", "pacman", "apk", "emerge", "xbps-install"):
        if which(tool):
            return {"apt-get": "apt", "xbps-install": "xbps"}.get(tool, tool)
    return "unknown"
