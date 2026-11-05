"""Metadata Scrub tab: mat2 (primary), exiftool (alternate)."""

from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QRadioButton,
)

from ..backends import mat2_exif, remove
from ..core.runner import UserJob
from .base import TabBase
from .widgets import LogView, PathPicker, button, note


class ScrubTab(TabBase):
    key = "scrub"
    title = "Metadata Scrub"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._job = None
        self._files: list[str] = []
        self._todo: list[str] = []
        self._done = 0
        self._failed = 0
        self._stopping = False

        box = QGroupBox("Target")
        form = QFormLayout(box)
        self.target = PathPicker("any", "Drop a file or folder here, or browse")
        form.addRow("Path", self.target)
        sr = QHBoxLayout()
        self.s_file = QRadioButton("Single file")
        self.s_folder = QRadioButton("Folder (direct files)")
        self.s_rec = QRadioButton("Folder and all subfolders")
        self.s_file.setChecked(True)
        grp = QButtonGroup(self)
        for r in (self.s_file, self.s_folder, self.s_rec):
            grp.addButton(r)
            sr.addWidget(r)
        sr.addStretch(1)
        form.addRow("Scope", sr)
        self.backend = QComboBox()
        form.addRow("Backend", self.backend)
        self.remove_orig = QCheckBox("Replace originals with the cleaned copies")
        self.remove_orig.setObjectName("DangerCheck")
        form.addRow(self.remove_orig)
        self.remove_note = note("", "Warning")
        self.remove_note.hide()
        form.addRow(self.remove_note)
        form.addRow(note("Off by default: cleaned copies are written next to the originals as "
                         "name.cleaned.ext. When on, an original is removed only after its "
                         "cleaned copy is confirmed written, and the copy takes its name.",
                         "Muted"))
        self.lay.addWidget(box)

        pv = QGroupBox("Files that will be touched")
        pl = QFormLayout(pv)
        self.preview = QListWidget()
        self.preview.setMinimumHeight(120)
        self.count = QLabel("Press Preview to list them.")
        self.count.setObjectName("Muted")
        pl.addRow(self.preview)
        pl.addRow(self.count)
        self.lay.addWidget(pv)

        self.bar = QProgressBar()
        self.bar.setValue(0)
        self.lay.addWidget(self.bar)
        row = QHBoxLayout()
        row.addStretch(1)
        self.preview_btn = button("Preview")
        self.preview_btn.clicked.connect(self.do_preview)
        self.stop_btn = button("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        self.run_btn = button("Scrub", "go")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self.run)
        row.addWidget(self.preview_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.run_btn)
        self.lay.addLayout(row)
        self.log = LogView()
        self.lay.addWidget(self.log, 1)

        self.target.changed.connect(self._invalidate)
        for r in (self.s_file, self.s_folder, self.s_rec):
            r.toggled.connect(self._invalidate)
        self.remove_orig.toggled.connect(self._assess)

    def refresh_caps(self) -> None:
        super().refresh_caps()
        self.backend.clear()
        if self.ctx.caps.has("mat2"):
            self.backend.addItem("mat2", "mat2")
        if self.ctx.caps.has("exiftool"):
            self.backend.addItem("exiftool", "exiftool")

    @property
    def busy(self) -> bool:
        return self._job is not None and self._job.running

    def _scope(self) -> str:
        return "file" if self.s_file.isChecked() else ("folder" if self.s_folder.isChecked() else "recursive")

    def _invalidate(self) -> None:
        p = self.target.path()
        if os.path.isdir(p) and self.s_file.isChecked():
            self.s_folder.setChecked(True)
        elif os.path.isfile(p) and not self.s_file.isChecked():
            self.s_file.setChecked(True)
        self._files = []
        self.preview.clear()
        self.count.setText("Press Preview to list them.")
        self.run_btn.setEnabled(False)
        self._assess()

    def _assess(self) -> None:
        p = self.target.path()
        if not self.remove_orig.isChecked() or not os.path.exists(p):
            self.remove_note.hide()
            return
        _how, why = remove.assess(p)
        self.remove_note.setText(why)
        self.remove_note.show()

    def do_preview(self) -> None:
        p = self.target.path()
        if not os.path.exists(p):
            self.warn("Pick a file or folder.")
            return
        try:
            self._files = mat2_exif.collect(p, self._scope())
        except OSError as exc:
            self.warn(f"Could not read {p}: {exc}")
            return
        self.preview.clear()
        self.preview.addItems(self._files[:5000])
        extra = len(self._files) - 5000
        self.count.setText(f"{len(self._files)} file(s)." + (" Showing the first 5000." if extra > 0 else ""))
        self.run_btn.setEnabled(bool(self._files))

    def run(self) -> None:
        if self.busy or not self._files:
            return
        self._remove = self.remove_orig.isChecked()
        self._how = "delete"
        if self._remove:
            how, why = remove.assess(self._files[0])
            self._how = how
            if how == "delete" and not self.ask(why + "\n\nReplace the originals with a plain "
                                                "delete anyway?", yes="Plain delete"):
                return
        self._todo = list(self._files)
        self._done = self._failed = 0
        self._stopping = False
        self.bar.setRange(0, len(self._todo))
        self.bar.setValue(0)
        self.run_btn.setEnabled(False)
        self.preview_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.log.add(f"--- scrubbing {len(self._todo)} file(s) with {self.backend.currentData()} ---")
        self._next()

    def _stop(self) -> None:
        self._stopping = True
        if self._job:
            self._job.stop()

    def _next(self) -> None:
        if self._stopping or not self._todo:
            self.log.add(f"Finished: {self._done} cleaned, {self._failed} failed"
                         + (", stopped early." if self._stopping else "."))
            self.run_btn.setEnabled(True)
            self.preview_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            return
        f = self._todo.pop(0)
        argv = mat2_exif.mat2_argv(f) if self.backend.currentData() == "mat2" else mat2_exif.exiftool_argv(f)
        cleaned = mat2_exif.cleaned_name(f)
        if os.path.exists(cleaned):
            self.log.add(f"Skipped {f}: {cleaned} already exists.")
            self._failed += 1
            self._advance()
            return
        job = UserJob("scrub", argv)
        self._job = job
        self.start_job(job, self.log, lambda code: self._after(code, f, cleaned))

    def _advance(self) -> None:
        self.bar.setValue(self._done + self._failed)
        self._next()

    def _after(self, code: int, f: str, cleaned: str) -> None:
        if code != 0 or not os.path.isfile(cleaned) or os.path.getsize(cleaned) == 0:
            self._failed += 1
            self.log.add(f"Failed: {f} (exit {code})")
            self._advance()
            return
        if not self._remove:
            self._done += 1
            self._advance()
            return
        if self._how == "shred":
            job = UserJob("shred original", remove.shred_argv(f))
            self._job = job
            self.start_job(job, self.log, lambda c: self._replace(c == 0, f, cleaned))
            return
        try:
            os.remove(f)
            ok = True
        except OSError as exc:
            self.log.add(f"Could not remove {f}: {exc}")
            ok = False
        self._replace(ok, f, cleaned)

    def _replace(self, removed: bool, f: str, cleaned: str) -> None:
        if removed:
            try:
                os.rename(cleaned, f)
                self._done += 1
            except OSError as exc:
                self.log.add(f"Cleaned copy kept at {cleaned}; rename failed: {exc}")
                self._failed += 1
        else:
            self.log.add(f"Original kept: {f}. Cleaned copy at {cleaned}.")
            self._failed += 1
        self._advance()
