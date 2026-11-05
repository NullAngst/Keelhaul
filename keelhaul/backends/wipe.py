"""Disk Destroyer orchestration.

Wipe mode picks the proper hardware erase for the drive type and falls back
step by step. Destroy mode overwrites the whole device with random data
pass after pass until the device stops accepting writes or you stop it.

Every step runs as a RootJob through one RootSession per run.
"""

from __future__ import annotations

import os
import re
import secrets as pysecrets
import time
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, pyqtSignal

from ..core.devices import Device, human_size
from ..core.runner import RootJob

SHRED_PCT = re.compile(r"pass (\d+)/(\d+).*?(\d+)%\s*$")
DD_BYTES = re.compile(r"^(\d+) bytes")


def shred_progress(line: str) -> float | None:
    m = SHRED_PCT.search(line)
    if not m:
        return None
    p, total, pct = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return min(1.0, ((p - 1) + pct / 100.0) / max(total, 1))


@dataclass
class HdparmSecurity:
    supported: bool = False
    enabled: bool = False
    frozen: bool = True
    enhanced: bool = False
    estimate: str = ""


def parse_hdparm_identify(text: str) -> HdparmSecurity:
    sec = HdparmSecurity()
    idx = text.find("Security:")
    if idx < 0:
        return sec
    block = text[idx:]
    end = re.search(r"\n\S", block[10:])
    if end:
        block = block[: end.start() + 10]
    sec.supported = re.search(r"^\s+supported\s*$", block, re.M) is not None
    sec.enabled = re.search(r"^\s+enabled\s*$", block, re.M) is not None
    sec.frozen = re.search(r"not\s+frozen", block) is None
    sec.enhanced = "supported: enhanced erase" in block
    est = re.search(r"(\d+min) for (ENHANCED )?SECURITY ERASE", block)
    sec.estimate = est.group(1) if est else ""
    return sec


@dataclass
class Record:
    """Everything that goes into the optional wipe report."""
    device: str
    model: str
    serial: str
    size: str
    mode: str
    started: str = ""
    finished: str = ""
    steps: list[str] = field(default_factory=list)
    result: str = ""
    passes: int = 0
    hdparm_password: str = ""

    def text(self) -> str:
        lines = [
            "Keelhaul drive wipe report",
            "",
            f"Device:   {self.device}",
            f"Model:    {self.model or 'unknown'}",
            f"Serial:   {self.serial or 'unknown'}",
            f"Size:     {self.size}",
            f"Mode:     {self.mode}",
            f"Started:  {self.started}",
            f"Finished: {self.finished}",
        ]
        if self.passes:
            lines.append(f"Passes:   {self.passes}")
        lines += ["", "Steps:"] + [f"  {s}" for s in self.steps] + ["", f"Result: {self.result}"]
        if self.hdparm_password:
            lines += ["", "ATA security password set during erase (if the drive is locked, "
                      f"unlock with hdparm --security-disable): {self.hdparm_password}"]
        return "\n".join(lines) + "\n"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %Z")


class WipeRunner(QObject):
    """One drive. Emits progress 0..1, or -1 for an indeterminate step."""

    log = pyqtSignal(str)
    progress = pyqtSignal(float)
    status = pyqtSignal(str)
    done = pyqtSignal(bool)
    job_started = pyqtSignal(object)

    def __init__(self, session, dev: Device, target: str, mode: str, caps,
                 passes: int = 1, zero_last: bool = False, throttle: bool = True) -> None:
        super().__init__()
        self.session = session
        self.dev = dev
        self.target = target            # by-id path when available
        self.mode = mode                # "wipe" or "destroy"
        self.caps = caps
        self.passes = max(1, passes)
        self.zero_last = zero_last
        self.throttle = throttle
        self.job: RootJob | None = None
        self.stopped = False
        self.record = Record(dev.path + (f" ({target})" if target != dev.path else ""),
                             dev.model, dev.serial, human_size(dev.size),
                             "Wipe (clear, drive stays usable)" if mode == "wipe"
                             else "Destroy (write until dead)")
        self._plan: list = []
        self._dpass = 0
        self._fails_in_row = 0
        self._ident = ""
        self._fail_reason = ""
        session.failed.connect(self._auth_failed)

    def _auth_failed(self, msg: str) -> None:
        self._fail_reason = "Authentication failed: " + msg + " Nothing was written."
        self.stopped = True

    # ---- control ------------------------------------------------------
    def start(self) -> None:
        self.record.started = _now()
        if self.mode == "destroy":
            self._destroy_pass()
            return
        k = self.dev.kind
        if k == "nvme":
            self._plan = [self._nvme_format, self._discard, self._overwrite]
        elif k == "sata-ssd":
            self._plan = [self._hdparm_probe, self._discard, self._overwrite]
        else:
            self._plan = [self._overwrite]
        self._next()

    def stop(self) -> None:
        self.stopped = True
        if self.job:
            self.job.stop()

    def _finish(self, ok: bool, result: str) -> None:
        self.record.finished = _now()
        self.record.result = result
        self.status.emit(result)
        self.log.emit(result)
        self.done.emit(ok)

    def _next(self) -> None:
        if self._fail_reason:
            self._finish(False, self._fail_reason)
            return
        if self.stopped:
            self._finish(False, "Stopped before finishing. The drive may be partly wiped.")
            return
        if not self._plan:
            self._finish(False, "Every wipe method failed. The drive was not fully wiped.")
            return
        step = self._plan.pop(0)
        step()

    def _run(self, label: str, argv: list[str], on_exit, parser=None,
             log_argv: list[str] | None = None, throttle: bool | None = None) -> None:
        if not os.path.exists(self.target):
            self._finish(False, f"{self.target} disappeared before {label}.")
            return
        self.status.emit(label)
        self.record.steps.append(f"{_now()}  {label}: {' '.join(log_argv or argv)}")
        job = RootJob(self.session, label, argv, throttle=self.throttle if throttle is None else throttle,
                      log_argv=log_argv)
        self.job = job
        out: list[str] = []

        def on_line(s: str) -> None:
            out.append(s)
            self.log.emit(s)
            if parser:
                p = parser(s)
                if p is not None:
                    self.progress.emit(p)

        job.line.connect(on_line)
        job.finished.connect(lambda code: on_exit(code, "\n".join(out)))
        if parser is None:
            self.progress.emit(-1.0)
        self.job_started.emit(job)
        job.start()

    # ---- wipe steps ---------------------------------------------------
    def _step_result(self, code: int, what: str) -> None:
        if code == 0 and not self.stopped:
            self.record.steps.append(f"{_now()}  {what} succeeded")
            self.progress.emit(1.0)
            self._finish(True, f"{what} finished. The drive is empty and still usable.")
        else:
            self.record.steps.append(f"{_now()}  {what} failed (exit {code})")
            self.log.emit(f"{what} failed (exit {code}), trying the next method.")
            self._next()

    def _nvme_format(self) -> None:
        if not self.caps.has("nvme"):
            self._next()
            return
        self._run("NVMe format with user data erase",
                  ["nvme", "format", self.target, "--ses=1", "--force"],
                  lambda c, _o: self._step_result(c, "NVMe secure erase"))

    def _discard(self) -> None:
        if not self.caps.has("blkdiscard"):
            self._next()
            return
        self._run("Whole-device discard (TRIM)", ["blkdiscard", "-f", "-v", self.target],
                  lambda c, _o: self._step_result(c, "Whole-device discard"))

    def _overwrite(self) -> None:
        argv = ["shred", "-v", "-n", str(self.passes)]
        if self.zero_last:
            argv.append("-z")
        argv += ["--", self.target]
        label = f"Overwrite with random data ({self.passes} pass{'es' if self.passes > 1 else ''})"
        self._run(label, argv, lambda c, _o: self._step_result(c, "Overwrite"), shred_progress)

    def _hdparm_probe(self) -> None:
        if not self.caps.has("hdparm"):
            self._next()
            return
        self._run("Read ATA security state", ["hdparm", "-I", self.target], self._hdparm_decide)

    def _hdparm_decide(self, code: int, out: str) -> None:
        sec = parse_hdparm_identify(out) if code == 0 else HdparmSecurity()
        if not sec.supported:
            self.log.emit("Drive does not report ATA Security support.")
            self._next()
            return
        if sec.enabled:
            self.log.emit("A drive security password is already set. Skipping ATA Secure Erase.")
            self._next()
            return
        if sec.frozen:
            self.log.emit("Drive security is frozen (most BIOSes freeze it at boot). "
                          "Suspend and resume the machine, or hot-plug the drive, to unfreeze it. "
                          "Falling back to discard.")
            self._next()
            return
        pw = "kh" + pysecrets.token_hex(6)
        self.record.hdparm_password = pw
        self.log.emit(f"Setting temporary ATA password {pw}. If the erase fails, the drive may "
                      "stay locked with it. It is saved in the wipe report.")
        mask = ["hdparm", "--user-master", "u", "--security-set-pass", "****", self.target]
        self._run("Set temporary ATA security password",
                  ["hdparm", "--user-master", "u", "--security-set-pass", pw, self.target],
                  lambda c, _o: self._hdparm_erase(c, pw, sec), log_argv=mask)

    def _hdparm_erase(self, code: int, pw: str, sec: HdparmSecurity) -> None:
        if code != 0:
            self.log.emit("Could not set the ATA password.")
            self.record.hdparm_password = ""
            self._next()
            return
        flag = "--security-erase-enhanced" if sec.enhanced else "--security-erase"
        label = ("ATA Enhanced Secure Erase" if sec.enhanced else "ATA Secure Erase")
        if sec.estimate:
            label += f" (drive estimates {sec.estimate})"
        mask = ["hdparm", "--user-master", "u", flag, "****", self.target]
        self._run(label, ["hdparm", "--user-master", "u", flag, pw, self.target],
                  lambda c, _o: self._hdparm_after(c, pw), log_argv=mask, throttle=False)

    def _hdparm_after(self, code: int, pw: str) -> None:
        if code == 0:
            self.record.hdparm_password = ""  # erase clears the password
            self._step_result(0, "ATA Secure Erase")
            return
        self.log.emit("Secure erase failed. Trying to remove the temporary password.")
        mask = ["hdparm", "--user-master", "u", "--security-disable", "****", self.target]
        self._run("Remove temporary ATA password",
                  ["hdparm", "--user-master", "u", "--security-disable", pw, self.target],
                  lambda c, _o: self._after_disable(c), log_argv=mask)

    def _after_disable(self, code: int) -> None:
        if code == 0:
            self.record.hdparm_password = ""
        self._next()

    # ---- destroy ------------------------------------------------------
    def _destroy_pass(self) -> None:
        if self._fail_reason:
            self._finish(False, self._fail_reason)
            return
        if self.stopped:
            self._finish(False, f"Stopped by you after {self._dpass} full pass(es).")
            return
        if not os.path.exists(self.target):
            self._finish(True, f"Device disappeared after {self._dpass} pass(es). It is dead.")
            return
        self._dpass += 1
        self.record.passes = self._dpass
        self._run(f"Destroy pass {self._dpass}", ["shred", "-v", "-n", "1", "--", self.target],
                  self._destroy_after, shred_progress)

    def _destroy_after(self, code: int, out: str) -> None:
        if self._fail_reason:
            self._finish(False, self._fail_reason)
            return
        if self.stopped:
            self._finish(False, f"Stopped by you during pass {self._dpass}.")
            return
        if code == 0:
            self._fails_in_row = 0
            self._destroy_pass()
            return
        self._fails_in_row += 1
        gone = not os.path.exists(self.target)
        io_err = "Input/output error" in out or "No such device" in out
        if gone or (io_err and self._fails_in_row >= 2):
            self._finish(True, f"Device stopped responding during pass {self._dpass}. It is dead.")
            return
        if self._dpass == 1 and not io_err:
            self._finish(False, f"First pass failed (exit {code}). Nothing points to a dead drive, "
                                "so this looks like an error. Check the log.")
            return
        self.log.emit(f"Pass {self._dpass} hit write errors, going again.")
        self._destroy_pass()
