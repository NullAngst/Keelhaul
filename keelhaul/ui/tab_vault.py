"""File Vault tab: age (primary), gpg --symmetric (fallback)."""

from __future__ import annotations

import os
import shutil

from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QRadioButton,
    QVBoxLayout,
)

from ..backends import age_gpg, remove
from ..core.runner import UserJob
from .base import TabBase
from .widgets import LogView, PassphraseInput, PathPicker, button, note


class VaultTab(TabBase):
    key = "vault"
    title = "File Vault"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._job = None
        self._queue: list = []

        modebox = QGroupBox("Mode")
        ml = QHBoxLayout(modebox)
        self.enc = QRadioButton("Encrypt")
        self.dec = QRadioButton("Decrypt")
        self.enc.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.enc)
        grp.addButton(self.dec)
        ml.addWidget(self.enc)
        ml.addWidget(self.dec)
        ml.addStretch(1)
        self.lay.addWidget(modebox)

        io = QGroupBox("Files")
        form = QFormLayout(io)
        self.src = PathPicker("any", "Drop a file or folder here, or browse")
        self.out = PathPicker("save", "Output path (filled in for you)", "Save as")
        form.addRow("Input", self.src)
        form.addRow("Output", self.out)
        self.folder_note = note("Folders are packed with tar and streamed straight into the "
                                "encryptor, so no plaintext tar touches the disk. Decrypting "
                                "gives you the .tar back.", "Muted")
        self.folder_note.hide()
        form.addRow(self.folder_note)
        self.lay.addWidget(io)

        opts = QGroupBox("Options")
        ol = QFormLayout(opts)
        self.backend = QComboBox()
        self.armor = QCheckBox("ASCII armored output (gpg only)")
        self.delete_orig = QCheckBox("Remove the original after it encrypts")
        self.delete_orig.setObjectName("DangerCheck")
        self.delete_note = note("", "Warning")
        self.delete_note.hide()
        ol.addRow("Backend", self.backend)
        ol.addRow(self.armor)
        ol.addRow(self.delete_orig)
        ol.addRow(self.delete_note)
        self.lay.addWidget(opts)

        self.pass_box = QGroupBox("Passphrase")
        self.pass_lay = QVBoxLayout(self.pass_box)
        self.pass_enc = PassphraseInput(confirm=True)
        self.pass_dec = PassphraseInput(confirm=False, meter=False)
        self.pass_lay.addWidget(self.pass_enc)
        self.pass_lay.addWidget(self.pass_dec)
        self.lay.addWidget(self.pass_box)
        self.lay.addWidget(note("age uses scrypt in passphrase mode. gpg uses AES-256 with "
                                "SHA-512 S2K at the maximum iteration count and never caches "
                                "the passphrase in gpg-agent.", "Muted"))

        row = QHBoxLayout()
        row.addStretch(1)
        self.stop_btn = button("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        row.addWidget(self.stop_btn)
        self.run_btn = button("Encrypt", "go")
        self.run_btn.clicked.connect(self.run)
        row.addWidget(self.run_btn)
        self.lay.addLayout(row)
        self.log = LogView()
        self.lay.addWidget(self.log, 1)

        self.enc.toggled.connect(self._mode_changed)
        self.src.changed.connect(self._src_changed)
        self.backend.currentIndexChanged.connect(self._src_changed)
        self.armor.toggled.connect(self._src_changed)
        self.delete_orig.toggled.connect(self._assess_delete)

    def refresh_caps(self) -> None:
        super().refresh_caps()
        self.backend.clear()
        if self.ctx.caps.has("age"):
            self.backend.addItem("age", "age")
        if self.ctx.caps.has("gpg"):
            self.backend.addItem("gpg (AES-256)", "gpg")
        self._mode_changed()

    @property
    def busy(self) -> bool:
        return self._job is not None and self._job.running

    def _mode_changed(self) -> None:
        enc = self.enc.isChecked()
        self.pass_enc.setVisible(enc)
        self.pass_dec.setVisible(not enc)
        self.backend.setEnabled(enc)
        self.armor.setEnabled(enc)
        self.delete_orig.setEnabled(enc)
        self.run_btn.setText("Encrypt" if enc else "Decrypt")
        self._src_changed()

    def _src_changed(self) -> None:
        src = self.src.path()
        is_dir = os.path.isdir(src)
        self.folder_note.setVisible(is_dir)
        self.armor.setEnabled(self.enc.isChecked() and self.backend.currentData() == "gpg")
        if not src:
            return
        if self.enc.isChecked():
            self.out.set_path(age_gpg.encrypted_name(src, self.backend.currentData() or "age",
                                                     self.armor.isChecked(), is_dir))
        elif os.path.isfile(src):
            self.out.set_path(age_gpg.decrypted_name(src))
        self._assess_delete()

    def _assess_delete(self) -> None:
        src = self.src.path()
        if not self.delete_orig.isChecked() or not os.path.exists(src):
            self.delete_note.hide()
            return
        how, why = remove.assess(src)
        self.delete_note.setText(why)
        self.delete_note.show()

    # ---- run ----------------------------------------------------------
    def run(self) -> None:
        if self.busy:
            return
        src = self.src.path()
        out = self.out.path()
        if not src or not os.path.exists(src):
            self.warn("Pick an input file or folder.")
            return
        if not out:
            self.warn("Pick an output path.")
            return
        if os.path.exists(out) and not self.ask(f"{out} exists. Overwrite it?", yes="Overwrite"):
            return
        if os.path.realpath(out) == os.path.realpath(src):
            self.warn("Output and input are the same file.")
            return
        enc = self.enc.isChecked()
        pi = self.pass_enc if enc else self.pass_dec
        ok, why = pi.valid()
        if not ok:
            self.warn(why)
            return
        if enc and pi.meter and 0 <= pi.meter.score < 3:
            if not self.ask("That passphrase is rated weak. Anyone who gets the encrypted file "
                            "can try passphrases offline forever. Use it anyway?", yes="Use it"):
                return
        is_dir = os.path.isdir(src)
        if not enc and is_dir:
            self.warn("Decrypt takes a single encrypted file.")
            return
        self._delete_after = enc and self.delete_orig.isChecked()
        if self._delete_after:
            how, why = remove.assess(src)
            if how == "delete" and not self.ask(why + "\n\nRemove the original with a plain "
                                                "delete after it encrypts?", yes="Plain delete"):
                return
            self._delete_how = how
        secret = pi.take()
        if enc:
            backend = self.backend.currentData()
            feeder = age_gpg.tar_feeder(src) if is_dir else None
            in_path = None if is_dir else src
            if backend == "age":
                job = UserJob("age encrypt", age_gpg.age_encrypt(out, in_path), [secret],
                              feeder=feeder, pty_answers=2)
            else:
                job = UserJob("gpg encrypt", age_gpg.gpg_encrypt(out, in_path, self.armor.isChecked()),
                              [secret], feeder=feeder)
        else:
            backend = age_gpg.detect(src)
            if backend == "age":
                if not self.ctx.caps.has("age"):
                    secret.destroy()
                    self.warn("This is an age file and age is not installed.")
                    return
                job = UserJob("age decrypt", age_gpg.age_decrypt(out, src), [secret], pty_answers=1)
            else:
                if not self.ctx.caps.has("gpg"):
                    secret.destroy()
                    self.warn("This does not look like an age file, and gpg is not installed.")
                    return
                job = UserJob("gpg decrypt", age_gpg.gpg_decrypt(out, src), [secret])
        secret.destroy()
        pi.clear()
        self._src = src
        self._out = out
        self._job = job
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.log.add(f"--- {job.label}: {src} -> {out} ---")
        self.start_job(job, self.log, self._finished)

    def _stop(self) -> None:
        if self._job:
            self._job.stop()

    def _finished(self, code: int) -> None:
        self.stop_btn.setEnabled(False)
        ok = code == 0 and os.path.isfile(self._out) and os.path.getsize(self._out) > 0
        if not ok:
            self.run_btn.setEnabled(True)
            self.log.add(f"Failed (exit {code}). Nothing was removed.")
            if code != 0 and os.path.exists(self._out) and os.path.getsize(self._out) == 0:
                os.remove(self._out)
            return
        self.log.add(f"Done. Wrote {self._out}.")
        if not self._delete_after:
            self.run_btn.setEnabled(True)
            return
        self._remove_original()

    def _remove_original(self) -> None:
        src = self._src
        if self._delete_how != "shred":
            try:
                if os.path.isdir(src):
                    shutil.rmtree(src)
                else:
                    os.remove(src)
                self.log.add(f"Deleted {src}.")
            except OSError as exc:
                self.log.add(f"Could not delete {src}: {exc}")
            self.run_btn.setEnabled(True)
            return
        files = remove.files_under(src) if os.path.isdir(src) else [src]
        self._queue = files
        self._shred_next()

    def _shred_next(self) -> None:
        if not self._queue:
            if os.path.isdir(self._src):
                shutil.rmtree(self._src, ignore_errors=True)
            self.log.add(f"Overwrote and removed {self._src}.")
            self.run_btn.setEnabled(True)
            return
        f = self._queue.pop(0)
        job = UserJob("shred original", remove.shred_argv(f))
        self._job = job
        self.start_job(job, self.log, lambda c: (c and self.log.add(f"shred failed on {f}"),
                                                 self._shred_next()))
