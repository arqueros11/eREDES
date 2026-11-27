"""Ventana principal de eREDES: sidebar + barra superior + páginas + notificaciones."""
from __future__ import annotations

import importlib
import logging

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from app.core import config
from app.core.paths import APP_NAME, APP_SUBTITLE, version_label
from app.core.platform_info import IS_WINDOWS, is_admin, relaunch_as_admin_windows
from app.core.tasks import cancel_all

from . import icons, theme
from .bus import bus, notify
from .widgets import Badge, Toast, button, confirm

log = logging.getLogger("eredes.ui")

# Herramientas, ordenadas por uso. Sin dashboard: la app abre directamente en Impresoras.
PAGES = [
    ("", [("printers", "Impresoras", "app.ui.pages.printers:PrintersPage"),
          ("scanner", "Escáner", "app.ui.pages.scanner:ScannerPage"),
          ("drivers", "Drivers", "app.ui.pages.drivers:DriversPage"),
          ("recovery", "Recuperar IP", "app.ui.pages.ip_recovery:IpRecoveryPage"),
          ("diagnostics", "Diagnóstico", "app.ui.pages.diagnostics:DiagnosticsPage"),
          ("speedtest", "Speed Test", "app.ui.pages.speedtest:SpeedTestPage"),
          ("monitor", "Monitor", "app.ui.pages.monitor:MonitorPage"),
          ("reports", "Informes", "app.ui.pages.reports:ReportsPage"),
          ("settings", "Ajustes", "app.ui.pages.settings:SettingsPage")]),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — {APP_SUBTITLE}")
        self.setWindowIcon(icons.app_icon())
        self.resize(1360, 860)
        self.setMinimumSize(1024, 660)
        self.pages: dict[str, QWidget] = {}
        self.page_specs: dict[str, str] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.toasts: list[Toast] = []
        self.current_key = ""

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_sidebar())
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        rv.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        rv.addWidget(self.stack, 1)
        h.addWidget(right, 1)
        self.setCentralWidget(central)
        self.setUnifiedTitleAndToolBarOnMac(True)


        bus.notify.connect(self.show_toast)
        bus.navigate.connect(self.navigate)
        bus.new_printer.connect(self._open_new_printer)
        bus.diagnose_printer.connect(self._open_diagnose)
        bus.recovery_target.connect(self._open_recovery)
        bus.assign_free_ip.connect(self._assign_free_ip)
        self.navigate("printers")
        QTimer.singleShot(800, self._startup_checks)
        if config.get("auto_check_updates"):
            QTimer.singleShot(4000, lambda: self.check_updates(manual=False))

    # ------------------------------------------------------------ construcción
    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(250)
        v = QVBoxLayout(side)
        v.setContentsMargins(0, 18, 0, 10)
        v.setSpacing(0)
        brand = QHBoxLayout()
        brand.setContentsMargins(22, 4, 18, 6)
        brand.setSpacing(10)
        self.logo_lbl = QLabel()
        self.logo_lbl.setPixmap(icons.logo_mark(30))
        brand.addWidget(self.logo_lbl)
        name = QLabel("eREDES")
        name.setObjectName("BrandName")
        brand.addWidget(name)
        brand.addStretch(1)
        v.addLayout(brand)
        group = QButtonGroup(self)
        group.setExclusive(True)
        pal = theme.current()
        v.addSpacing(14)
        for section, items in PAGES:
            if section:
                lbl = QLabel(section)
                lbl.setObjectName("NavSection")
                v.addWidget(lbl)
            for key, title, spec in items:
                b = QPushButton(f"  {title}")
                b.setObjectName("NavButton")
                b.setCheckable(True)
                b.setIcon(icons.icon(icons.NAV[key], pal.sidebar_text))
                b.setIconSize(QSize(22, 22))
                b.setMinimumHeight(46)
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, k=key: self.navigate(k))
                group.addButton(b)
                v.addWidget(b)
                self.nav_buttons[key] = b
                self.page_specs[key] = spec
        v.addStretch(1)
        foot = QHBoxLayout()
        foot.setContentsMargins(18, 0, 14, 4)
        ver = QLabel(version_label())
        ver.setObjectName("SidebarFooter")
        foot.addWidget(ver)
        foot.addStretch(1)
        self.update_btn = QPushButton("  Actualizar")
        self.update_btn.setObjectName("UpdateButton")
        self.update_btn.setIcon(icons.icon("mdi6.download", theme.current().primary))
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.setToolTip("Buscar e instalar la última versión desde GitHub")
        self.update_btn.clicked.connect(lambda: self.check_updates(manual=True))
        foot.addWidget(self.update_btn)
        v.addLayout(foot)
        return side

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(64)
        h = QHBoxLayout(bar)
        h.setContentsMargins(24, 10, 18, 10)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        self.title_lbl = QLabel("")
        self.title_lbl.setObjectName("PageTitle")
        self.sub_lbl = QLabel("")
        self.sub_lbl.setObjectName("PageSubtitle")
        tv.addWidget(self.title_lbl)
        self.sub_lbl.hide()  # interfaz minimalista: solo el título
        h.addLayout(tv, 1)
        pal = theme.current()
        if is_admin():
            h.addWidget(Badge("ADMINISTRADOR", pal.success))
        elif IS_WINDOWS:
            h.addWidget(button("Reiniciar como administrador", "", "mdi6.shield-account", self._elevate,
                               "Necesario para crear impresoras, puertos e instalar drivers en Windows"))
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("Ghost")
        self.theme_btn.setFixedSize(38, 38)
        self.theme_btn.setToolTip("Cambiar modo claro/oscuro")
        self.theme_btn.clicked.connect(self.toggle_theme)
        self._update_theme_icon()
        h.addWidget(self.theme_btn)
        return bar

    # ------------------------------------------------------------ navegación
    def _page(self, key: str) -> QWidget:
        if key not in self.pages:
            mod, cls = self.page_specs[key].split(":")
            page = getattr(importlib.import_module(mod), cls)()
            self.pages[key] = page
            self.stack.addWidget(page)
        return self.pages[key]

    def navigate(self, key: str) -> None:
        if key not in self.page_specs:
            return
        if self.current_key and self.current_key in self.pages:
            self.pages[self.current_key].on_hide()
        page = self._page(key)
        self.stack.setCurrentWidget(page)
        self._fade_in(page)
        self.nav_buttons[key].setChecked(True)
        self.title_lbl.setText(page.title)
        self.sub_lbl.setText(page.subtitle)
        self.current_key = key
        self._paint_nav_icons()
        page.on_show()

    def _fade_in(self, w: QWidget) -> None:
        """Aparición suave (160 ms). El efecto se retira al terminar para no penalizar el repintado."""
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        eff = QGraphicsOpacityEffect(w)
        w.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b"opacity", w)
        anim.setDuration(160)
        anim.setStartValue(0.25)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: w.setGraphicsEffect(None))
        anim.start(QPropertyAnimation.DeleteWhenStopped)

    def _paint_nav_icons(self):
        pal = theme.current()
        for k, b in self.nav_buttons.items():
            b.setIcon(icons.icon(icons.NAV[k], "#FFFFFF" if k == self.current_key else pal.primary))

    def _open_new_printer(self, ip: str, port: int):
        self.navigate("printers")
        self._page("printers").open_create(ip, port)

    def _open_diagnose(self, ip: str, port: int, queue: str):
        self.navigate("diagnostics")
        self._page("diagnostics").preset(ip, port, queue)

    def _assign_free_ip(self, ip: str):
        self.navigate("recovery")
        self._page("recovery").preset_new_ip(ip)

    def _open_recovery(self, ip: str):
        self.navigate("recovery")
        self._page("recovery").preset_target(ip)

    # ------------------------------------------------------------ tema
    def toggle_theme(self):
        new = "light" if theme.current().name == "dark" else "dark"
        apply_theme(new)
        config.set("theme", new)
        self._update_theme_icon()
        self._paint_nav_icons()
        self.logo_lbl.setPixmap(icons.logo_mark(30))
        bus.theme_changed.emit(new)

    def _update_theme_icon(self):
        dark = theme.current().name == "dark"
        self.theme_btn.setIcon(icons.icon("mdi6.white-balance-sunny" if dark else "mdi6.weather-night",
                                          theme.current().text))
        self.theme_btn.setIconSize(QSize(20, 20))

    # ------------------------------------------------------------ notificaciones
    def show_toast(self, kind: str, title: str, message: str):
        t = Toast(self, kind, title, message)
        t.show()
        self.toasts.append(t)
        t.destroyed.connect(lambda *_: self._forget_toast(t))
        self.relayout_toasts()
        if kind == "error":
            log.warning("UI error: %s — %s", title, message)

    def _forget_toast(self, t):
        if t in self.toasts:
            self.toasts.remove(t)

    def relayout_toasts(self):
        """Avisos apilados arriba a la derecha (no tapan los botones de acción)."""
        y = 78
        for t in [t for t in self.toasts if t.isVisible()]:
            t.adjustSize()
            t.move(self.width() - t.width() - 22, y)
            t.raise_()
            y += t.height() + 10

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self.relayout_toasts()

    # ------------------------------------------------------------ admin / inicio / cierre
    def _elevate(self):
        if confirm(self, "Reiniciar como administrador",
                   "eREDES se cerrará y se abrirá de nuevo con permisos de administrador (UAC)."):
            if relaunch_as_admin_windows():
                QApplication.quit()
            else:
                notify("error", "No se pudo elevar", "Windows rechazó la solicitud de administrador.")

    # ------------------------------------------------------------ actualizaciones
    def check_updates(self, manual: bool = False):
        from app.core import updater
        from app.core.tasks import run_task
        if getattr(self, "_upd_busy", False):
            return
        self._upd_busy = True
        if manual:
            self.update_btn.setText("  Buscando…")

        def work(_c):
            try:
                return updater.check()
            except updater.UpdateError as e:   # sin internet / sin versiones: no es un fallo de la app
                return str(e)

        def done(info):
            if isinstance(info, str):
                fail(info)
                return
            self._upd_busy = False
            self.update_btn.setText("  Actualizar")
            if info.available:
                self._mark_update(info)
                if manual:
                    self._offer_update(info)
                else:
                    notify("info", f"Nueva versión {version_label(info.latest)} disponible",
                           "Pulse Actualizar (abajo a la izquierda).")
            elif manual:
                notify("ok", "eREDES está actualizado", version_label(info.current))

        def fail(msg):
            self._upd_busy = False
            self.update_btn.setText("  Actualizar")
            if manual:
                notify("warning", "No se pudo buscar actualizaciones", msg)

        run_task(work, on_result=done, on_error=fail)

    def _mark_update(self, info):
        self._pending_update = info
        pal = theme.current()
        self.update_btn.setText(f"  {version_label(info.latest)}")
        self.update_btn.setToolTip(f"Actualizar a {version_label(info.latest)}")
        self.update_btn.setStyleSheet(f"#UpdateButton {{ background:{pal.primary}; color:white; border:none; }}")
        self.update_btn.setIcon(icons.icon("mdi6.download", "#FFFFFF"))
        try:
            self.update_btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.update_btn.clicked.connect(lambda: self._offer_update(info))

    def _offer_update(self, info):
        from app.core import updater
        if not updater.can_self_update():
            confirm(self, f"{version_label(info.latest)} disponible",
                    "Esta copia se ejecuta desde el código fuente. Para actualizar use la app instalada "
                    "(.exe / .app) o descargue la nueva versión de GitHub.", info.notes or info.page_url,
                    ok_text="Entendido")
            return
        if not confirm(self, f"Actualizar a {version_label(info.latest)}",
                       f"Versión actual: {version_label(info.current)}. Se descargará {info.asset_name} "
                       f"({info.asset_size / 1e6:.0f} MB) y eREDES se reiniciará.<br>"
                       "Tus drivers, datos e informes no se tocan.",
                       info.notes, ok_text="Actualizar ahora"):
            return
        from .widgets import UpdateDialog
        dlg = UpdateDialog(self, info)
        if dlg.exec() and dlg.ok:
            QApplication.quit()

    def _startup_checks(self):
        from app.network import ip_recovery
        pending = ip_recovery.pending_from_previous_run()
        if pending:
            txt = "\n".join(f"{s.interface}: {s.ip}/{s.mask}" for s in pending)
            if confirm(self, "Configuración de red pendiente de restaurar",
                       "En una sesión anterior se añadió una IP temporal que no se restauró. "
                       "¿Quitarla ahora y verificar la configuración original?", txt, ok_text="Restaurar"):
                for s in pending:
                    r = ip_recovery.remove_temporary_ip(s)
                    notify("ok" if r.ok else "error", r.message, r.detail)

    def closeEvent(self, e):  # noqa: N802
        from app.network import ip_recovery
        sessions = ip_recovery.active_sessions()
        if sessions:
            txt = "\n".join(f"{s.interface}: {s.ip}" for s in sessions)
            if not confirm(self, "Restaurar configuración de red",
                           "Hay IPs temporales activas. Se quitarán antes de salir para dejar la red como estaba.",
                           txt, ok_text="Restaurar y salir"):
                e.ignore()
                return
            for r in ip_recovery.restore_all():
                if not r.ok:
                    confirm(self, "Atención", r.message, r.detail, danger=True, ok_text="Entendido")
        cancel_all()
        super().closeEvent(e)


def apply_theme(name: str) -> None:
    pal = theme.set_current(name)
    app = QApplication.instance()
    app.setStyleSheet(theme.qss(pal, icons.ensure_check_image()))
