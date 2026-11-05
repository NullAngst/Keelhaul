"""Entry point: harden the process, lock memory, load theme, start watchdog."""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if "--askpass" in argv:
        from .ui.dialogs import run_askpass
        return run_askpass(argv[1:])

    # Lock memory and disable core dumps before Qt allocates anything.
    from .core.secrets import harden_process
    mem = harden_process()

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from . import themes
    from .core import logbook, tools
    from .core.settings import DEFAULTS, Settings
    from .ui.base import AppContext
    from .ui.mainwindow import MainWindow
    from .watchdog.monitor import Watchdog

    app = QApplication(argv)
    app.setApplicationName("Keelhaul")
    app.setDesktopFileName("keelhaul")
    settings = Settings()
    app.setStyleSheet(themes.stylesheet(settings.get("theme")))
    logbook.set_enabled(settings.get("logging"))

    wd_keys = ("throttle", "cpu_ceiling", "rss_ceiling_mib", "load_ceiling",
               "sustain_seconds", "recover_seconds")
    wd_conf = {k: settings.get(k) for k in wd_keys if k in DEFAULTS}
    watchdog = Watchdog(wd_conf)
    watchdog.start()

    ctx = AppContext(tools.probe(), settings, watchdog, wd_conf, mem)
    win = MainWindow(ctx)
    win.show()

    def startup_warnings() -> None:
        if os.geteuid() == 0:
            QMessageBox.warning(win, "Running as root",
                                "Keelhaul is meant to run as your normal user and ask for root "
                                "only per operation. Running the whole GUI as root works, but it "
                                "gives up that separation.")
        if mem.swap_risk:
            QMessageBox.warning(
                win, "Cannot guarantee no swap",
                mem.summary() + "\n\nTo fix it, either turn swap off while you work "
                "(sudo swapoff -a), use zram instead of disk swap, or raise the memlock limit "
                "for your user (for example 'yourname - memlock unlimited' in "
                "/etc/security/limits.conf, or LimitMEMLOCK=infinity for your session).")

    QTimer.singleShot(0, startup_warnings)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
