"""Removing an original after encrypt or scrub.

On a spinning disk with a non-COW filesystem, shred -u overwrites the file
in place and then unlinks it, which actually gets rid of the old bytes. On
an SSD (wear leveling) or btrfs/ZFS (copy on write), overwriting writes to
new blocks and the old ones stay put, so only a plain delete is honest
there, and the UI says that true erasure needs a whole-drive wipe.
"""

from __future__ import annotations

import os

from ..core import devices


def assess(path: str) -> tuple[str, str]:
    """('shred' or 'delete', reason shown to the user)."""
    m = devices.mount_for(path)
    if m and m.cow:
        return "delete", (f"{m.target} is {m.fstype}, a copy-on-write filesystem. Deleting the "
                          "original does not remove the old blocks, and snapshots may keep "
                          "them. True erasure needs a whole-drive wipe.")
    src = m.source if m else ""
    if src.startswith("/dev/") and devices.is_solid_state(src):
        return "delete", ("The original is on a solid-state drive. Wear leveling means deleting "
                          "it does not guarantee the old bytes are gone. True erasure needs a "
                          "whole-drive wipe.")
    if src.startswith("/dev/"):
        return "shred", "The original is on a spinning disk, so it is overwritten once and then deleted."
    return "delete", "Could not tell what the original is stored on, so it is only deleted."


def shred_argv(path: str) -> list[str]:
    return ["shred", "-u", "-n", "1", "--", path]


def files_under(folder: str) -> list[str]:
    out = []
    for root, _dirs, names in os.walk(folder):
        for n in names:
            p = os.path.join(root, n)
            if not os.path.islink(p):
                out.append(p)
    return out
