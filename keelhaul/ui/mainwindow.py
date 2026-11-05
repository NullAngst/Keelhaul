"""Main window: toolbar row, folder tabs, status bar."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QTabWidget,
    QToolBar,
    QToolButton,
    QWidget,
)

from .. import themes
from ..core import logbook, tools
from .base import AppContext
from .dialogs import AboutDialog, CapabilitiesDialog, SettingsDialog
from .tab_crypto import CryptoTab
from .tab_destroyer import DestroyerTab
from .tab_freespace import FreeSpaceTab
from .tab_hash import HashTab
from .tab_scrub import ScrubTab
from .tab_vault import VaultTab
from .widgets import repolish


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle("Keelhaul")
        self.resize(980, 780)

        bar = QToolBar("Main")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(bar)
        bar.addAction("Settings", self.open_settings)
        bar.addAction("About", lambda: AboutDialog(self).exec())
        theme_btn = QToolButton()
        theme_btn.setText("Theme")
        theme_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(theme_btn)
        group = QActionGroup(menu)
        current = ctx.settings.get("theme")
        for name in themes.names():
            act = QAction(themes.THEMES[name]["label"], menu, checkable=True)
            act.setChecked(name == current)
            act.triggered.connect(lambda _c, n=name: self.set_theme(n))
            group.addAction(act)
            menu.addAction(act)
        theme_btn.setMenu(menu)
        bar.addWidget(theme_btn)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        self.caps_btn = QToolButton()
        self.caps_btn.setObjectName("CapsIndicator")
        self.caps_btn.clicked.connect(self.open_caps)
        bar.addWidget(self.caps_btn)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        self.pages = [CryptoTab(ctx), VaultTab(ctx), ScrubTab(ctx), HashTab(ctx),
                      DestroyerTab(ctx), FreeSpaceTab(ctx)]
        for p in self.pages:
            self.tabs.addTab(p, p.title)
        self.setCentralWidget(self.tabs)

        self.usage = QLabel("")
        self.usage.setObjectName("Muted")
        self.statusBar().addPermanentWidget(self.usage)
        self.mem_label = QLabel("")
        self.mem_label.setObjectName("Muted")
        self.statusBar().addWidget(self.mem_label, 1)
        ctx.watchdog.usage.connect(self._usage)
        ctx.watchdog.throttled.connect(self._throttled)
        self.refresh_caps()
        self._mem_status()

    def refresh_caps(self) -> None:
        ready = degraded = 0
        for p in self.pages:
            p.refresh_caps()
            st = self.ctx.caps.tab(p.key).state
            ready += st == tools.READY
            degraded += st == tools.DEGRADED
            i = self.tabs.indexOf(p)
            self.tabs.setTabToolTip(i, self.ctx.caps.tab(p.key).note or "Ready")
        total = len(self.pages)
        self.caps_btn.setText(f"Backends: {ready}/{total} ready" + (f", {degraded} degraded" if degraded else ""))
        self.caps_btn.setProperty("state", "ready" if ready == total else "degraded")
        repolish(self.caps_btn)

    def _mem_status(self) -> None:
        m = self.ctx.mem
        if m.whole_process:
            self.mem_label.setText("Memory locked: nothing can swap to disk.")
        elif not m.swap_on_disk:
            self.mem_label.setText("Secret buffers locked. No disk swap active.")
        else:
            self.mem_label.setText("WARNING: disk swap active and memory only partly locked. See Backends.")

    def _usage(self, cpu: float, rss: int) -> None:
        if not self.ctx.running:
            self.usage.setText("")
            return
        self.usage.setText(f"Jobs: {len(self.ctx.running)}  CPU {cpu:.0f}%  RSS {rss // (1024 * 1024)} MiB")

    def _throttled(self, job, paused: bool) -> None:
        self.statusBar().showMessage(
            f"Watchdog {'paused' if paused else 'resumed'} {job.label}", 4000)

    def set_theme(self, name: str) -> None:
        self.ctx.settings.set("theme", name)
        QApplication.instance().setStyleSheet(themes.stylesheet(name))

    def open_settings(self) -> None:
        if SettingsDialog(self, self.ctx.settings).exec():
            s = self.ctx.settings
            self.set_theme(s.get("theme"))
            logbook.set_enabled(s.get("logging"))
            self.ctx.wd_conf.update({k: s.get(k) for k in self.ctx.wd_conf})

    def open_caps(self) -> None:
        self.ctx.caps = tools.probe()
        self.refresh_caps()
        CapabilitiesDialog(self, self.ctx.caps, self.ctx.mem).exec()

    def closeEvent(self, ev) -> None:  # noqa: N802
        busy = [p.title for p in self.pages if p.busy] or (["background jobs"] if self.ctx.running else [])
        if busy:
            r = QMessageBox.warning(
                self, "Jobs still running",
                f"Still running: {', '.join(busy)}.\n\nRoot jobs keep running until they finish "
                "even after Keelhaul closes. User jobs are stopped. Quit anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            from ..core.runner import UserJob
            for j in list(self.ctx.running):
                if isinstance(j, UserJob):
                    j.stop()
        self.ctx.watchdog.shutdown()
        ev.accept()
