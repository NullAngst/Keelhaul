"""Per-operation privilege escalation.

The GUI never runs as root. When an operation needs root, a RootSession
starts the helper (core/root_helper.py) under pkexec, or under sudo -A on
systems without a working polkit agent. One session serves one operation,
so a multi-drive wipe asks for authentication once, and the helper exits
when the operation is over. No persistent root, no setuid.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from . import env, logbook
from .runner import RootJob

HELPER_SRC = (Path(__file__).with_name("root_helper.py")).read_text(encoding="utf-8")

PKEXEC_DISMISSED = 126
PKEXEC_NOT_AUTHORIZED = 127


def _askpass_script() -> str:
    """Write a tiny SUDO_ASKPASS wrapper that re-enters Keelhaul in askpass mode.

    It lives in a fresh 0700 directory under XDG_RUNTIME_DIR (tmpfs) and
    holds no secret, only the command to show the password dialog.
    """
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    d = tempfile.mkdtemp(prefix="keelhaul-askpass.", dir=base)
    path = os.path.join(d, "askpass")
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        cmd = f'exec "{appimage}" --askpass "$@"\n'
    else:
        pkg_parent = str(Path(__file__).resolve().parents[2])
        cmd = (f'PYTHONPATH="{pkg_parent}" exec "{sys.executable}" '
               f'-m keelhaul --askpass "$@"\n')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n" + cmd)
    os.chmod(path, stat.S_IRWXU)
    return path


class RootSession(QObject):
    ready = pyqtSignal()
    failed = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, method: str = "auto") -> None:
        super().__init__()
        self.method = method
        self.proc: subprocess.Popen | None = None
        self.jobs: dict[str, RootJob] = {}
        self.is_ready = False
        self.dead = False
        self._queue: list[RootJob] = []
        self._lock = threading.RLock()  # submit() may call open(), which may fail()
        self._tried: list[str] = []
        self._askpass: str | None = None

    # ---- launch -------------------------------------------------------
    def _methods(self) -> list[str]:
        if self.method in ("pkexec", "sudo"):
            return [self.method]
        return [m for m in ("pkexec", "sudo") if env.which(m)]

    def open(self) -> None:
        remaining = [m for m in self._methods() if m not in self._tried]
        if not remaining:
            self._fail("No working privilege escalation. Install polkit (pkexec) or sudo.")
            return
        python = env.which("python3")
        if not python:
            self._fail("The root helper needs the system python3, which was not found.")
            return
        method = remaining[0]
        self._tried.append(method)
        base = [python, "-I", "-c", HELPER_SRC]
        cenv = env.child_env()
        if method == "pkexec":
            argv = [env.which("pkexec") or "pkexec"] + base
        else:
            self._askpass = _askpass_script()
            cenv["SUDO_ASKPASS"] = self._askpass
            argv = [env.which("sudo") or "sudo", "-A", "--"] + base
        logbook.record(f"escalate via {method}")
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, env=cenv)
        except OSError as exc:
            self._cleanup_askpass()
            self._fail(f"Could not start {method}: {exc}")
            return
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            self._dispatch(ev)
        rc = proc.wait()
        err = proc.stderr.read().decode("utf-8", "replace").strip() if proc.stderr else ""
        self._cleanup_askpass()
        if not self.is_ready:
            no_agent = rc == PKEXEC_NOT_AUTHORIZED and "agent" in err.lower()
            if self._tried[-1] == "pkexec" and no_agent and env.which("sudo"):
                self.open()
                return
            if rc == PKEXEC_DISMISSED or "dismissed" in err.lower():
                msg = "Authentication was cancelled."
            else:
                msg = err or f"Escalation failed (exit {rc})."
            self._fail(msg)
            return
        self.dead = True
        # Helper died with jobs still marked running: report them as failed.
        for job in list(self.jobs.values()):
            if job.running:
                job._done(-1)
        self.closed.emit()

    def _fail(self, msg: str) -> None:
        self.dead = True
        # Failed first, so listeners know why before the jobs report exit -1.
        self.failed.emit(msg)
        with self._lock:
            queued, self._queue = self._queue, []
        for job in queued:
            job.line.emit("Error: " + msg)
            for s in job.secrets:
                s.destroy()
            job.secrets = []
            job._done(-1)

    def _dispatch(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "ready":
            self.is_ready = True
            self.ready.emit()
            with self._lock:
                queued, self._queue = self._queue, []
            for job in queued:
                self._send_start(job)
            return
        job = self.jobs.get(ev.get("id", ""))
        if job is None:
            return
        if kind == "started":
            job._on_started(int(ev["pid"]))
        elif kind == "out":
            job.line.emit(ev.get("data", ""))
        elif kind == "error":
            job.line.emit("Error: " + ev.get("message", ""))
        elif kind == "exit":
            job._done(int(ev.get("code", -1)))

    def _cleanup_askpass(self) -> None:
        if self._askpass:
            try:
                os.unlink(self._askpass)
                os.rmdir(os.path.dirname(self._askpass))
            except OSError:
                pass
            self._askpass = None

    # ---- jobs ---------------------------------------------------------
    def submit(self, job: RootJob) -> None:
        if self.dead:
            for s in job.secrets:
                s.destroy()
            job.line.emit("Error: root helper is not running.")
            job._done(-1)
            return
        self.jobs[job.id] = job
        with self._lock:
            if not self.is_ready:
                self._queue.append(job)
                if self.proc is None:
                    self.open()
                return
        self._send_start(job)

    def _write(self, data) -> bool:
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            return False
        try:
            self.proc.stdin.write(data)
            return True
        except (BrokenPipeError, OSError):
            return False

    def _send_start(self, job: RootJob) -> None:
        req = {"cmd": "start", "id": job.id, "argv": job.argv,
               "secrets": [len(s) for s in job.secrets], "nice": job.throttle,
               "chown_after": job.chown_after, "remove_after": job.remove_after}
        with self._lock:
            ok = self._write((json.dumps(req) + "\n").encode())
            for s in job.secrets:
                ok = ok and self._write(s.view())
            if ok:
                self.proc.stdin.flush()
        for s in job.secrets:
            s.destroy()
        job.secrets = []
        if not ok:
            job.line.emit("Error: root helper is not running.")
            job._done(-1)

    def send_signal(self, job: RootJob, sig: str) -> None:
        with self._lock:
            if self._write((json.dumps({"cmd": "signal", "id": job.id, "sig": sig}) + "\n").encode()):
                self.proc.stdin.flush()

    def close(self) -> None:
        """No more jobs. The helper exits once its running jobs finish."""
        with self._lock:
            if self.proc and self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except OSError:
                    pass
