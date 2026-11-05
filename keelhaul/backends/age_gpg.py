"""File Vault backends: age (primary) and gpg --symmetric (fallback).

age only reads passphrases from its controlling terminal, so the runner
gives it a pseudo-terminal and types the passphrase into the prompt. gpg
reads it from a pipe fd with loopback pinentry. Neither ever sees the
passphrase on argv.
"""

from __future__ import annotations

import os

AGE_MAGIC = (b"age-encryption.org/v1", b"-----BEGIN AGE ENCRYPTED FILE-----")


def detect(path: str) -> str:
    """'age' or 'gpg' from the file header."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return "gpg"
    return "age" if head.startswith(AGE_MAGIC) else "gpg"


def tar_feeder(folder: str) -> list[str]:
    folder = os.path.abspath(folder.rstrip("/"))
    return ["tar", "-cf", "-", "-C", os.path.dirname(folder), "--", os.path.basename(folder)]


def encrypted_name(src: str, backend: str, armor: bool, is_folder: bool) -> str:
    base = os.path.abspath(src.rstrip("/")) + (".tar" if is_folder else "")
    if backend == "age":
        return base + ".age"
    return base + (".asc" if armor else ".gpg")


def decrypted_name(src: str) -> str:
    for ext in (".age", ".gpg", ".asc"):
        if src.endswith(ext):
            out = src[: -len(ext)]
            break
    else:
        out = src + ".decrypted"
    if os.path.exists(out):
        root, ext = os.path.splitext(out)
        n = 1
        while os.path.exists(f"{root}.{n}{ext}"):
            n += 1
        out = f"{root}.{n}{ext}"
    return out


def age_encrypt(out: str, src: str | None) -> list[str]:
    """src None means read from stdin (the tar feeder)."""
    return ["age", "--passphrase", "-o", out] + (["--", src] if src else [])


def age_decrypt(out: str, src: str) -> list[str]:
    return ["age", "--decrypt", "-o", out, "--", src]


_GPG_BASE = ["gpg", "--batch", "--yes", "--no-symkey-cache", "--pinentry-mode", "loopback",
             "--passphrase-fd", "@FD0@"]


def gpg_encrypt(out: str, src: str | None, armor: bool) -> list[str]:
    argv = _GPG_BASE + ["--symmetric", "--cipher-algo", "AES256", "--s2k-mode", "3",
                        "--s2k-digest-algo", "SHA512", "--s2k-count", "65011712"]
    if armor:
        argv.append("--armor")
    argv += ["-o", out]
    return argv + (["--", src] if src else [])


def gpg_decrypt(out: str, src: str) -> list[str]:
    return _GPG_BASE + ["-o", out, "--decrypt", "--", src]
