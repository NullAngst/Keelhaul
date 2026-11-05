"""Disk Destroyer tab."""

from __future__ import annotations

import os
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QRadioButton,
    QSpinBox,
    QWidget,
)

from ..backends.wipe import WipeRunner
from ..core import devices
from .base import TabBase
from .confirm import confirm
from .widgets import LogView, button, note

KIND_TEXT = {
    "nvme": "NVMe SSD: nvme format (user data erase), then blkdiscard, then overwrite",
    "sata-ssd": "SATA SSD: ATA Secure Erase via hdparm, then blkdiscard, then overwrite",
    "hdd": "HDD: overwrite with random data",
    "flash": "USB/SD flash: overwrite with random data (no reliable hardware erase)",
}


class DriveRow(QWidget):
    def __init__(self, dev: devices.Device, runner: WipeRunner) -> None:
        super().__init__()
        self.runner = runner
        g = QGridLayout(self)
        g.setContentsMargins(0, 2, 0, 2)
        g.addWidget(QLabel(f"<b>{dev.path}</b>  {dev.model}  {devices.human_size(dev.size)}"), 0, 0, 1, 2)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setProperty("role", "danger")
        g.addWidget(self.bar, 1, 0)
        self.stop = button("Stop")
        self.stop.clicked.connect(runner.stop)
        g.addWidget(self.stop, 1, 1)
        self.status = QLabel("Waiting for authentication")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        g.addWidget(self.status, 2, 0, 1, 2)
        runner.progress.connect(self._progress)
        runner.status.connect(self.status.setText)
        runner.done.connect(self._done)

    def _progress(self, p: float) -> None:
        if p < 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(p * 1000))

    def _done(self, ok: bool) -> None:
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000 if ok else self.bar.value())
        self.stop.setEnabled(False)


class DestroyerTab(TabBase):
    key = "destroy"
    title = "Disk Destroyer"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._devs: list[devices.Device] = []
        self._runners: list[WipeRunner] = []
        self._session = None

        self.lay.addWidget(note(
            "EVERYTHING ON THE SELECTED DRIVES IS GONE FOR GOOD. THERE IS NO UNDO. Check the "
            "model and serial against the label on the drive before you arm it.", "Warning"))

        box = QGroupBox("Drives")
        bl = QFormLayout(box)
        self.list = QListWidget()
        self.list.setMinimumHeight(130)
        bl.addRow(self.list)
        rr = QHBoxLayout()
        refresh = button("Refresh")
        refresh.clicked.connect(self.refresh_devices)
        rr.addWidget(refresh)
        rr.addStretch(1)
        bl.addRow(rr)
        self.lay.addWidget(box)

        mbox = QGroupBox("Mode (pick one every run)")
        ml = QFormLayout(mbox)
        self.m_wipe = QRadioButton("Wipe: clear the drive and keep it usable")
        self.m_destroy = QRadioButton("Destroy: write random data until the drive dies")
        grp = QButtonGroup(self)
        grp.addButton(self.m_wipe)
        grp.addButton(self.m_destroy)
        ml.addRow(self.m_wipe)
        ml.addRow(note("Uses the drive's own erase where it has one, so the data is "
                       "unrecoverable and the drive still mounts and formats afterward.", "Muted"))
        ml.addRow(self.m_destroy)
        ml.addRow(note("Fills the whole device with random data, over and over, until it stops "
                       "accepting writes, disappears, or you press Stop. On an SSD this burns "
                       "through the flash on purpose. This ruins the drive.", "Muted"))
        self.passes = QSpinBox()
        self.passes.setRange(1, 7)
        self.passes.setValue(1)
        self.zero_last = QCheckBox("Finish with a pass of zeros")
        self.full_speed = QCheckBox("Full speed (no nice, no ionice, no watchdog pausing)")
        ml.addRow("Overwrite passes", self.passes)
        ml.addRow(self.zero_last)
        ml.addRow(self.full_speed)
        ml.addRow(note("One random pass is enough on any drive made this century. More passes "
                       "only cost time. They apply to overwrite steps in Wipe mode.", "Muted"))
        self.lay.addWidget(mbox)

        self.plan = note("", "Note")
        self.lay.addWidget(self.plan)

        row = QHBoxLayout()
        row.addStretch(1)
        self.report_btn = button("Save wipe report")
        self.report_btn.setEnabled(False)
        self.report_btn.clicked.connect(self.save_report)
        row.addWidget(self.report_btn)
        self.run_btn = button("Arm and run", "danger")
        self.run_btn.clicked.connect(self.run)
        row.addWidget(self.run_btn)
        self.lay.addLayout(row)

        self.rows_box = QGroupBox("Progress")
        self.rows = QFormLayout(self.rows_box)
        self.rows_box.hide()
        self.lay.addWidget(self.rows_box)
        self.log = LogView()
        self.lay.addWidget(self.log, 1)

        self.list.itemChanged.connect(self._update_plan)
        self.m_wipe.toggled.connect(self._update_plan)
        self.m_destroy.toggled.connect(self._update_plan)
        self.refresh_devices()

    @property
    def busy(self) -> bool:
        return any(r.job is not None and r.job.running for r in self._runners)

    def refresh_devices(self) -> None:
        if self.busy:
            return
        self.list.clear()
        try:
            self._devs = devices.list_disks()
        except Exception as exc:
            self._devs = []
            self.log.add(f"Could not list drives: {exc}")
        for d in self._devs:
            tag = " [SYSTEM DRIVE]" if d.protected else (" [MOUNTED]" if d.mounted else "")
            it = QListWidgetItem(f"{d.describe()}  {d.kind}{tag}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Unchecked)
            self.list.addItem(it)
        self._update_plan()

    def _selected(self) -> list[devices.Device]:
        return [self._devs[i] for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _update_plan(self) -> None:
        sel = self._selected()
        mode_set = self.m_wipe.isChecked() or self.m_destroy.isChecked()
        self.run_btn.setEnabled(bool(sel) and mode_set and not self.busy)
        wipe = self.m_wipe.isChecked()
        self.passes.setEnabled(wipe)
        self.zero_last.setEnabled(wipe)
        if not sel:
            self.plan.setText("Tick one or more drives.")
            return
        if not mode_set:
            self.plan.setText("Pick Wipe or Destroy. There is no default.")
            return
        lines = []
        for d in sel:
            how = KIND_TEXT[d.kind] if wipe else "shred random pass, repeated until the device fails"
            lines.append(f"{d.path}: {how}")
        self.plan.setText("\n".join(lines))

    def run(self) -> None:
        sel = self._selected()
        if not sel or self.busy:
            return
        mode = "wipe" if self.m_wipe.isChecked() else "destroy"
        verb = "wipe" if mode == "wipe" else "destroy"
        if not confirm(self, "Arm Disk Destroyer", verb, sel,
                       ("Wipe erases every byte on these drives. The data cannot be recovered."
                        if mode == "wipe" else
                        "Destroy writes to these drives until they die. The data and the "
                        "drives are both gone."), "Wipe" if mode == "wipe" else "Destroy"):
            return
        for d in sel:
            err = devices.reverify(d)
            if err:
                self.warn(err)
                return
        # Reset the progress area.
        while self.rows.rowCount():
            self.rows.removeRow(0)
        self._runners = []
        self._session = self.ctx.session()
        self._session.failed.connect(lambda m: self.log.add("Escalation failed: " + m))
        throttle = not self.full_speed.isChecked()
        for d in sel:
            target = devices.by_id(d.path)
            r = WipeRunner(self._session, d, target, mode, self.ctx.caps,
                           passes=self.passes.value(), zero_last=self.zero_last.isChecked(),
                           throttle=throttle)
            r.log.connect(lambda s, n=d.name: self.log.add(f"[{n}] {s}"))
            r.job_started.connect(self.ctx.track)
            r.done.connect(self._one_done)
            self.rows.addRow(DriveRow(d, r))
            self._runners.append(r)
        self.rows_box.show()
        self.run_btn.setEnabled(False)
        self.report_btn.setEnabled(False)
        self.log.add(f"--- {mode} on {', '.join(d.path for d in sel)} ---")
        for r in self._runners:
            r.start()

    def _one_done(self, _ok: bool) -> None:
        if any(r.record.finished == "" for r in self._runners):
            return
        if self._session:
            self._session.close()
        self.report_btn.setEnabled(True)
        self._update_plan()
        self.refresh_devices()

    def save_report(self) -> None:
        if not self._runners:
            return
        default = os.path.expanduser(f"~/keelhaul-wipe-{time.strftime('%Y%m%d-%H%M%S')}.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Save wipe report", default, "Text (*.txt)")
        if not path:
            return
        text = "\n\n".join(r.record.text() for r in self._runners)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.log.add(f"Report saved to {path}")

