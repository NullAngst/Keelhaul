"""Drive Crypto tab: LUKS2 through cryptsetup."""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..backends import cryptsetup as cs
from ..core import devices
from ..core.runner import RootJob
from .base import TabBase
from .confirm import confirm
from .widgets import LogView, PassphraseInput, PathPicker, button, note, repolish

OPS = [
    ("format", "Format new LUKS2 container"),
    ("open", "Open (unlock)"),
    ("close", "Close (lock)"),
    ("addkey", "Add a key slot"),
    ("changekey", "Change a passphrase"),
    ("removekey", "Remove a key slot"),
    ("dump", "Show header info"),
    ("backup", "Back up the header"),
    ("restore", "Restore the header"),
]


class CryptoTab(TabBase):
    key = "crypto"
    title = "Drive Crypto"

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._devs: list[devices.Device] = []
        self._session = None
        self._job = None

        top = QGroupBox("Device")
        tl = QHBoxLayout(top)
        self.dev_combo = QComboBox()
        self.dev_combo.setMinimumWidth(380)
        tl.addWidget(self.dev_combo, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_devices)
        tl.addWidget(refresh)
        self.lay.addWidget(top)
        self.dev_flag = note("", "Warning")
        self.dev_flag.hide()
        self.lay.addWidget(self.dev_flag)

        opbox = QGroupBox("Operation")
        ol = QVBoxLayout(opbox)
        self.op_combo = QComboBox()
        for key, label in OPS:
            self.op_combo.addItem(label, key)
        ol.addWidget(self.op_combo)
        self.stack = QStackedWidget()
        ol.addWidget(self.stack)
        self.lay.addWidget(opbox)

        self.pages: dict[str, QWidget] = {}
        self._build_format()
        self._build_open()
        self._build_close()
        self._build_keyops()
        self._build_simple("dump", "Reads the LUKS header and lists key slots, cipher and KDF settings.")
        self._build_backup()
        self._build_restore()
        for key, _label in OPS:
            self.stack.addWidget(self.pages[key])

        row = QHBoxLayout()
        row.addStretch(1)
        self.run_btn = button("Run", "go")
        self.run_btn.clicked.connect(self.run)
        row.addWidget(self.run_btn)
        self.lay.addLayout(row)
        self.log = LogView()
        self.lay.addWidget(self.log, 1)

        self.op_combo.currentIndexChanged.connect(self._op_changed)
        self.dev_combo.currentIndexChanged.connect(self._dev_changed)
        self.refresh_devices()
        self._op_changed()

    # ---- pages --------------------------------------------------------
    def _build_format(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(note("Formats the device as a new LUKS2 container. Everything on it is "
                           "destroyed. Defaults: LUKS2, aes-xts-plain64, 512-bit key, 4096-byte "
                           "sectors, Argon2id."))
        self.fmt_pass = PassphraseInput(confirm=True)
        lay.addWidget(self.fmt_pass)
        adv = QGroupBox("Advanced (change cipher and KDF cost)")
        adv.setCheckable(True)
        adv.setChecked(False)
        adv_inner = QWidget()
        QVBoxLayout(adv).addWidget(adv_inner)
        form = QFormLayout(adv_inner)
        form.setContentsMargins(0, 0, 0, 0)
        adv_inner.hide()
        adv.toggled.connect(adv_inner.setVisible)
        self.cipher = QComboBox()
        self.cipher.addItems(cs.CIPHERS)
        self.keysize = QComboBox()
        self.keysize.addItems([str(k) for k in cs.KEY_SIZES])
        self.sector = QComboBox()
        self.sector.addItems([str(s) for s in cs.SECTOR_SIZES])
        self.pbkdf = QComboBox()
        self.pbkdf.addItems(cs.PBKDFS)
        self.iter_time = QSlider(Qt.Orientation.Horizontal)
        self.iter_time.setRange(cs.ITER_TIME_FLOOR_MS // 500, 20)
        self.iter_time.setValue(4)
        self.iter_lab = QLabel()
        self.memory = QSlider(Qt.Orientation.Horizontal)
        self.memory.setRange(cs.MEMORY_FLOOR_KIB // (256 * 1024), cs.MEMORY_MAX_KIB // (256 * 1024))
        self.memory.setValue(4)
        self.mem_lab = QLabel()
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 16)
        self.parallel.setValue(4)
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("optional")
        form.addRow("Cipher", self.cipher)
        form.addRow("Key size (bits)", self.keysize)
        form.addRow("Sector size", self.sector)
        form.addRow("KDF", self.pbkdf)
        ir = QHBoxLayout()
        ir.addWidget(self.iter_time, 1)
        ir.addWidget(self.iter_lab)
        form.addRow("Unlock time", ir)
        mr = QHBoxLayout()
        mr.addWidget(self.memory, 1)
        mr.addWidget(self.mem_lab)
        form.addRow("Argon2 memory", mr)
        form.addRow("Argon2 threads", self.parallel)
        form.addRow("Label", self.label_edit)
        form.addRow(note("Floors: 1 s unlock time and 256 MiB memory. cryptsetup lowers "
                         "memory on its own if the machine does not have enough RAM. A "
                         "pbkdf2 KDF is weaker than Argon2 and only makes sense for old "
                         "bootloaders.", "Muted"))
        self.iter_time.valueChanged.connect(self._update_kdf_labels)
        self.memory.valueChanged.connect(self._update_kdf_labels)
        self._update_kdf_labels()
        self.adv = adv
        lay.addWidget(adv)
        self.pages["format"] = w

    def _update_kdf_labels(self) -> None:
        self.iter_lab.setText(f"{self.iter_time.value() * 0.5:.1f} s")
        self.mem_lab.setText(f"{self.memory.value() * 256} MiB")

    def _build_open(self) -> None:
        w = QWidget()
        form = QFormLayout(w)
        self.open_pass = PassphraseInput(confirm=False, meter=False)
        self.map_name = QLineEdit()
        self.map_name.setPlaceholderText("mapper name")
        form.addRow("Passphrase", self.open_pass)
        form.addRow("Open as /dev/mapper/", self.map_name)
        form.addRow(note("Keelhaul only unlocks the container. Mount the filesystem inside "
                         "with your file manager or mount."))
        self.pages["open"] = w

    def _build_close(self) -> None:
        w = QWidget()
        form = QFormLayout(w)
        self.close_combo = QComboBox()
        self.close_combo.setEditable(True)
        form.addRow("Mapper name", self.close_combo)
        form.addRow(note("Unmount the filesystem first, or cryptsetup refuses to close it."))
        self.pages["close"] = w

    def _build_keyops(self) -> None:
        for key, text in (("addkey", "Adds a new passphrase in a free key slot. Either passphrase "
                                     "will unlock the container."),
                          ("changekey", "Replaces one passphrase with a new one in place.")):
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(note(text))
            old = PassphraseInput(confirm=False, meter=False, label="Existing passphrase")
            new = PassphraseInput(confirm=True, label="New passphrase")
            lay.addWidget(old)
            lay.addWidget(new)
            setattr(self, f"{key}_old", old)
            setattr(self, f"{key}_new", new)
            self.pages[key] = w
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(note("Removes the key slot that this passphrase unlocks. cryptsetup "
                           "refuses to remove the last remaining slot from here."))
        self.remove_pass = PassphraseInput(confirm=False, meter=False, label="Passphrase to remove")
        lay.addWidget(self.remove_pass)
        self.pages["removekey"] = w

    def _build_simple(self, key: str, text: str) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(note(text))
        self.pages[key] = w

    def _build_backup(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(note("The LUKS header holds the key slots. If it gets corrupted, the "
                           "container is gone even with the right passphrase. A backup fixes "
                           "that.", "Note"))
        lay.addWidget(note("The backup file can unlock the container with any passphrase that "
                           "was valid when you made it, even after you change it on the drive. "
                           "Treat it like the drive itself. Keelhaul never writes it anywhere "
                           "you did not pick.", "Warning"))
        self.backup_path = PathPicker("save", "Where to save the header backup", "Save header backup")
        lay.addWidget(self.backup_path)
        self.pages["backup"] = w

    def _build_restore(self) -> None:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(note("Writes a saved header back over the device's current header. Key "
                           "slots revert to the ones in the backup.", "Warning"))
        self.restore_path = PathPicker("open", "Header backup file", "Choose header backup")
        lay.addWidget(self.restore_path)
        self.pages["restore"] = w

    # ---- state --------------------------------------------------------
    def refresh_devices(self) -> None:
        self.dev_combo.clear()
        try:
            self._devs = devices.list_targets_for_crypto()
        except Exception as exc:  # lsblk missing or failing
            self._devs = []
            self.log.add(f"Could not list devices: {exc}")
        for d in self._devs:
            flags = []
            if d.protected:
                flags.append("SYSTEM")
            elif d.mounted:
                flags.append("MOUNTED")
            if d.is_luks:
                flags.append("LUKS")
            suffix = f"   [{', '.join(flags)}]" if flags else ""
            self.dev_combo.addItem(d.describe() + suffix)
        self.close_combo.clear()
        try:
            names = sorted(n for n in os.listdir("/dev/mapper") if n != "control")
        except OSError:
            names = []
        self.close_combo.addItems(names)
        self._dev_changed()

    def _current(self) -> devices.Device | None:
        i = self.dev_combo.currentIndex()
        return self._devs[i] if 0 <= i < len(self._devs) else None

    def _op(self) -> str:
        return self.op_combo.currentData()

    def _dev_changed(self) -> None:
        d = self._current()
        if d and not self.map_name.text():
            self.map_name.setPlaceholderText(f"keelhaul_{d.name}")
        self._update_flag()

    def _op_changed(self) -> None:
        op = self._op()
        self.stack.setCurrentWidget(self.pages[op])
        destructive = op in ("format", "restore")
        self.run_btn.setProperty("role", "danger" if destructive else "go")
        self.run_btn.setText({"format": "Format", "restore": "Restore header", "open": "Open",
                              "close": "Close", "dump": "Show info", "backup": "Back up",
                              "addkey": "Add key", "changekey": "Change passphrase",
                              "removekey": "Remove key"}[op])
        repolish(self.run_btn)
        self.dev_combo.setEnabled(op != "close")
        self._update_flag()

    def _update_flag(self) -> None:
        d = self._current()
        op = self._op()
        msg = ""
        if d and op not in ("close", "format") and not d.is_luks:
            msg = f"{d.path} does not look like a LUKS container."
        if d and op == "format" and (d.mounted or d.protected):
            msg = f"{d.path} is in use ({', '.join(d.all_mounts)}). Formatting is refused unless you override it."
        self.dev_flag.setText(msg)
        self.dev_flag.setVisible(bool(msg))

    @property
    def busy(self) -> bool:
        return self._job is not None and self._job.running

    def _options(self) -> cs.FormatOptions:
        o = cs.FormatOptions()
        if self.adv.isChecked():
            o.cipher = self.cipher.currentText()
            o.key_size = int(self.keysize.currentText())
            o.sector_size = int(self.sector.currentText())
            o.pbkdf = self.pbkdf.currentText()
            o.iter_time_ms = self.iter_time.value() * 500
            o.memory_kib = self.memory.value() * 256 * 1024
            o.parallel = self.parallel.value()
            o.label = self.label_edit.text().strip()
        return o

    # ---- run ----------------------------------------------------------
    def run(self) -> None:
        if self.busy:
            return
        op = self._op()
        dev = self._current()
        if op != "close" and dev is None:
            self.warn("Pick a device first.")
            return
        secrets = []
        argv: list[str]
        chown = None
        inputs = []
        if op == "format":
            ok, why = self.fmt_pass.valid()
            if not ok:
                self.warn(why)
                return
            if self.fmt_pass.meter and 0 <= self.fmt_pass.meter.score < 3:
                if not self.ask("That passphrase is rated weak. LUKS is only as strong as its "
                                "passphrase. Use it anyway?", yes="Use it"):
                    return
            if not confirm(self, "Format as LUKS2", "format", [dev],
                           f"Formatting {dev.path} destroys everything on it. There is no undo.",
                           "Format"):
                return
            err = devices.reverify(dev)
            if err:
                self.warn(err)
                return
            target = devices.by_id(dev.path)
            argv = cs.format_argv(target, self._options())
            secrets = [self.fmt_pass.take()]
            inputs = [self.fmt_pass]
        elif op == "open":
            ok, why = self.open_pass.valid()
            if not ok:
                self.warn(why)
                return
            name = self.map_name.text().strip() or f"keelhaul_{dev.name}"
            if not cs.MAPPER_NAME.match(name):
                self.warn("Mapper names can only use letters, digits, dot, dash and underscore.")
                return
            argv = cs.open_argv(dev.path, name)
            secrets = [self.open_pass.take()]
            inputs = [self.open_pass]
        elif op == "close":
            name = self.close_combo.currentText().strip()
            if not cs.MAPPER_NAME.match(name):
                self.warn("Enter a valid mapper name.")
                return
            argv = cs.close_argv(name)
        elif op in ("addkey", "changekey"):
            old = getattr(self, f"{op}_old")
            new = getattr(self, f"{op}_new")
            for pi in (old, new):
                ok, why = pi.valid()
                if not ok:
                    self.warn(why)
                    return
            build = cs.addkey_argv if op == "addkey" else cs.changekey_argv
            argv = build(dev.path, self._options())
            secrets = [old.take(), new.take()]
            inputs = [old, new]
        elif op == "removekey":
            ok, why = self.remove_pass.valid()
            if not ok:
                self.warn(why)
                return
            if not self.ask(f"Remove the key slot on {dev.path} that this passphrase unlocks? "
                            "Make sure another passphrase or a header backup still works.",
                            yes="Remove"):
                return
            argv = cs.removekey_argv(dev.path)
            secrets = [self.remove_pass.take()]
            inputs = [self.remove_pass]
        elif op == "dump":
            argv = cs.dump_argv(dev.path)
        elif op == "backup":
            out = self.backup_path.path()
            if not out or not os.path.isabs(out):
                self.warn("Pick an absolute path for the backup file.")
                return
            if os.path.exists(out):
                self.warn("That file already exists. cryptsetup will not overwrite it; pick a new name.")
                return
            argv = cs.header_backup_argv(dev.path, out)
            chown = out
        elif op == "restore":
            src = self.restore_path.path()
            if not os.path.isfile(src):
                self.warn("Pick the header backup file to restore.")
                return
            if not confirm(self, "Restore LUKS header", "restore", [dev],
                           f"This overwrites the LUKS header on {dev.path}. Key slots added "
                           "since the backup stop working.", "Restore header"):
                return
            argv = cs.header_restore_argv(dev.path, src)
        else:
            return

        self._session = self.ctx.session()
        self._session.failed.connect(lambda m: self.log.add("Escalation failed: " + m))
        job = RootJob(self._session, self.op_combo.currentText(), argv, secrets=secrets,
                      throttle=False, chown_after=chown)
        for s in secrets:
            s.destroy()
        for pi in inputs:
            pi.clear()
        self._job = job
        self.run_btn.setEnabled(False)
        self.log.add(f"--- {self.op_combo.currentText()} ---")
        self.start_job(job, self.log, self._finished)

    def _finished(self, code: int) -> None:
        self.run_btn.setEnabled(True)
        if self._session:
            self._session.close()
        self.log.add("Done." if code == 0 else f"Failed (exit {code}).")
        if code == 0 and self._op() == "backup":
            self.log.add(f"Header saved to {self.backup_path.path()} (mode 600).")
        self.refresh_devices()
