"""Metadata scrub backends: mat2 (primary) and exiftool (alternate).

Both write a cleaned copy next to the original as name.cleaned.ext, which
is mat2's own naming. Keelhaul never parses or rewrites file bytes itself.
"""

from __future__ import annotations

import os


def cleaned_name(path: str) -> str:
    root, ext = os.path.splitext(path)
    return f"{root}.cleaned{ext}"


def mat2_argv(path: str) -> list[str]:
    return ["mat2", "--", path]


def exiftool_argv(path: str) -> list[str]:
    # -o writes a new file and leaves the original untouched.
    return ["exiftool", "-all=", "-o", cleaned_name(path), "--", path]


def collect(target: str, scope: str) -> list[str]:
    """scope: 'file', 'folder' (direct files only), 'recursive'."""
    if scope == "file":
        return [target] if os.path.isfile(target) else []
    files = []
    if scope == "folder":
        with os.scandir(target) as it:
            for e in it:
                if e.is_file(follow_symlinks=False) and ".cleaned." not in e.name:
                    files.append(e.path)
    else:
        for root, dirs, names in os.walk(target, followlinks=False):
            dirs.sort()
            for n in sorted(names):
                p = os.path.join(root, n)
                if not os.path.islink(p) and ".cleaned." not in n:
                    files.append(p)
    return sorted(files)
