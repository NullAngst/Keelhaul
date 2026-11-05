"""Shared tab scaffolding and app context."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QScrollArea, QVBoxLayout, QWidget

from ..core import tools
from ..core.escalation import RootSession
from .widgets import Banner


class AppContext:
    def __init__(self, caps, settings, watchdog, wd_conf: dict, mem) -> None:
        self.caps = caps
        self.settings = settings
        self.watchdog = watchdog
        self.wd_conf = wd_conf
        self.mem = mem
        self.running: set = set()

    def track(self, job) -> None:
        self.running.add(job)
        self.watchdog.register(job)
        job.finished.connect(lambda _c, j=job: self.running.discard(j))

    def session(self) -> RootSession:
        return RootSession(self.settings.get("escalation"))


class TabBase(QWidget):
    key = ""
    title = ""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        self._lay = QVBoxLayout(inner)
        self._lay.setContentsMargins(14, 12, 14, 12)
        self._lay.setSpacing(10)
        self.banner = Banner()
        self._lay.addWidget(self.banner)
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(10)
        self._lay.addWidget(self.body, 1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def refresh_caps(self) -> None:
        st = self.ctx.caps.tab(self.key)
        text = st.note
        if st.missing and st.state != tools.READY:
            text += "\n" + tools.install_hint(st.missing, self.ctx.caps.family)
        self.banner.show_status(st.state, text if st.state != tools.READY or st.note else "")
        self.body.setEnabled(st.state != tools.DISABLED)

    def start_job(self, job, log=None, on_done=None) -> None:
        if log is not None:
            job.line.connect(log.add)
        if on_done is not None:
            job.finished.connect(on_done)
        self.ctx.track(job)
        job.start()

    def warn(self, text: str, title: str = "Keelhaul") -> None:
        QMessageBox.warning(self, title, text)

    def ask(self, text: str, title: str = "Keelhaul", yes: str = "Continue") -> bool:
        box = QMessageBox(QMessageBox.Icon.Warning, title, text, parent=self)
        ok = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.exec()
        return box.clickedButton() is ok

    @property
    def busy(self) -> bool:
        return False
