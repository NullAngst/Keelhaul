"""Hash Check backends: coreutils sha256sum, sha512sum, b2sum."""

from __future__ import annotations

import hmac
import os
import re

ALGORITHMS = {"SHA-256": "sha256sum", "SHA-512": "sha512sum", "BLAKE2b-512": "b2sum"}
SUM_FILE_HINTS = {"sha256sum": ("sha256", "SHA256"), "sha512sum": ("sha512", "SHA512"),
                  "b2sum": ("b2", "B2", "blake2", "BLAKE2")}
_HEX = re.compile(r"^[0-9a-fA-F]+$")


def hash_argv(tool: str, path: str) -> list[str]:
    return [tool, "--", path]


def check_argv(tool: str, sums_file: str) -> list[str]:
    # --ignore-missing: a distro SHA256SUMS lists every ISO, you have one of them.
    # GNU coreutils 8.25+, which every desktop distro ships.
    return [tool, "-c", "--ignore-missing", "--", os.path.basename(sums_file)]


def parse_digest(line: str) -> str | None:
    """First token of a coreutils output line, if it looks like a digest."""
    tok = line.strip().split(" ", 1)[0].lstrip("\\")
    return tok.lower() if tok and _HEX.match(tok) else None


def normalize_expected(text: str) -> str:
    """Accept a bare hash or a pasted 'hash  filename' line."""
    tok = text.strip().split()[0] if text.strip() else ""
    return tok.lower()


def matches(actual: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(actual.lower(), expected.lower())


def guess_tool_for_sums(path: str) -> str:
    name = os.path.basename(path)
    for tool, hints in SUM_FILE_HINTS.items():
        if any(h in name for h in hints):
            return tool
    return "sha256sum"
