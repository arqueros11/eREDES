"""Ajustes: tema, puertos, subredes de fábrica, datos del técnico, copias de seguridad."""
from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit,
                               QSpinBox, QVBoxLayout)

from app.core import config, paths
from app.core.validation import ValidationError, parse_port_list
from app.database.db import get_db

from ..bus import bus, notify
from ..widgets import Card, button, confirm
from .base import BasePage


class SettingsPage(BasePage):
    key = "settings"
    title = "Ajustes"

    def __init__(self):
        super().__init__()
        cols = QHBoxLayout()
        cols.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)

        gen = Card("General", icon_name="mdi6.cog-outline")
        f = QFormLayout()
        f.setVerticalSpacing(10)
        self.theme = QComboBox()
        self.theme.addItems(["Claro", "Oscuro"])
        self.theme.setCurrentIndex(0 if config.get("theme") == "light" else 1)
        self.tech = QLineEdit(config.get("technician") or "")
        self.company = QLineEdit(config.get("company") or "")
        f.addRow("Apariencia", self.theme)
        f.addRow("Técnico", self.tech)
        f.addRow("Empresa", self.company)
        gen.add(f)
        left.addWidget(gen)

        net = Card("Red", icon_name="mdi6.lan")
        f2 = QFormLayout()
        f2.setVerticalSpacing(10)
        self.ports = QLineEdit(", ".join(map(str, config.get("scan_ports"))))
        self.pports = QLineEdit(", ".join(map(str, config.get("printer_ports"))))
        self.ping = QSpinBox()
        self.ping.setRange(100, 5000)
        self.ping.setSuffix(" ms")
        self.ping.setValue(int(config.get("ping_timeout_ms")))
        self.tcp = QSpinBox()
        self.tcp.setRange(100, 5000)
        self.tcp.setSuffix(" ms")
        self.tcp.setValue(int(config.get("tcp_timeout_ms")))
        self.snmp = QLineEdit(config.get("snmp_community") or "public")
        self.dur = QSpinBox()
        self.dur.setRange(4, 30)
        self.dur.setSuffix(" s")
        self.dur.setValue(int(config.get("speedtest_duration_s")))
        f2.addRow("Puertos escáner", self.ports)
        f2.addRow("Puertos impresora", self.pports)
        f2.addRow("Timeout ping", self.ping)
        f2.addRow("Timeout TCP", self.tcp)
        f2.addRow("Comunidad SNMP", self.snmp)
        f2.addRow("Duración speed test", self.dur)
        net.add(f2)
        left.addWidget(net)
        save = button("Guardar", "Primary", "mdi6.content-save-outline", self.save)
        save.setMinimumHeight(46)
        left.addWidget(save)
        left.addStretch(1)
        cols.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(14)
        fs = Card("Subredes de fábrica", "Una por línea. Se usan en Recuperar IP.", "mdi6.factory")
        self.subnets = QPlainTextEdit("\n".join(config.get("factory_subnets")))
        self.subnets.setMinimumHeight(150)
        fs.add(self.subnets)
        right.addWidget(fs)

        upd = Card("Actualizaciones", icon_name="mdi6.download")
        fu = QFormLayout()
        fu.setVerticalSpacing(10)
        self.repo = QLineEdit(config.get("github_repo") or "")
        self.repo.setPlaceholderText("usuario/repositorio")
        self.token = QLineEdit(config.get("github_token") or "")
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("Solo si el repositorio es privado")
        self.auto_upd = QCheckBox("Buscar al abrir la app")
        self.auto_upd.setChecked(bool(config.get("auto_check_updates")))
        fu.addRow("GitHub", self.repo)
        fu.addRow("Token", self.token)
        fu.addRow("", self.auto_upd)
        upd.add(fu)
        ub = button("Buscar actualizaciones", "", "mdi6.refresh", self._check_updates)
        ub.setMinimumHeight(42)
        upd.add(ub)
        right.addWidget(upd)

        data = Card("Datos", icon_name="mdi6.backup-restore")
        for text, ic, fn in (("Copia de seguridad", "mdi6.content-save-outline", self.backup),
                             ("Restaurar copia", "mdi6.backup-restore", self.restore),
                             ("Abrir carpeta de eREDES", "mdi6.folder-open-outline",
                              lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.home_dir())))),
                             ("Abrir logs", "mdi6.text-box-search-outline",
                              lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.logs_dir()))))):
            b = button(text, "", ic, fn)
            b.setMinimumHeight(42)
            data.add(b)
        right.addWidget(data)
        right.addStretch(1)
        cols.addLayout(right, 1)
        self.root.addLayout(cols, 1)

    def save(self):
        import ipaddress
        try:
            ports = parse_port_list(self.ports.text())
            pports = parse_port_list(self.pports.text())
            subnets = []
            for line in self.subnets.toPlainText().splitlines():
                if line.strip():
                    subnets.append(str(ipaddress.IPv4Network(line.strip(), strict=False)))
        except (ValidationError, ValueError) as e:
            notify("error", "Valor inválido", str(e))
            return
        config.set("scan_ports", ports)
        config.set("printer_ports", pports)
        config.set("ping_timeout_ms", self.ping.value())
        config.set("tcp_timeout_ms", self.tcp.value())
        config.set("snmp_community", self.snmp.text().strip() or "public")
        config.set("speedtest_duration_s", self.dur.value())
        config.set("factory_subnets", subnets)
        config.set("technician", self.tech.text().strip())
        config.set("company", self.company.text().strip())
        repo = self.repo.text().strip().strip("/").removeprefix("https://github.com/")
        config.set("github_repo", repo)
        config.set("github_token", self.token.text().strip())
        config.set("auto_check_updates", self.auto_upd.isChecked())
        new_theme = "light" if self.theme.currentIndex() == 0 else "dark"
        if new_theme != config.get("theme"):
            win = self.window()
            if hasattr(win, "toggle_theme"):
                win.toggle_theme()
        notify("ok", "Ajustes guardados")
        bus.data_changed.emit("settings")

    def _check_updates(self):
        self.save()
        win = self.window()
        if hasattr(win, "check_updates"):
            win.check_updates(manual=True)

    def backup(self):
        p = get_db().backup()
        notify("ok", "Copia creada", p.name)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))

    def restore(self):
        f, _ = QFileDialog.getOpenFileName(self, "Restaurar copia", str(paths.backups_dir()), "eREDES (*.db)")
        if f and confirm(self, "Restaurar copia", "Se reemplazarán los datos actuales (antes se hace una copia).",
                         danger=True, ok_text="Restaurar"):
            try:
                get_db().restore(f)
                notify("ok", "Copia restaurada", "Reinicie eREDES para recargar todo.")
            except ValueError as e:
                notify("error", "No se pudo restaurar", str(e))
