"""Hash Check tab: coreutils sha256sum, sha512sum, b2sum."""

from __future__ import annotations

import os

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
)

from ..backends import hashes
from ..core.runner import UserJob
from .base import TabBase
from .widgets import LogView, PathPicker, button, note, repolish


class HashTab(TabBase):
    key = "hash"
    title = "Hash Check"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._job = None
        self.setAcceptDrops(True)

        box = QGroupBox("File")
        form = QFormLayout(box)
        self.file = PathPicker("open", "Drop a file here (or a SHA256SUMS file), or browse")
        form.addRow("File", self.file)
        self.algo = QComboBox()
        form.addRow("Algorithm", self.algo)
        self.expected = QLineEdit()
        self.expected.setPlaceholderText("Paste the expected hash (optional)")
        form.addRow("Expected", self.expected)
        self.sums = PathPicker("open", "Or verify against a SHA256SUMS / .sha256 file", "Choose checksum file")
        form.addRow("Checksum file", self.sums)
        self.lay.addWidget(box)

        res = QGroupBox("Result")
        rl = QFormLayout(res)
        dr = QHBoxLayout()
        self.digest = QLineEdit()
        self.digest.setReadOnly(True)
        self.digest.setPlaceholderText("Digest appears here")
        copy = button("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.digest.text()))
        dr.addWidget(self.digest, 1)
        dr.addWidget(copy)
        rl.addRow("Digest", dr)
        self.verdict = QLabel("No result yet")
        self.verdict.setProperty("verdict", "none")
        rl.addRow(self.verdict)
        self.lay.addWidget(res)

        row = QHBoxLayout()
        row.addStretch(1)
        self.run_btn = button("Hash", "go")
        self.run_btn.clicked.connect(self.run)
        row.addWidget(self.run_btn)
        self.lay.addLayout(row)
        self.lay.addWidget(note("Drop a file anywhere on this tab. Dropping a checksum file "
                                "verifies every entry in it against the files next to it.",
                                "Muted"))
        self.log = LogView(100)
        self.lay.addWidget(self.log, 1)

        self.file.changed.connect(self._file_changed)
        self.expected.textChanged.connect(self._compare)

    def refresh_caps(self) -> None:
        super().refresh_caps()
        self.algo.clear()
        for label, tool in hashes.ALGORITHMS.items():
            if self.ctx.caps.has(tool):
                self.algo.addItem(label, tool)

    @property
    def busy(self) -> bool:
        return self._job is not None and self._job.running

    def dragEnterEvent(self, ev) -> None:  # noqa: N802
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:  # noqa: N802
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if not paths:
            return
        sums = [p for p in paths if self._is_sums(p)]
        others = [p for p in paths if p not in sums]
        if sums:
            self.sums.set_path(sums[0])
        if others:
            self.file.set_path(others[0])
        self.run()

    @staticmethod
    def _is_sums(p: str) -> bool:
        n = os.path.basename(p).upper()
        return "SUMS" in n or n.endswith((".SHA256", ".SHA512", ".B2", ".SHA256SUM", ".SHA512SUM"))

    def _file_changed(self) -> None:
        self.digest.clear()
        self._set_verdict("none", "No result yet")
        p = self.file.path()
        if p and self._is_sums(p):
            self.sums.set_path(p)
            self.file.set_path("")

    def _set_verdict(self, kind: str, text: str) -> None:
        self.verdict.setProperty("verdict", kind)
        self.verdict.setText(text)
        repolish(self.verdict)

    def _compare(self) -> None:
        exp = hashes.normalize_expected(self.expected.text())
        got = self.digest.text()
        if not got:
            return
        if not exp:
            self._set_verdict("none", "Hashed. Paste an expected hash to compare.")
        elif hashes.matches(got, exp):
            self._set_verdict("match", "MATCH")
        else:
            self._set_verdict("nomatch", "NO MATCH")

    def run(self) -> None:
        if self.busy:
            return
        sums = self.sums.path()
        f = self.file.path()
        if sums and os.path.isfile(sums) and not f:
            self._verify_sums(sums)
            return
        if not os.path.isfile(f):
            self.warn("Pick a file to hash.")
            return
        tool = self.algo.currentData()
        self.digest.clear()
        self._set_verdict("none", "Hashing...")
        job = UserJob("hash", hashes.hash_argv(tool, f), throttle=True)
        out: list[str] = []
        job.line.connect(out.append)
        self._job = job
        self.run_btn.setEnabled(False)

        def done(code: int) -> None:
            self.run_btn.setEnabled(True)
            digest = hashes.parse_digest(out[0]) if code == 0 and out else None
            if not digest:
                self._set_verdict("nomatch", "Hashing failed")
                self.log.add("\n".join(out))
                return
            self.digest.setText(digest)
            self.log.add(f"{self.algo.currentText()}  {digest}  {f}")
            self._compare()

        self.start_job(job, None, done)

    def _verify_sums(self, sums: str) -> None:
        tool = hashes.guess_tool_for_sums(sums)
        if not self.ctx.caps.has(tool):
            self.warn(f"{tool} is needed for this checksum file and is not installed.")
            return
        self._set_verdict("none", "Verifying...")
        job = UserJob("verify sums", hashes.check_argv(tool, sums), cwd=os.path.dirname(sums) or ".")
        out: list[str] = []
        job.line.connect(out.append)
        job.line.connect(self.log.add)
        self._job = job
        self.run_btn.setEnabled(False)

        def done(code: int) -> None:
            self.run_btn.setEnabled(True)
            ok = sum(1 for ln in out if ln.endswith(": OK"))
            bad = sum(1 for ln in out if ln.endswith(": FAILED") or "FAILED open" in ln)
            if code == 0 and ok:
                self._set_verdict("match", f"ALL {ok} MATCH")
            else:
                self._set_verdict("nomatch", f"{bad} FAILED, {ok} OK")

        self.start_job(job, None, done)
