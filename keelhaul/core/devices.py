"""Block device and filesystem probing via lsblk and /proc/self/mounts."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field

from . import env

PROTECTED_MOUNTS = {"/", "/boot", "/boot/efi", "/efi", "/usr", "/var"}
COW_FS = {"btrfs", "zfs", "bcachefs"}
REAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "vfat", "exfat", "ntfs",
           "ntfs3", "zfs", "jfs", "reiserfs", "bcachefs", "hfsplus", "nilfs2", "fuseblk"}
_COLS = "NAME,PATH,SIZE,TYPE,ROTA,TRAN,MODEL,SERIAL,FSTYPE,RM,RO,PKNAME"


@dataclass
class Device:
    name: str
    path: str
    size: int
    type: str
    rota: bool
    tran: str
    model: str
    serial: str
    fstype: str
    removable: bool
    readonly: bool
    mountpoints: list[str] = field(default_factory=list)
    children: list["Device"] = field(default_factory=list)
    parent: str = ""

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    @property
    def all_mounts(self) -> list[str]:
        return [m for d in self.walk() for m in d.mountpoints]

    @property
    def mounted(self) -> bool:
        return bool(self.all_mounts)

    @property
    def protected(self) -> bool:
        """Holds the running system: root, boot, usr, var, or active swap."""
        return any(m in PROTECTED_MOUNTS or m == "[SWAP]" for m in self.all_mounts)

    @property
    def kind(self) -> str:
        """nvme, sata-ssd, hdd, or usb/other for wipe method choice."""
        if self.tran == "nvme" or self.name.startswith("nvme"):
            return "nvme"
        if not self.rota and self.tran in ("sata", "ata"):
            return "sata-ssd"
        if self.rota:
            return "hdd"
        return "flash"

    @property
    def is_luks(self) -> bool:
        return self.fstype == "crypto_LUKS"

    def describe(self) -> str:
        parts = [self.path, human_size(self.size)]
        if self.model:
            parts.append(self.model)
        if self.serial:
            parts.append(f"S/N {self.serial}")
        if self.fstype:
            parts.append(self.fstype)
        return "  ".join(parts)


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024 or unit == "PiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{n} B"


def _run_lsblk(mount_col: str) -> dict:
    exe = env.which("lsblk")
    if not exe:
        raise FileNotFoundError("lsblk not found")
    out = subprocess.run([exe, "-J", "-b", "-o", f"{_COLS},{mount_col}"],
                         capture_output=True, text=True, env=env.child_env(), check=True)
    return json.loads(out.stdout)


def _parse(node: dict, parent: str = "") -> Device:
    mps = node.get("mountpoints")
    if mps is None:
        mp = node.get("mountpoint")
        mps = [mp] if mp else []
    dev = Device(
        name=node.get("name") or "",
        path=node.get("path") or "/dev/" + (node.get("name") or ""),
        size=int(node.get("size") or 0),
        type=node.get("type") or "",
        rota=bool(node.get("rota")) if not isinstance(node.get("rota"), str)
        else node.get("rota") == "1",
        tran=(node.get("tran") or "").lower(),
        model=(node.get("model") or "").strip(),
        serial=(node.get("serial") or "").strip(),
        fstype=node.get("fstype") or "",
        removable=bool(node.get("rm")) if not isinstance(node.get("rm"), str)
        else node.get("rm") == "1",
        readonly=bool(node.get("ro")) if not isinstance(node.get("ro"), str)
        else node.get("ro") == "1",
        mountpoints=[m for m in mps if m],
        parent=parent,
    )
    dev.children = [_parse(c, dev.name) for c in node.get("children", [])]
    return dev


def list_disks() -> list[Device]:
    """Top-level disks with their partitions and holders as children."""
    try:
        data = _run_lsblk("MOUNTPOINTS")
    except subprocess.CalledProcessError:
        data = _run_lsblk("MOUNTPOINT")  # util-linux older than 2.37
    disks = []
    for node in data.get("blockdevices", []):
        dev = _parse(node)
        if dev.type in ("disk",) and dev.size > 0 and not dev.name.startswith(("zram", "loop")):
            disks.append(dev)
    return disks


def list_targets_for_crypto() -> list[Device]:
    """Disks and partitions a LUKS container can live on."""
    out = []
    for d in list_disks():
        for dev in d.walk():
            if dev.type in ("disk", "part"):
                out.append(dev)
    return out


def by_id(path: str) -> str:
    """A stable /dev/disk/by-id link for path, or path itself.

    Kernel names like sdb shift when drives are plugged in a different order.
    A by-id link names the drive by model and serial, so if the drive is
    swapped between confirm and start, the link stops resolving instead of
    pointing at the wrong disk.
    """
    base = "/dev/disk/by-id"
    real = os.path.realpath(path)
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return path
    best = None
    for n in names:
        if os.path.realpath(os.path.join(base, n)) != real:
            continue
        rank = 0 if n.startswith(("nvme-", "ata-", "scsi-", "usb-", "mmc-")) and "eui." not in n else 1
        if best is None or rank < best[0]:
            best = (rank, n)
    return os.path.join(base, best[1]) if best else path


def reverify(dev: Device) -> str | None:
    """Re-probe right before a destructive step. Returns an error or None."""
    try:
        now = {d.path: d for d in list_targets_for_crypto()}
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"Could not re-check devices: {exc}"
    cur = now.get(dev.path)
    if cur is None:
        return f"{dev.path} is gone."
    if (cur.serial, cur.size, cur.model) != (dev.serial, dev.size, dev.model):
        return (f"{dev.path} changed since you confirmed it "
                f"(now {cur.describe()}). Refusing to continue.")
    return None


def is_solid_state(path: str) -> bool:
    """True if any device under path (or the path's own disk) is non-rotational."""
    exe = env.which("lsblk")
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "-s", "-n", "-o", "ROTA", path], capture_output=True,
                             text=True, env=env.child_env(), check=True)
    except (subprocess.CalledProcessError, OSError):
        return False
    vals = [v.strip() for v in out.stdout.split() if v.strip()]
    return "0" in vals


def device_for_file(path: str) -> str | None:
    """Block device backing the filesystem that holds path."""
    m = mount_for(path)
    return m.source if m and m.source.startswith("/dev/") else None


@dataclass
class Mount:
    target: str
    source: str
    fstype: str

    @property
    def cow(self) -> bool:
        return self.fstype in COW_FS


def _unescape(s: str) -> str:
    return s.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def list_mounts() -> list[Mount]:
    out = []
    seen = set()
    try:
        with open("/proc/self/mounts", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                src, tgt, fs = _unescape(parts[0]), _unescape(parts[1]), parts[2]
                if fs not in REAL_FS or tgt in seen:
                    continue
                seen.add(tgt)
                out.append(Mount(tgt, src, fs))
    except OSError:
        pass
    return out


def mount_for(path: str) -> Mount | None:
    path = os.path.realpath(path)
    best = None
    for m in list_mounts():
        t = m.target.rstrip("/") or "/"
        if path == t or path.startswith(t.rstrip("/") + "/") or t == "/":
            if best is None or len(t) > len(best.target):
                best = m
    return best
