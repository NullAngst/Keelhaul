"""Keelhaul root helper.

This file is never imported by the GUI. Its source is read and handed to
the system python3 as `python3 -I -c <source>` under pkexec or sudo, so it
must stay self-contained (stdlib only, no keelhaul imports). Passing it on
argv instead of as a file means root never executes a file the user could
swap out between write and exec, and root never needs to read the
AppImage's FUSE mount (which it cannot do anyway).

It contains no secrets itself, so argv visibility is fine.

Protocol, newline-delimited JSON on stdin:
  {"cmd":"start","id":"j1","argv":["cryptsetup",...],"secrets":[n0,n1],
   "nice":true,"chown_after":"/abs/path","remove_after":"/mnt/.keelhaul-fill.x"}
  followed by n0+n1 raw bytes
  {"cmd":"signal","id":"j1","sig":"STOP"}
stdout carries JSON events: ready, started, out, exit, error, bye.

Placeholders in argv: @FD0@ becomes the fd number of secret 0, @FDPATH0@
becomes /dev/fd/N. Secrets go into kernel pipes, never argv or disk.
"""

import ctypes
import ctypes.util
import json
import os
import pwd
import re
import resource
import shutil
import signal
import subprocess
import sys
import threading

# Only these programs may run as root. Resolved with root's own PATH, so a
# user-controlled PATH cannot point them somewhere else. This limits which
# programs run, not what the authenticated user asks them to do.
ALLOW = {"cryptsetup", "nvme", "hdparm", "blkdiscard", "shred", "sfill", "dd", "sync"}
SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
SIGNALS = {"STOP": signal.SIGSTOP, "CONT": signal.SIGCONT,
           "TERM": signal.SIGTERM, "KILL": signal.SIGKILL}
MAX_SECRET = 65536
PLACEHOLDER = re.compile(r"@FD(PATH)?(\d+)@")

_out_lock = threading.Lock()


def emit(obj):
    with _out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def harden():
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
    libc.prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE 0
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    libc.mlockall(1 | 2)  # root has CAP_IPC_LOCK, so this succeeds


def requester():
    uid = os.environ.get("PKEXEC_UID") or os.environ.get("SUDO_UID")
    if uid is None:
        return None, None
    uid = int(uid)
    gid = os.environ.get("SUDO_GID")
    if gid is None:
        try:
            gid = pwd.getpwuid(uid).pw_gid
        except KeyError:
            gid = -1
    return uid, int(gid)


def zero(buf):
    ctypes.memset((ctypes.c_char * len(buf)).from_buffer(buf), 0, len(buf))


def read_exact(stream, n):
    buf = bytearray(n)
    view = memoryview(buf)
    pos = 0
    while pos < n:
        got = stream.readinto(view[pos:])
        if not got:
            raise EOFError("stdin closed while reading a secret")
        pos += got
    view.release()
    return buf


class Job:
    def __init__(self, jid, proc, chown_after, owner, remove_after=None):
        self.id = jid
        self.proc = proc
        self.chown_after = chown_after
        self.remove_after = remove_after
        self.owner = owner
        self.thread = threading.Thread(target=self.pump, daemon=True)
        self.thread.start()

    def pump(self):
        fd = self.proc.stdout.fileno()
        pending = b""
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            pending += chunk
            parts = re.split(rb"[\r\n]", pending)
            pending = parts.pop()
            for p in parts:
                if p:
                    emit({"type": "out", "id": self.id, "data": p.decode("utf-8", "replace")})
        if pending:
            emit({"type": "out", "id": self.id, "data": pending.decode("utf-8", "replace")})
        rc = self.proc.wait()
        # Hand the output file back to the user even on a non-zero exit:
        # dd filling free space exits 1 on ENOSPC by design.
        if self.chown_after and self.owner[0] is not None and os.path.isfile(self.chown_after):
            try:
                os.chown(self.chown_after, self.owner[0], self.owner[1])
                os.chmod(self.chown_after, 0o600)
            except OSError as exc:
                emit({"type": "error", "id": self.id, "message": "chown failed: " + str(exc)})
        if self.remove_after and os.path.isfile(self.remove_after):
            try:
                os.unlink(self.remove_after)
                os.sync()
            except OSError as exc:
                emit({"type": "error", "id": self.id, "message": "remove failed: " + str(exc)})
        emit({"type": "exit", "id": self.id, "code": rc})


def start(req, stream, jobs, owner):
    jid = str(req.get("id", ""))
    argv = [str(a) for a in req.get("argv", [])]
    lens = [int(n) for n in req.get("secrets", [])]
    secrets = []
    try:
        for n in lens:
            if n < 0 or n > MAX_SECRET:
                raise ValueError("secret length out of range")
            secrets.append(read_exact(stream, n))
        if not argv or argv[0] not in ALLOW:
            raise ValueError("program not allowed as root: " + (argv[0] if argv else "(none)"))
        exe = shutil.which(argv[0], path=SAFE_PATH)
        if not exe:
            raise ValueError(argv[0] + " not found on root's PATH")

        rfds = []
        for s in secrets:
            r, w = os.pipe()
            os.write(w, s)
            os.close(w)
            rfds.append(r)

        def sub(m):
            idx = int(m.group(2))
            if idx >= len(rfds):
                raise ValueError("placeholder refers to a missing secret")
            return ("/dev/fd/%d" if m.group(1) else "%d") % rfds[idx]

        final = [exe] + [PLACEHOLDER.sub(sub, a) for a in argv[1:]]
        if req.get("nice"):
            ionice = shutil.which("ionice", path=SAFE_PATH)
            nice = shutil.which("nice", path=SAFE_PATH)
            if ionice:
                final = [ionice, "-c", "2", "-n", "7"] + final
            if nice:
                final = [nice, "-n", "10"] + final
        chown_after = req.get("chown_after")
        if chown_after and not os.path.isabs(chown_after):
            raise ValueError("chown_after must be absolute")
        # Only Keelhaul's own free-space fill files may be removed as root.
        remove_after = req.get("remove_after")
        if remove_after and not (os.path.isabs(remove_after)
                                 and os.path.basename(remove_after).startswith(".keelhaul-fill.")):
            raise ValueError("remove_after must be an absolute .keelhaul-fill.* path")
        proc = subprocess.Popen(
            final, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, pass_fds=rfds, start_new_session=True,
            env={"PATH": SAFE_PATH, "LC_ALL": "C", "LANG": "C"},
        )
        for r in rfds:
            os.close(r)
        emit({"type": "started", "id": jid, "pid": proc.pid})
        jobs[jid] = Job(jid, proc, chown_after, owner, remove_after)
    except (ValueError, OSError, EOFError) as exc:
        emit({"type": "error", "id": jid, "message": str(exc)})
        emit({"type": "exit", "id": jid, "code": -1})
    finally:
        for s in secrets:
            zero(s)


def main():
    harden()
    owner = requester()
    jobs = {}
    stream = sys.stdin.buffer
    emit({"type": "ready", "uid": os.getuid()})
    while True:
        line = stream.readline(65536)
        if not line:
            break
        try:
            req = json.loads(line)
        except ValueError:
            emit({"type": "error", "id": "", "message": "bad request"})
            continue
        cmd = req.get("cmd")
        if cmd == "start":
            start(req, stream, jobs, owner)
        elif cmd == "signal":
            job = jobs.get(str(req.get("id")))
            sig = SIGNALS.get(req.get("sig"))
            if job and sig and job.proc.poll() is None:
                try:
                    os.killpg(job.proc.pid, sig)
                except ProcessLookupError:
                    pass
    for job in jobs.values():
        job.thread.join()
    emit({"type": "bye"})


main()
