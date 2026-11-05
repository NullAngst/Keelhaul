"""Shared widgets."""

from __future__ import annotations

import os

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeySequence, QPainter, QPalette
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.secrets import SecretBuffer

try:
    from zxcvbn import zxcvbn
except ImportError:  # strength meter degrades to a length hint
    zxcvbn = None


def button(text: str, role: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if role:
        b.setProperty("role", role)
    return b


def note(text: str, obj: str = "Note") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(obj)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


class SecretEdit(QFrame):
    """Passphrase field that types straight into a locked SecretBuffer.

    A QLineEdit keeps its text in a QString that Keelhaul cannot zero, so
    this widget handles keys itself and only paints bullets. Each keystroke
    arrives as a one-character str from Qt, which is unavoidable; the full
    passphrase never exists as one str except inside the strength meter.
    """

    changed = pyqtSignal()
    submitted = pyqtSignal()

    def __init__(self, placeholder: str = "Passphrase", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SecretEdit")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.placeholder = placeholder
        self.buf = SecretBuffer()
        self._chars = 0

    def sizeHint(self) -> QSize:
        h = self.fontMetrics().height() + 14
        return QSize(220, h)

    def minimumSizeHint(self) -> QSize:
        return QSize(80, self.fontMetrics().height() + 14)

    def __len__(self) -> int:
        return self._chars

    def clear(self) -> None:
        self.buf.clear()
        self._chars = 0
        self.update()
        self.changed.emit()

    def take(self) -> SecretBuffer:
        """A copy for the job; the field keeps its own until clear()."""
        return self.buf.copy()

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        if ev.matches(QKeySequence.StandardKey.Paste):
            text = QGuiApplication.clipboard().text().replace("\r", "").replace("\n", "")
            if text:
                if self.buf.append(text.encode("utf-8")):
                    self._chars += len(text)
                del text
                self._changed()
            return
        if ev.key() == Qt.Key.Key_Backspace:
            if self._chars:
                self.buf.pop_char()
                self._chars -= 1
                self._changed()
            return
        if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.submitted.emit()
            return
        if ev.matches(QKeySequence.StandardKey.SelectAll) or ev.key() == Qt.Key.Key_Escape:
            if ev.key() == Qt.Key.Key_Escape:
                self.clear()
            return
        text = ev.text()
        if text and text.isprintable() and not (ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
            if self.buf.append(text.encode("utf-8")):
                self._chars += len(text)
                self._changed()
            return
        super().keyPressEvent(ev)

    def _changed(self) -> None:
        self.update()
        self.changed.emit()

    def paintEvent(self, ev) -> None:  # noqa: N802
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.contentsRect().adjusted(8, 0, -8, 0)
        col = self.palette().color(QPalette.ColorRole.WindowText)
        if not self._chars:
            col.setAlpha(110)
            p.setPen(col)
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.placeholder)
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        d = 7
        gap = 4
        cy = r.center().y() - d / 2 + 1
        shown = min(self._chars, max(1, (r.width() - 40) // (d + gap)))
        for i in range(shown):
            p.drawEllipse(QRectF(r.left() + i * (d + gap), cy, d, d))
        if shown < self._chars:
            p.setPen(col)
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, f"{self._chars}")


class StrengthMeter(QWidget):
    """zxcvbn estimate. Builds a transient str of the passphrase to score it."""

    def __init__(self, edit: SecretEdit) -> None:
        super().__init__()
        self.edit = edit
        self.bar = QProgressBar()
        self.bar.setRange(0, 4)
        self.bar.setTextVisible(False)
        self.bar.setObjectName("Meter")
        self.label = QLabel("")
        self.label.setObjectName("Muted")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        lay.addWidget(self.bar)
        lay.addWidget(self.label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._score)
        edit.changed.connect(self._timer.start)
        self.score = -1
        self.hide()

    def _score(self) -> None:
        n = len(self.edit)
        if n == 0:
            self.bar.setValue(0)
            self.label.setText("")
            self.score = -1
            self.hide()
            return
        self.show()
        if zxcvbn is None or n > 128:
            self.score = 4 if n >= 20 else (2 if n >= 12 else 0)
            self.bar.setValue(self.score)
            self.label.setText(f"{n} characters (install zxcvbn for a real estimate)")
            return
        text = bytes(self.edit.buf.view()).decode("utf-8", "replace")
        try:
            res = zxcvbn(text)
        finally:
            del text
        self.score = int(res["score"])
        self.bar.setValue(self.score)
        words = ["very weak", "weak", "fair", "strong", "very strong"][self.score]
        crack = res["crack_times_display"]["offline_slow_hashing_1e4_per_second"]
        tip = res.get("feedback", {}).get("warning") or ""
        self.label.setText(f"{words} ({self.score}/4). At 10,000 guesses a second: {crack}. {tip}".strip())
        self.bar.setProperty("role", "danger" if self.score < 3 else None)
        repolish(self.bar)


class PassphraseInput(QWidget):
    """One field, or a pair with confirm and a strength meter."""

    changed = pyqtSignal()

    def __init__(self, confirm: bool, meter: bool = True, label: str = "Passphrase") -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.first = SecretEdit(label)
        lay.addWidget(self.first)
        self.second = None
        self.meter = None
        self.match = None
        if confirm:
            self.second = SecretEdit("Confirm " + label.lower())
            lay.addWidget(self.second)
            self.match = QLabel("")
            self.match.setObjectName("Muted")
            self.match.hide()
            lay.addWidget(self.match)
            self.second.changed.connect(self._update)
        if meter:
            self.meter = StrengthMeter(self.first)
            lay.addWidget(self.meter)
        self.first.changed.connect(self._update)

    def _update(self) -> None:
        if self.second is not None and self.match is not None:
            if len(self.second):
                ok = self.first.buf.equals(self.second.buf)
                self.match.setText("Passphrases match." if ok else "Passphrases do not match.")
            else:
                self.match.setText("")
            self.match.setVisible(bool(self.match.text()))
        self.changed.emit()

    def valid(self) -> tuple[bool, str]:
        if not len(self.first):
            return False, "Enter a passphrase."
        if self.second is not None and not self.first.buf.equals(self.second.buf):
            return False, "The passphrases do not match."
        return True, ""

    def take(self) -> SecretBuffer:
        return self.first.take()

    def clear(self) -> None:
        self.first.clear()
        if self.second is not None:
            self.second.clear()


class PathPicker(QWidget):
    """Line edit + Browse, accepts drag and drop. mode: open, dir, any, save."""

    changed = pyqtSignal(str)

    def __init__(self, mode: str = "open", placeholder: str = "Drop a file here or browse",
                 caption: str = "Choose") -> None:
        super().__init__()
        self.mode = mode
        self.caption = caption
        self.setAcceptDrops(True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed.emit)
        lay.addWidget(self.edit, 1)
        self.browse = QPushButton("Browse")
        self.browse.clicked.connect(self._browse)
        lay.addWidget(self.browse)
        if mode == "any":
            self.folder_btn = QPushButton("Folder")
            self.folder_btn.clicked.connect(self._browse_dir)
            lay.addWidget(self.folder_btn)

    def path(self) -> str:
        return os.path.expanduser(self.edit.text().strip())

    def set_path(self, p: str) -> None:
        self.edit.setText(p)

    def _browse(self) -> None:
        if self.mode == "dir":
            self._browse_dir()
            return
        if self.mode == "save":
            p, _ = QFileDialog.getSaveFileName(self, self.caption, self.path())
        else:
            p, _ = QFileDialog.getOpenFileName(self, self.caption, self.path())
        if p:
            self.set_path(p)

    def _browse_dir(self) -> None:
        p = QFileDialog.getExistingDirectory(self, self.caption, self.path())
        if p:
            self.set_path(p)

    def dragEnterEvent(self, ev) -> None:  # noqa: N802
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:  # noqa: N802
        urls = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.set_path(urls[0])


class LogView(QPlainTextEdit):
    def __init__(self, height: int = 140) -> None:
        super().__init__()
        self.setObjectName("Log")
        self.setReadOnly(True)
        self.setMaximumBlockCount(4000)
        self.setMinimumHeight(height)
        self.setPlaceholderText("Backend output shows up here.")

    def add(self, text: str) -> None:
        self.appendPlainText(text)


class Banner(QLabel):
    """Top-of-tab notice for missing or degraded backends."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Banner")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hide()

    def show_status(self, state: str, text: str) -> None:
        self.setProperty("state", state)
        self.setText(text)
        repolish(self)
        self.setVisible(bool(text))


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color: palette(mid);")
    return f
