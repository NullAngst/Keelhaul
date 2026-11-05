"""Resource watchdog.

Samples CPU time and RSS for every process in each running job's process
groups. If Keelhaul's own children stay over a ceiling for the sustain
window, the heaviest throttled job gets SIGSTOP, the system gets a few
seconds to recover, then SIGCONT. It paces work. It never changes what a
crypto or erase command does.

Root children are readable here too: /proc/<pid>/stat is world-readable.
Pausing them goes through the root helper.
"""

from __future__ import annotations

import os
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

CLK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")
NCPU = os.cpu_count() or 1


def _stat(pid: str) -> tuple[int, int, int] | None:
    """(pgrp, cpu ticks, rss bytes) for one pid."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            raw = fh.read().decode("ascii", "replace")
    except OSError:
        return None
    fields = raw[raw.rfind(")") + 2:].split()
    try:
        return int(fields[2]), int(fields[11]) + int(fields[12]), int(fields[21]) * PAGE
    except (IndexError, ValueError):
        return None


def sample_groups(pgids: set[int]) -> dict[int, tuple[int, int]]:
    """pgid -> (summed ticks, summed rss) across all live processes."""
    out: dict[int, list[int]] = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        st = _stat(pid)
        if st and st[0] in pgids:
            acc = out.setdefault(st[0], [0, 0])
            acc[0] += st[1]
            acc[1] += st[2]
    return {k: (v[0], v[1]) for k, v in out.items()}


class Watchdog(QObject):
    throttled = pyqtSignal(object, bool)     # job, paused
    usage = pyqtSignal(float, int)           # cpu percent of machine, rss bytes

    def __init__(self, conf: dict, interval: float = 1.0) -> None:
        """conf is a plain dict the GUI replaces whole, never a QSettings shared across threads."""
        super().__init__()
        self.conf = conf
        self.interval = interval
        self._jobs: list = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._prev: dict[int, int] = {}
        self._over_since: float | None = None
        self._paused_by_us: dict[object, float] = {}
        self._thread = threading.Thread(target=self._loop, daemon=True, name="keelhaul-watchdog")

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()

    def register(self, job) -> None:
        with self._lock:
            if job not in self._jobs:
                self._jobs.append(job)
        job.finished.connect(lambda _c, j=job: self.unregister(j))

    def unregister(self, job) -> None:
        with self._lock:
            if job in self._jobs:
                self._jobs.remove(job)
        self._paused_by_us.pop(job, None)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._tick()
            except Exception:
                pass  # a vanished pid mid-scan must never kill the watchdog

    def _tick(self) -> None:
        with self._lock:
            jobs = [j for j in self._jobs if j.running]
        if not jobs:
            self._over_since = None
            return
        pgids = {pg for j in jobs for pg in j.pgids}
        groups = sample_groups(pgids)
        per_job = {}
        total_cpu = 0.0
        total_rss = 0
        for j in jobs:
            ticks = sum(groups.get(pg, (0, 0))[0] for pg in j.pgids)
            rss = sum(groups.get(pg, (0, 0))[1] for pg in j.pgids)
            prev = self._prev.get(id(j), ticks)
            self._prev[id(j)] = ticks
            cpu = 100.0 * (ticks - prev) / (CLK * self.interval * NCPU)
            per_job[j] = (cpu, rss)
            total_cpu += cpu
            total_rss += rss
        self.usage.emit(total_cpu, total_rss)

        now = time.monotonic()
        # Resume jobs we paused once their recovery window is over.
        recover = self.conf["recover_seconds"]
        for j, since in list(self._paused_by_us.items()):
            if now - since >= recover:
                j.resume()
                self._paused_by_us.pop(j, None)
                self.throttled.emit(j, False)

        if not self.conf["throttle"]:
            self._over_since = None
            return
        cpu_ceiling = self.conf["cpu_ceiling"]
        rss_ceiling = self.conf["rss_ceiling_mib"] * 1024 * 1024
        load_ceiling = self.conf["load_ceiling"] or NCPU * 1.5
        load = os.getloadavg()[0]
        over = (total_cpu > cpu_ceiling or total_rss > rss_ceiling
                or (load > load_ceiling and total_cpu > 10.0))
        if not over:
            self._over_since = None
            return
        if self._over_since is None:
            self._over_since = now
            return
        if now - self._over_since < self.conf["sustain_seconds"]:
            return
        candidates = [j for j in jobs if j.throttle and not j.paused]
        if not candidates:
            return
        heaviest = max(candidates, key=lambda j: per_job[j])
        heaviest.pause()
        self._paused_by_us[heaviest] = now
        self._over_since = None
        self.throttled.emit(heaviest, True)
