"""cryptsetup (LUKS2) argv builders.

Secrets are passed as @FDPATH0@ / @FDPATH1@ placeholders that the runner or
root helper swaps for /dev/fd/N pipe paths. Using --key-file on a pipe means
cryptsetup takes the exact bytes typed, with no newline handling, which
matches what an interactive unlock at boot will read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAPPER_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

CIPHERS = ["aes-xts-plain64", "serpent-xts-plain64", "twofish-xts-plain64"]
KEY_SIZES = [512, 256]  # XTS splits the key, so 512 bits is AES-256
SECTOR_SIZES = [4096, 512]
PBKDFS = ["argon2id", "argon2i", "pbkdf2"]

ITER_TIME_FLOOR_MS = 1000
MEMORY_FLOOR_KIB = 256 * 1024
MEMORY_MAX_KIB = 4 * 1024 * 1024


@dataclass
class FormatOptions:
    cipher: str = "aes-xts-plain64"
    key_size: int = 512
    sector_size: int = 4096
    pbkdf: str = "argon2id"
    iter_time_ms: int = 2000
    memory_kib: int = 1024 * 1024
    parallel: int = 4
    label: str = ""

    def pbkdf_args(self) -> list[str]:
        args = ["--pbkdf", self.pbkdf,
                "--iter-time", str(max(self.iter_time_ms, ITER_TIME_FLOOR_MS))]
        if self.pbkdf.startswith("argon2"):
            mem = min(max(self.memory_kib, MEMORY_FLOOR_KIB), MEMORY_MAX_KIB)
            args += ["--pbkdf-memory", str(mem), "--pbkdf-parallel", str(max(1, self.parallel))]
        return args


def format_argv(dev: str, o: FormatOptions) -> list[str]:
    argv = ["cryptsetup", "luksFormat", "--type", "luks2", "--batch-mode",
            "--cipher", o.cipher, "--key-size", str(o.key_size),
            "--sector-size", str(o.sector_size)] + o.pbkdf_args()
    if o.label:
        argv += ["--label", o.label]
    return argv + ["--key-file=@FDPATH0@", "--", dev]


def open_argv(dev: str, name: str) -> list[str]:
    return ["cryptsetup", "open", "--type", "luks", "--key-file=@FDPATH0@", "--", dev, name]


def close_argv(name: str) -> list[str]:
    return ["cryptsetup", "close", "--", name]


def addkey_argv(dev: str, o: FormatOptions) -> list[str]:
    return (["cryptsetup", "luksAddKey"] + o.pbkdf_args()
            + ["--key-file=@FDPATH0@", "--", dev, "@FDPATH1@"])


def changekey_argv(dev: str, o: FormatOptions) -> list[str]:
    return (["cryptsetup", "luksChangeKey"] + o.pbkdf_args()
            + ["--key-file=@FDPATH0@", "--", dev, "@FDPATH1@"])


def removekey_argv(dev: str) -> list[str]:
    # No --batch-mode: removing the last key slot asks for confirmation on a
    # closed stdin, so cryptsetup refuses instead of locking you out.
    return ["cryptsetup", "luksRemoveKey", "--key-file=@FDPATH0@", "--", dev]


def dump_argv(dev: str) -> list[str]:
    return ["cryptsetup", "luksDump", "--", dev]


def header_backup_argv(dev: str, out: str) -> list[str]:
    return ["cryptsetup", "luksHeaderBackup", "--header-backup-file", out, "--", dev]


def header_restore_argv(dev: str, src: str) -> list[str]:
    return ["cryptsetup", "luksHeaderRestore", "--batch-mode", "--header-backup-file", src, "--", dev]
