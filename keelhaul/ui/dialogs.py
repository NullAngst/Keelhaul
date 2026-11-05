"""Capabilities panel, settings, about, and the sudo askpass prompt."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from .. import __version__, themes
from ..core import logbook, tools
from .widgets import note

TAB_NAMES = [("crypto", "Drive Crypto"), ("vault", "File Vault"), ("scrub", "Metadata Scrub"),
             ("hash", "Hash Check"), ("destroy", "Disk Destroyer"), ("freespace", "Free-Space Wipe")]


class CapabilitiesDialog(QDialog):
    def __init__(self, parent, caps: tools.Capabilities, mem) -> None:
        super().__init__(parent)
        self.setWindowTitle("Backends")
        self.setMinimumSize(640, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(note(f"Detected package manager: {caps.family}. Keelhaul bundles none of "
                           "these tools; it calls the copies your system provides.", "Muted"))
        tabs = QTreeWidget()
        tabs.setHeaderLabels(["Tab", "State", "Notes"])
        for key, name in TAB_NAMES:
            st = caps.tab(key)
            text = st.note
            if st.missing:
                text += ("\n" if text else "") + tools.install_hint(st.missing, caps.family)
            QTreeWidgetItem(tabs, [name, st.state, text])
        tabs.resizeColumnToContents(0)
        lay.addWidget(tabs)
        tree = QTreeWidget()
        tree.setHeaderLabels(["Tool", "Path"])
        for t in tools.TOOLS:
            QTreeWidgetItem(tree, [t, caps.paths.get(t) or "not found"])
        tree.resizeColumnToContents(0)
        lay.addWidget(tree)
        lay.addWidget(note("Memory: " + mem.summary(), "Note"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class SettingsDialog(QDialog):
    def __init__(self, parent, settings) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        form = QFormLayout(self)
        self.theme = QComboBox()
        for name in themes.names():
            self.theme.addItem(themes.THEMES[name]["label"], name)
        self.theme.setCurrentIndex(max(0, themes.names().index(settings.get("theme"))
                                       if settings.get("theme") in themes.names() else 0))
        self.esc = QComboBox()
        for k, label in (("auto", "Automatic (pkexec, then sudo)"), ("pkexec", "pkexec only"),
                         ("sudo", "sudo only")):
            self.esc.addItem(label, k)
        self.esc.setCurrentIndex(["auto", "pkexec", "sudo"].index(settings.get("escalation"))
                                 if settings.get("escalation") in ("auto", "pkexec", "sudo") else 0)
        self.throttle = QCheckBox("Pause Keelhaul's jobs when they overload the machine")
        self.throttle.setChecked(settings.get("throttle"))
        self.cpu = QSpinBox()
        self.cpu.setRange(10, 100)
        self.cpu.setSuffix(" % of all cores")
        self.cpu.setValue(settings.get("cpu_ceiling"))
        self.rss = QSpinBox()
        self.rss.setRange(128, 65536)
        self.rss.setSuffix(" MiB")
        self.rss.setValue(settings.get("rss_ceiling_mib"))
        self.sustain = QSpinBox()
        self.sustain.setRange(2, 120)
        self.sustain.setSuffix(" s")
        self.sustain.setValue(settings.get("sustain_seconds"))
        self.recover = QSpinBox()
        self.recover.setRange(1, 60)
        self.recover.setSuffix(" s")
        self.recover.setValue(settings.get("recover_seconds"))
        self.logging = QCheckBox("Keep an operational log (commands, exit codes, times)")
        self.logging.setChecked(settings.get("logging"))
        form.addRow("Theme", self.theme)
        form.addRow("Privilege escalation", self.esc)
        form.addRow(self.throttle)
        form.addRow("CPU ceiling", self.cpu)
        form.addRow("Memory ceiling", self.rss)
        form.addRow("Over the ceiling for", self.sustain)
        form.addRow("Pause for", self.recover)
        form.addRow(self.logging)
        path = QLineEdit(logbook.log_path())
        path.setReadOnly(True)
        form.addRow("Log file", path)
        form.addRow(note("The log never contains passphrases or file contents. Crypto key "
                         "derivation is never throttled, since a slowed benchmark would pick "
                         "weaker KDF parameters.", "Muted"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _save(self) -> None:
        s = self.settings
        s.set("theme", self.theme.currentData())
        s.set("escalation", self.esc.currentData())
        s.set("throttle", self.throttle.isChecked())
        s.set("cpu_ceiling", self.cpu.value())
        s.set("rss_ceiling_mib", self.rss.value())
        s.set("sustain_seconds", self.sustain.value())
        s.set("recover_seconds", self.recover.value())
        s.set("logging", self.logging.isChecked())
        self.accept()


class AboutDialog(QDialog):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Keelhaul")
        lay = QVBoxLayout(self)
        title = QLabel(f"<h2>Keelhaul {__version__}</h2>")
        lay.addWidget(title)
        lay.addWidget(note(
            "A GUI over trusted Linux security tools: cryptsetup, age, gpg, mat2, exiftool, "
            "coreutils, nvme-cli, hdparm, util-linux and secure-delete. Keelhaul has no crypto "
            "or wiping code of its own.\n\nLicensed under the GNU GPL, version 3.", "Muted"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


def run_askpass(argv: list[str]) -> int:
    """SUDO_ASKPASS mode: show one password prompt, print it, exit.

    Runs in its own short-lived process as the user. sudo reads the line
    from stdout and the process ends right after.
    """
    app = QApplication(sys.argv[:1])
    prompt = " ".join(a for a in argv if a != "--askpass").strip() or "Password for sudo:"
    text, ok = QInputDialog.getText(None, "Keelhaul needs root", prompt, QLineEdit.EchoMode.Password)
    del app
    if not ok:
        return 1
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return 0
