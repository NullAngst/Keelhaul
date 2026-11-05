"""Subprocess runner. Every backend call goes through here.

Rules enforced in this module:
  * argv lists only, never shell=True, never a formatted command string
  * secrets travel through kernel pipes (or a pty for age), never argv
  * every child gets its own process group so the watchdog can pause it
  * the GUI thread never blocks: readers run on daemon threads and report
    back through Qt signals, which Qt queues onto the GUI thread
"""

from __future__ import annotations

import fcntl
import itertools
import os
import re
import signal
import subprocess
import termios
import threading

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import env, logbook
from .secrets import SecretBuffer

PLACEHOLDER = re.compile(r"@FD(PATH)?(\d+)@")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_SIGNALS = {"STOP": signal.SIGSTOP, "CONT": signal.SIGCONT,
            "TERM": signal.SIGTERM, "KILL": signal.SIGKILL}
_ids = itertools.count(1)


def split_lines(pending: bytes, chunk: bytes) -> tuple[list[str], bytes]:
    """Split on CR and LF so progress lines that overwrite themselves arrive."""
    pending += chunk
    parts = re.split(rb"[\r\n]", pending)
    rest = parts.pop()
    return [p.decode("utf-8", "replace") for p in parts if p], rest


class Job(QObject):
    """Base class. Subclasses implement _launch and _send_signal."""

    line = pyqtSignal(str)
    started = pyqtSignal()
    finished = pyqtSignal(int)
    paused_changed = pyqtSignal(bool)

    def __init__(self, label: str, throttle: bool = True) -> None:
        super().__init__()
        self.id = f"j{next(_ids)}"
        self.label = label
        self.throttle = throttle
        self.pgids: list[int] = []
        self.running = False
        self.paused = False
        self.exit_code: int | None = None
        self.log_argv: list[str] = []
        self._stopping = False

    # Called by the watchdog and the UI.
    def pause(self) -> None:
        if self.running and not self.paused:
            self._send_signal("STOP")
            self.paused = True
            self.paused_changed.emit(True)

    def resume(self) -> None:
        if self.running and self.paused:
            self._send_signal("CONT")
            self.paused = False
            self.paused_changed.emit(False)

    def stop(self) -> None:
        """TERM, then KILL if the process group is still around after 5 s."""
        if not self.running or self._stopping:
            return
        self._stopping = True
        if self.paused:
            self._send_signal("CONT")
        self._send_signal("TERM")
        QTimer.singleShot(5000, self._kill_if_alive)

    def _kill_if_alive(self) -> None:
        if self.running:
            self._send_signal("KILL")

    def start(self) -> None:
        logbook.record(f"start {self.label}: {' '.join(self.log_argv)}")
        self._launch()

    def _done(self, code: int) -> None:
        self.running = False
        self.exit_code = code
        logbook.record(f"exit {self.label}: code {code}")
        self.finished.emit(code)

    def _launch(self) -> None:
        raise NotImplementedError

    def _send_signal(self, sig: str) -> None:
        raise NotImplementedError


def _secret_pipes(secrets: list[SecretBuffer]) -> list[int]:
    """Write each secret into its own pipe and return the read ends."""
    fds = []
    for s in secrets:
        r, w = os.pipe()
        try:
            os.write(w, s.view())
        finally:
            os.close(w)
        fds.append(r)
    return fds


def _substitute(argv: list[str], fds: list[int]) -> list[str]:
    def sub(m: re.Match) -> str:
        fd = fds[int(m.group(2))]
        return f"/dev/fd/{fd}" if m.group(1) else str(fd)
    return [PLACEHOLDER.sub(sub, a) for a in argv]


def _nice_prefix() -> list[str]:
    prefix = []
    nice = env.which("nice")
    ionice = env.which("ionice")
    if nice:
        prefix += [nice, "-n", "10"]
    if ionice:
        prefix += [ionice, "-c", "2", "-n", "7"]
    return prefix


class UserJob(Job):
    """Runs a backend as the current user.

    feeder: optional argv whose stdout becomes this job's stdin (tar | age).
    pty_answers: run on a pseudo-terminal and type secret 0 into each prompt
    that matches pty_prompt, up to this many times. age only reads
    passphrases from its terminal, so this is how it gets one without argv.
    """

    def __init__(self, label: str, argv: list[str], secrets: list[SecretBuffer] | None = None,
                 feeder: list[str] | None = None, cwd: str | None = None,
                 throttle: bool = True, pty_answers: int = 0,
                 pty_prompt: str = r"passphrase[^\n]*:\s*$") -> None:
        super().__init__(label, throttle)
        self.argv = argv
        # Own copies, so the UI can clear its fields right away.
        self.secrets = [s.copy() for s in (secrets or [])]
        self.feeder = feeder
        self.cwd = cwd
        self.pty_answers = pty_answers
        self.pty_prompt = re.compile(pty_prompt, re.IGNORECASE)
        self.log_argv = (feeder + ["|"] if feeder else []) + argv
        self._procs: list[subprocess.Popen] = []

    def _resolve(self, argv: list[str]) -> list[str]:
        exe = env.which(argv[0])
        if not exe:
            raise FileNotFoundError(f"{argv[0]} not found")
        return (_nice_prefix() if self.throttle else []) + [exe] + argv[1:]

    def _launch(self) -> None:
        try:
            self._spawn()
        except OSError as exc:
            self.line.emit(f"Could not start {self.argv[0]}: {exc}")
            self._wipe_secrets()
            self._done(-1)

    def _spawn(self) -> None:
        cenv = env.child_env()
        stdin = subprocess.DEVNULL
        if self.feeder:
            feed = subprocess.Popen(self._resolve(self.feeder), stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, cwd=self.cwd, env=cenv,
                                    start_new_session=True)
            self._procs.append(feed)
            self.pgids.append(feed.pid)
            stdin = feed.stdout

        if self.pty_answers:
            master, slave = os.openpty()

            def take_tty() -> None:
                fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

            proc = subprocess.Popen(
                self._resolve(self.argv), stdin=stdin if self.feeder else slave,
                stdout=slave, stderr=slave, cwd=self.cwd, env=cenv,
                start_new_session=True, pass_fds=(slave,), preexec_fn=take_tty)
            os.close(slave)
            read_fd = master
        else:
            fds = _secret_pipes(self.secrets)
            try:
                proc = subprocess.Popen(
                    _substitute(self._resolve(self.argv), fds), stdin=stdin,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=self.cwd,
                    env=cenv, start_new_session=True, pass_fds=tuple(fds))
            finally:
                for fd in fds:
                    os.close(fd)
            self._wipe_secrets()
            read_fd = proc.stdout.fileno()
            master = None

        if self.feeder and self._procs[0].stdout:
            self._procs[0].stdout.close()
        self._procs.append(proc)
        self.pgids.append(proc.pid)
        self.running = True
        self.started.emit()
        threading.Thread(target=self._pump, args=(proc, read_fd, master), daemon=True).start()

    def _pump(self, proc: subprocess.Popen, fd: int, master: int | None) -> None:
        pending = b""
        answered = 0
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break  # pty master returns EIO once the child side closes
            if not chunk:
                break
            lines, pending = split_lines(pending, chunk)
            for ln in lines:
                if master is not None:
                    ln = ANSI.sub("", ln).strip()  # age redraws its prompt line
                if ln:
                    self.line.emit(ln)
            if master is not None and answered < self.pty_answers and self.secrets:
                tail = pending.decode("utf-8", "replace")
                if self.pty_prompt.search(tail):
                    os.write(master, self.secrets[0].view())
                    os.write(master, b"\n")
                    answered += 1
                    pending = b""
                    if answered >= self.pty_answers:
                        self._wipe_secrets()
        if pending:
            self.line.emit(pending.decode("utf-8", "replace"))
        if master is not None:
            os.close(master)
        self._wipe_secrets()
        code = proc.wait()
        for p in self._procs[:-1]:
            p.wait()
        self._done(code)

    def _wipe_secrets(self) -> None:
        for s in self.secrets:
            s.destroy()
        self.secrets = []

    def _send_signal(self, sig: str) -> None:
        for pg in self.pgids:
            try:
                os.killpg(pg, _SIGNALS[sig])
            except (ProcessLookupError, PermissionError):
                pass


class RootJob(Job):
    """Runs through a RootSession. Signals go to the helper, which is root."""

    def __init__(self, session, label: str, argv: list[str],
                 secrets: list[SecretBuffer] | None = None, throttle: bool = True,
                 chown_after: str | None = None, log_argv: list[str] | None = None,
                 remove_after: str | None = None) -> None:
        super().__init__(label, throttle)
        self.session = session
        self.argv = argv
        self.secrets = [s.copy() for s in (secrets or [])]
        self.chown_after = chown_after
        self.remove_after = remove_after
        self.log_argv = log_argv or argv

    def _launch(self) -> None:
        self.session.submit(self)

    def _on_started(self, pid: int) -> None:
        self.pgids = [pid]
        self.running = True
        self.started.emit()

    def _send_signal(self, sig: str) -> None:
        self.session.send_signal(self, sig)
