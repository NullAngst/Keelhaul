"""Secret handling: locked, zeroable buffers and process hardening.

Passphrases live in fixed-capacity bytearrays that never resize, so their
address never moves and the bytes can be overwritten in place. Python str
objects are immutable and cannot be zeroed, so the passphrase widget never
builds one for the whole secret (see ui/widgets.py for the one exception,
the strength meter).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import resource

_libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)

MCL_CURRENT = 1
MCL_FUTURE = 2
PR_SET_DUMPABLE = 4

# mlockall(MCL_FUTURE) makes every later allocation count against
# RLIMIT_MEMLOCK. A Qt process grows well past the usual 8 MiB default,
# so locking everything under a small limit makes allocations fail later
# and crashes the app. Only lock the whole process when the limit is this
# big or unlimited; otherwise lock the secret pages one by one.
MLOCKALL_MIN_LIMIT = 1024 * 1024 * 1024

DEFAULT_CAPACITY = 1024


class MemoryLockStatus:
    """What we managed to lock at startup, for the UI to report honestly."""

    def __init__(self) -> None:
        self.whole_process = False
        self.limit_bytes: int | None = None
        self.swap_on_disk = False
        self.swap_devices: list[str] = []
        self.error = ""

    @property
    def swap_risk(self) -> bool:
        """True when plaintext could reach a disk through swap."""
        return self.swap_on_disk and not self.whole_process

    def summary(self) -> str:
        if self.whole_process:
            return "Whole process memory is locked. Nothing can be swapped to disk."
        if not self.swap_on_disk:
            return ("Secret buffers are locked. No disk-backed swap is active, so "
                    "nothing can be swapped to disk right now.")
        lim = "unknown" if self.limit_bytes is None else f"{self.limit_bytes // 1024} KiB"
        return ("Only the secret buffers are locked (RLIMIT_MEMLOCK is "
                f"{lim}). Disk swap is active on {', '.join(self.swap_devices)}, "
                "so transient copies elsewhere in the interpreter could be "
                "swapped. Raise the memlock limit or turn off swap to close this.")


def _disk_swap_devices() -> list[str]:
    """Active swap that lands on a physical disk. zram is RAM, so it is skipped."""
    found = []
    try:
        with open("/proc/swaps", encoding="utf-8") as fh:
            next(fh, None)
            for line in fh:
                parts = line.split()
                if parts and "zram" not in parts[0]:
                    found.append(parts[0])
    except OSError:
        pass
    return found


def harden_process() -> MemoryLockStatus:
    """Disable core dumps, block same-user ptrace, and lock memory if we can."""
    status = MemoryLockStatus()

    # A core dump of this process could hold a passphrase. Children inherit this.
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        pass
    # Not dumpable also stops other processes of the same user from ptracing us.
    _libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)

    soft, _hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    status.limit_bytes = None if soft == resource.RLIM_INFINITY else soft
    status.swap_devices = _disk_swap_devices()
    status.swap_on_disk = bool(status.swap_devices)

    if soft == resource.RLIM_INFINITY or soft >= MLOCKALL_MIN_LIMIT:
        if _libc.mlockall(MCL_CURRENT | MCL_FUTURE) == 0:
            status.whole_process = True
        else:
            status.error = os.strerror(ctypes.get_errno())
    return status


class SecretBuffer:
    """A fixed-size, page-locked byte buffer that is overwritten on clear.

    The underlying bytearray is allocated once at full capacity and never
    resized, so CPython never copies it to a new address.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._buf = bytearray(capacity)
        self._len = 0
        self._locked = False
        self._cbuf = (ctypes.c_char * capacity).from_buffer(self._buf)
        if _libc.mlock(ctypes.addressof(self._cbuf), ctypes.c_size_t(capacity)) == 0:
            self._locked = True

    @property
    def locked(self) -> bool:
        return self._locked

    def __len__(self) -> int:
        return self._len

    @property
    def capacity(self) -> int:
        return len(self._buf)

    def append(self, data: bytes) -> bool:
        if self._len + len(data) > len(self._buf):
            return False
        self._buf[self._len:self._len + len(data)] = data
        self._len += len(data)
        return True

    def pop_char(self) -> None:
        """Remove the last UTF-8 character (not just the last byte)."""
        if self._len == 0:
            return
        i = self._len - 1
        while i > 0 and (self._buf[i] & 0xC0) == 0x80:
            i -= 1
        ctypes.memset(ctypes.addressof(self._cbuf) + i, 0, self._len - i)
        self._len = i

    def view(self) -> memoryview:
        """Zero-copy view of the live bytes. Do not keep it past the operation."""
        return memoryview(self._buf)[: self._len]

    def equals(self, other: "SecretBuffer") -> bool:
        if len(self) != len(other):
            return False
        diff = 0
        for a, b in zip(self.view(), other.view(), strict=True):
            diff |= a ^ b
        return diff == 0

    def copy(self) -> "SecretBuffer":
        dup = SecretBuffer(self.capacity)
        dup.append(self.view())
        return dup

    def clear(self) -> None:
        ctypes.memset(ctypes.addressof(self._cbuf), 0, len(self._buf))
        self._len = 0

    def destroy(self) -> None:
        self.clear()
        if self._locked:
            _libc.munlock(ctypes.addressof(self._cbuf), ctypes.c_size_t(len(self._buf)))
            self._locked = False

    def __del__(self) -> None:
        try:
            self.destroy()
        except Exception:
            pass
