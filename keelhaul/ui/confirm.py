"""The gate in front of every irreversible operation.

For each device: model, serial and size are shown back, and you type the
device name to arm it. Mounted or system devices need a second typed
override. The final button stays disabled until every field matches.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ..core.devices import Device, human_size
from .widgets import note, repolish


class ConfirmDestructive(QDialog):
    def __init__(self, parent, title: str, action: str, devices: list[Device],
                 consequence: str, button_text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        warn = note(consequence, "Warning")
        lay.addWidget(warn)
        self._checks: list[tuple[QLineEdit, str]] = []
        for dev in devices:
            box = QGroupBox(dev.path)
            form = QFormLayout(box)
            form.addRow("Model", QLabel(dev.model or "unknown"))
            form.addRow("Serial", QLabel(dev.serial or "unknown"))
            form.addRow("Size", QLabel(f"{human_size(dev.size)} ({dev.size:,} bytes)"))
            if dev.all_mounts:
                form.addRow("Mounted at", QLabel(", ".join(dev.all_mounts)))
            arm = QLineEdit()
            arm.setPlaceholderText(dev.name)
            form.addRow(f"Type {dev.name} to arm", arm)
            self._checks.append((arm, dev.name))
            if dev.mounted or dev.protected:
                why = ("holds the running system (root, boot or swap)" if dev.protected
                       else "is mounted")
                phrase = f"{action} {dev.name} anyway"
                lab = QLabel(f"{dev.path} {why}. Refused by default.\nType: {phrase}")
                lab.setObjectName("Warning")
                lab.setWordWrap(True)
                form.addRow(lab)
                over = QLineEdit()
                over.setPlaceholderText(phrase)
                form.addRow("Override", over)
                self._checks.append((over, phrase))
            lay.addWidget(box)
        for edit, _want in self._checks:
            edit.textChanged.connect(self._update)
        self.buttons = QDialogButtonBox()
        self.ok = self.buttons.addButton(button_text, QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok.setProperty("role", "danger")
        repolish(self.ok)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.ok.setEnabled(False)
        self.ok.setAutoDefault(False)

    def _update(self) -> None:
        self.ok.setEnabled(all(e.text().strip() == want for e, want in self._checks))


def confirm(parent, title: str, action: str, devices: list[Device], consequence: str,
            button_text: str) -> bool:
    dlg = ConfirmDestructive(parent, title, action, devices, consequence, button_text)
    return dlg.exec() == QDialog.DialogCode.Accepted
