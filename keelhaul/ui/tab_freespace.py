"""Free-Space Wipe tab: sfill, or a dd random-fill file as fallback."""

from __future__ import annotations

import os

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
)

from ..backends import freespace as fs
from ..core import devices
from ..core.runner import RootJob, UserJob
from .base import TabBase
from .widgets import LogView, button, note


class FreeSpaceTab(TabBase):
    key = "freespace"
    title = "Free-Space Wipe"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._mounts: list[devices.Mount] = []
        self._job = None
        self._session = None
        self._fill = None
        self._start_free = 0

        box = QGroupBox("Filesystem")
        form = QFormLayout(box)
        mr = QHBoxLayout()
        self.mount = QComboBox()
        self.mount.setMinimumWidth(360)
        mr.addWidget(self.mount, 1)
        refresh = button("Refresh")
        refresh.clicked.connect(self.refresh_mounts)
        mr.addWidget(refresh)
        form.addRow("Mountpoint", mr)
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        form.addRow(self.info)
        self.warning = note("", "Warning")
        self.warning.hide()
        form.addRow(self.warning)
        self.as_root = QCheckBox("Run as root (covers root-reserved blocks on ext filesystems)")
        form.addRow(self.as_root)
        self.backend_lab = QLabel("")
        self.backend_lab.setObjectName("Muted")
        form.addRow("Backend", self.backend_lab)
        self.lay.addWidget(box)

        self.lay.addWidget(note("Overwrites only the unallocated space, so traces of files you "
                                "already deleted the normal way get covered. Existing files are "
                                "left alone. The filesystem fills to 100% for a while, so close "
                                "anything that writes heavily to it first.", "Note"))

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.lay.addWidget(self.bar)
        row = QHBoxLayout()
        row.addStretch(1)
        self.stop_btn = button("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        self.run_btn = button("Wipe free space", "danger")
        self.run_btn.clicked.connect(self.run)
        row.addWidget(self.stop_btn)
        row.addWidget(self.run_btn)
        self.lay.addLayout(row)
        self.log = LogView()
        self.lay.addWidget(self.log, 1)

        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._update_progress)
        self.mount.currentIndexChanged.connect(self._mount_changed)
        self.refresh_mounts()

    def refresh_caps(self) -> None:
        super().refresh_caps()
        self.backend_lab.setText("sfill (secure-delete), one random pass" if self.ctx.caps.has("sfill")
                                 else "dd random fill file (sfill not installed)")

    @property
    def busy(self) -> bool:
        return self._job is not None and self._job.running

    def refresh_mounts(self) -> None:
        if self.busy:
            return
        self.mount.clear()
        self._mounts = devices.list_mounts()
        for m in self._mounts:
            self.mount.addItem(f"{m.target}   ({m.fstype} on {m.source})")
        self._mount_changed()

    def _current(self) -> devices.Mount | None:
        i = self.mount.currentIndex()
        return self._mounts[i] if 0 <= i < len(self._mounts) else None

    def _mount_changed(self) -> None:
        m = self._current()
        if not m:
            self.info.setText("No real filesystems found.")
            self.warning.hide()
            return
        try:
            avail = fs.avail_bytes(m.target)
            free = fs.free_bytes_root(m.target)
        except OSError:
            avail = free = 0
        self.info.setText(f"{devices.human_size(avail)} free for users, "
                          f"{devices.human_size(free)} free including reserved blocks")
        warns = []
        if m.cow:
            warns.append(f"{m.fstype} is copy-on-write. Snapshots and COW keep old blocks that "
                         "a free-space fill never touches.")
        if m.source.startswith("/dev/") and devices.is_solid_state(m.source):
            warns.append("This is on a solid-state drive. Wear leveling means overwriting free "
                         "space does not reach the old cells.")
        if m.fstype in ("ext3", "ext4", "xfs"):
            warns.append("Journaled metadata (file names, sizes) may still hold traces.")
        if warns:
            warns.append("No guarantee the old data is gone here. Full-disk encryption from the "
                         "start, or a whole-device wipe, is the real answer.")
        self.warning.setText("\n".join(warns))
        self.warning.setVisible(bool(warns))
        need = fs.needs_root(m.target)
        self.as_root.setChecked(need or free > avail)
        self.as_root.setEnabled(not need)

    def run(self) -> None:
        if self.busy:
            return
        m = self._current()
        if not m:
            return
        if self.warning.isVisible() and not self.ask(self.warning.text() + "\n\nRun it anyway?",
                                                     yes="Run anyway"):
            return
        root = self.as_root.isChecked()
        use_sfill = self.ctx.caps.has("sfill")
        try:
            self._start_free = fs.free_bytes_root(m.target) if root else fs.avail_bytes(m.target)
        except OSError as exc:
            self.warn(f"Cannot read {m.target}: {exc}")
            return
        self._mount = m
        self._fill = None
        if use_sfill:
            argv = fs.sfill_argv(m.target)
        else:
            self._fill = fs.fill_path(m.target)
            argv = fs.dd_argv(self._fill)
        if root:
            self._session = self.ctx.session()
            self._session.failed.connect(lambda msg: self.log.add("Escalation failed: " + msg))
            job = RootJob(self._session, "free-space wipe", argv, remove_after=self._fill)
        else:
            job = UserJob("free-space wipe", argv)
        self._job = job
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.bar.setValue(0)
        self.log.add(f"--- free-space wipe on {m.target} ---")
        self._poll.start()
        self.start_job(job, self.log, self._finished)

    def _update_progress(self) -> None:
        if not self._start_free:
            return
        try:
            now = fs.free_bytes_root(self._mount.target) if self.as_root.isChecked() else fs.avail_bytes(self._mount.target)
        except OSError:
            return
        self.bar.setValue(int(1000 * max(0.0, 1 - now / self._start_free)))

    def _stop(self) -> None:
        if self._job:
            self._job.stop()

    def _finished(self, code: int) -> None:
        self._poll.stop()
        self.stop_btn.setEnabled(False)
        self.run_btn.setEnabled(True)
        if self._session:
            self._session.close()
        if self._fill:
            # dd stops on ENOSPC with exit 1; that is the expected end.
            try:
                os.remove(self._fill)
                self.log.add("Removed the fill file.")
            except FileNotFoundError:
                pass
            except OSError as exc:
                self.log.add(f"COULD NOT REMOVE {self._fill}: {exc}. Delete it or the "
                             "filesystem stays full.")
            os.sync()
            filled = code in (0, 1)
        else:
            filled = code == 0
        self.bar.setValue(1000 if filled and not (self._job and self._job._stopping) else self.bar.value())
        self.log.add("Done." if filled else f"Stopped or failed (exit {code}).")
        self._mount_changed()
