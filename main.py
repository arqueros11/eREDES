"""eREDES — PRINTER & NETWORK MANAGER. Punto de entrada."""
from __future__ import annotations

import argparse
import logging
import sys
import traceback


def main() -> int:
    ap = argparse.ArgumentParser(description="eREDES — Printer & Network Manager")
    ap.add_argument("--debug", action="store_true", help="Logs detallados en consola")
    ap.add_argument("--screenshot", metavar="PNG", help="(pruebas) captura la ventana y sale")
    ap.add_argument("--page", default="printers", help="(pruebas) página inicial")
    args = ap.parse_args()

    # Ceder el intérprete con más frecuencia: la interfaz sigue fluida aunque haya
    # decenas de hilos de red trabajando (escaneos, speed test).
    sys.setswitchinterval(0.0005)

    from app.core import paths
    paths.ensure_layout()
    # Cierres inesperados (fallos nativos): quedan registrados en logs/crash.log
    import faulthandler
    try:
        _crash = open(paths.logs_dir() / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(_crash)
    except OSError:
        pass
    from app.core.logging_setup import setup_logging
    setup_logging(args.debug)
    log = logging.getLogger("eredes")

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    def qt_messages(mode, _ctx, msg):  # mensajes internos de Qt -> log
        if mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            log.error("Qt: %s", msg)
        elif mode == QtMsgType.QtWarningMsg:
            log.warning("Qt: %s", msg)
    qInstallMessageHandler(qt_messages)
    app = QApplication(sys.argv)
    app.setApplicationName(paths.APP_NAME)
    app.setOrganizationName("eREDES")
    app.setApplicationVersion(paths.APP_VERSION)
    f = QFont()
    if sys.platform.startswith("win"):
        f.setFamily("Segoe UI")
    f.setPointSize(15 if sys.platform == "darwin" else 11)
    app.setFont(f)

    def excepthook(exc_type, exc, tb):
        msg = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Excepción no controlada:\n%s", msg)
        try:
            from app.ui.bus import notify
            notify("error", "Error inesperado", str(exc))
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = excepthook

    from app.core import config
    from app.drivers.library import DriverLibrary
    from app.ui.icons import app_icon
    from app.ui.main_window import MainWindow, apply_theme

    apply_theme(config.get("theme") or "dark")
    app.setWindowIcon(app_icon())
    log.info("eREDES %s iniciado. Carpeta de datos: %s", paths.APP_VERSION, paths.home_dir())

    w = MainWindow()
    w.show()
    from app.core.tasks import run_task
    run_task(lambda _c: DriverLibrary().sync(),  # la ventana aparece al instante; la biblioteca se lee aparte
             on_error=lambda m: log.error("No se pudo sincronizar la biblioteca de drivers: %s", m))
    if args.page != "printers":
        w.navigate(args.page)
    if args.screenshot:
        def snap():
            w.grab().save(args.screenshot)
            app.quit()
        QTimer.singleShot(int(__import__("os").environ.get("EREDES_SNAP_DELAY", "6000")), snap)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
