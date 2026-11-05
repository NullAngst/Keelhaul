"""Backend capability probe.

Keelhaul bundles none of the security binaries. At launch it looks for each
one on the system PATH and decides, per tab, whether the tab is ready,
degraded (fallback only) or disabled.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import env

TOOLS = [
    "cryptsetup", "age", "gpg", "mat2", "exiftool", "sha256sum", "sha512sum",
    "b2sum", "nvme", "hdparm", "blkdiscard", "lsblk", "shred", "dd", "sfill",
    "tar", "pkexec", "sudo", "python3", "nice", "ionice",
]

# Package names per family. A missing entry means the tool name is the package.
PACKAGES = {
    "gpg": {"apt": "gnupg", "dnf": "gnupg2", "zypper": "gpg2", "pacman": "gnupg",
            "apk": "gnupg", "emerge": "app-crypt/gnupg"},
    "exiftool": {"apt": "libimage-exiftool-perl", "dnf": "perl-Image-ExifTool",
                 "zypper": "exiftool", "pacman": "perl-image-exiftool",
                 "apk": "exiftool", "emerge": "media-libs/exiftool"},
    "nvme": {"emerge": "sys-apps/nvme-cli", "*": "nvme-cli"},
    "cryptsetup": {"emerge": "sys-fs/cryptsetup"},
    "age": {"emerge": "app-crypt/age"},
    "mat2": {"emerge": "app-misc/mat2"},
    "hdparm": {"emerge": "sys-apps/hdparm"},
    "sfill": {"apt": "secure-delete", "pacman": "secure-delete (AUR)",
              "*": "secure-delete (not packaged on every distro)"},
    "pkexec": {"apt": "pkexec", "dnf": "polkit", "zypper": "polkit",
               "pacman": "polkit", "apk": "polkit", "emerge": "sys-auth/polkit"},
    "sudo": {"emerge": "app-admin/sudo"},
    "python3": {"pacman": "python", "emerge": "dev-lang/python"},
}
for _t in ("sha256sum", "sha512sum", "b2sum", "shred", "dd", "nice"):
    PACKAGES[_t] = {"emerge": "sys-apps/coreutils", "*": "coreutils"}
for _t in ("blkdiscard", "lsblk", "ionice"):
    PACKAGES[_t] = {"emerge": "sys-apps/util-linux", "*": "util-linux"}

INSTALL_CMD = {
    "apt": "sudo apt install", "dnf": "sudo dnf install", "zypper": "sudo zypper install",
    "pacman": "sudo pacman -S", "apk": "sudo apk add", "emerge": "sudo emerge --ask",
    "xbps": "sudo xbps-install",
}


def package_for(tool: str, family: str) -> str:
    names = PACKAGES.get(tool, {})
    return names.get(family) or names.get("*") or tool


def install_hint(tools: list[str], family: str) -> str:
    pkgs = []
    for t in tools:
        p = package_for(t, family)
        if p not in pkgs:
            pkgs.append(p)
    cmd = INSTALL_CMD.get(family)
    if cmd:
        return f"{cmd} {' '.join(pkgs)}"
    return "Install with your package manager: " + ", ".join(pkgs)


READY, DEGRADED, DISABLED = "ready", "degraded", "disabled"


@dataclass
class TabStatus:
    state: str
    note: str = ""
    missing: list[str] = field(default_factory=list)


@dataclass
class Capabilities:
    paths: dict[str, str | None]
    family: str

    def has(self, tool: str) -> bool:
        return bool(self.paths.get(tool))

    @property
    def can_escalate(self) -> bool:
        return self.has("pkexec") or self.has("sudo")

    def tab(self, name: str) -> TabStatus:
        h = self.has
        esc = [] if self.can_escalate else ["pkexec"]
        if name == "crypto":
            miss = [t for t in ("cryptsetup", "lsblk") if not h(t)] + esc
            return TabStatus(DISABLED if miss else READY,
                             "" if not miss else "Needs cryptsetup, lsblk and pkexec or sudo.", miss)
        if name == "vault":
            if h("age"):
                return TabStatus(READY, "" if h("gpg") else "gpg fallback not found.")
            if h("gpg"):
                return TabStatus(DEGRADED, "age not found, using gpg only.", ["age"])
            return TabStatus(DISABLED, "Needs age or gpg.", ["age", "gpg"])
        if name == "scrub":
            if h("mat2"):
                return TabStatus(READY)
            if h("exiftool"):
                return TabStatus(DEGRADED, "mat2 not found, using exiftool only.", ["mat2"])
            return TabStatus(DISABLED, "Needs mat2 or exiftool.", ["mat2", "exiftool"])
        if name == "hash":
            if h("sha256sum") or h("b2sum"):
                return TabStatus(READY)
            return TabStatus(DISABLED, "Needs coreutils.", ["sha256sum"])
        if name == "destroy":
            miss = [t for t in ("lsblk", "shred") if not h(t)] + esc
            if miss:
                return TabStatus(DISABLED, "Needs lsblk, shred and pkexec or sudo.", miss)
            hw = [t for t in ("nvme", "hdparm", "blkdiscard") if not h(t)]
            if hw:
                return TabStatus(DEGRADED, "Hardware erase tools missing: " + ", ".join(hw)
                                 + ". SSD wipes fall back to overwrite.", hw)
            return TabStatus(READY)
        if name == "freespace":
            if h("sfill"):
                return TabStatus(READY)
            if h("dd"):
                return TabStatus(DEGRADED, "sfill not found, using a dd random-fill file.", ["sfill"])
            return TabStatus(DISABLED, "Needs sfill or dd.", ["sfill"])
        raise KeyError(name)


def probe() -> Capabilities:
    return Capabilities({t: env.which(t) for t in TOOLS}, env.distro_family())
