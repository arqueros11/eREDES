"""Recuperar IP en 2 pasos a pantalla completa:
  ① Buscar   — Bonjour (sin tocar la red) o IP temporal + escaneo de la subred de fábrica.
  ② Cambiar IP — perfil del fabricante, aplicar y verificar.
"""
from __future__ import annotations

import ipaddress

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from app.core import config
from app.core.tasks import run_task
from app.core.validation import ValidationError, validate_ipv4, validate_netmask

from .. import theme
from ..bus import bus, notify
from ..widgets import sort_table, Badge, Banner, Card, TaskPanel, button, confirm, fill_table, make_table, selected_data
from .base import BasePage


def _lbl(t):
    lb = QLabel(t)
    lb.setObjectName("FormLabel")
    return lb


def _big(w, h=44):
    w.setMinimumHeight(h)
    return w


class IpRecoveryPage(BasePage):
    key = "recovery"
    title = "Recuperar IP"

    def __init__(self):
        super().__init__()
        # ---- cabecera: pasos + estado de la red
        head = QHBoxLayout()
        head.setSpacing(0)
        self.steps = QButtonGroup(self)
        self.step_btns: list[QPushButton] = []
        for i, name in enumerate(("①  Buscar impresora", "②  Cambiar IP")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(42)
            b.setMinimumWidth(210)
            self.steps.addButton(b, i)
            self.step_btns.append(b)
            head.addWidget(b)
        self.step_btns[0].setChecked(True)
        self.steps.idClicked.connect(self.go)
        head.addStretch(1)
        self.state = Badge("Red original", theme.current().success)
        head.addWidget(self.state)
        self.root.addLayout(head)
        self._style_steps()

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_find())
        self.stack.addWidget(self._build_change())
        self.root.addWidget(self.stack, 1)
        self.task = TaskPanel()
        self.root.addWidget(self.task)
        self.session = None
        self.rows = []
        bus.theme_changed.connect(lambda _t: self._style_steps())

    # ------------------------------------------------------------ construcción
    def _build_find(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)

        c = Card()
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        bonjour = _big(button("Buscar sin cambiar la red (Bonjour)", "Primary", "mdi6.magnify", self.mdns))
        g.addWidget(bonjour, 0, 0, 1, 6)
        self.subnet = QComboBox()
        self.subnet.setEditable(True)
        self.subnet.addItems(config.get("factory_subnets"))
        self.subnet.currentTextChanged.connect(self._suggest)
        self.iface = QComboBox()
        self.temp_ip = QLineEdit()
        for x in (self.subnet, self.iface, self.temp_ip):
            _big(x)
        g.addWidget(_lbl("Subred de fábrica"), 1, 0)
        g.addWidget(self.subnet, 1, 1)
        g.addWidget(_lbl("Interfaz"), 1, 2)
        g.addWidget(self.iface, 1, 3, 1, 3)
        g.addWidget(_lbl("IP temporal"), 2, 0)
        g.addWidget(self.temp_ip, 2, 1)
        self.add_btn = _big(button("Activar y escanear", "Primary", "mdi6.radar", self.activate_or_scan))
        self.rm_btn = _big(button("Restaurar red", "Danger", "mdi6.restore", self.restore))
        g.addWidget(self.add_btn, 2, 2, 1, 2)
        g.addWidget(self.rm_btn, 2, 4, 1, 2)
        g.setColumnStretch(1, 3)
        g.setColumnStretch(3, 2)
        c.add(g)
        v.addWidget(c)

        t = Card("Encontradas", icon_name="mdi6.devices")
        self.table = make_table(["IP", "Nombre / modelo", "Origen", "Puertos", "Fabricante"], stretch_col=1)
        self.table.doubleClicked.connect(lambda _i: self._to_change())
        t.add(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(_big(button("Cambiar IP de la seleccionada", "Primary", "mdi6.arrow-right", self._to_change)))
        row.addStretch(1)
        t.add(row)
        v.addWidget(t, 1)
        return w

    def _build_change(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(14)
        c = Card()
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(12)
        self.cur_ip = _big(QLineEdit())
        self.cur_ip.setPlaceholderText("IP actual de la impresora")
        self.new_ip = _big(QLineEdit())
        self.new_ip.setPlaceholderText("192.168.1.240")
        self.mask = _big(QLineEdit("255.255.255.0"))
        self.gw = _big(QLineEdit())
        self.gw.setPlaceholderText("192.168.1.1")
        self.profile = _big(QComboBox())
        g.addWidget(_lbl("IP actual"), 0, 0)
        g.addWidget(self.cur_ip, 0, 1)
        g.addWidget(_lbl("Nueva IP"), 0, 2)
        g.addWidget(self.new_ip, 0, 3)
        g.addWidget(_lbl("Máscara"), 1, 0)
        g.addWidget(self.mask, 1, 1)
        g.addWidget(_lbl("Gateway"), 1, 2)
        g.addWidget(self.gw, 1, 3)
        g.addWidget(_lbl("Perfil"), 2, 0)
        g.addWidget(self.profile, 2, 1, 1, 3)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        c.add(g)
        r = QHBoxLayout()
        r.setSpacing(10)
        r.addWidget(_big(button("Aplicar", "Primary", "mdi6.check-circle", self.apply), 48))
        r.addWidget(_big(button("Verificar", "", "mdi6.lan-connect", self.verify), 48))
        r.addWidget(_big(button("Crear impresora con la nueva IP", "", "mdi6.printer-pos", self._create), 48))
        r.addStretch(1)
        c.add(r)
        self.banner = Banner("", "info")
        c.add(self.banner)
        v.addWidget(c)
        v.addStretch(1)
        return w

    def _style_steps(self):
        p = theme.current()
        n = len(self.step_btns)
        for i, b in enumerate(self.step_btns):
            radius = ("border-top-left-radius:9px; border-bottom-left-radius:9px;" if i == 0 else "") + \
                     ("border-top-right-radius:9px; border-bottom-right-radius:9px;" if i == n - 1 else "")
            b.setStyleSheet(
                f"QPushButton {{ border:1px solid {p.border}; border-radius:0; {radius} padding:6px 20px;"
                f" background:{p.surface2}; font-weight:600; }}"
                f"QPushButton:checked {{ background:{p.primary}; color:white; border-color:{p.primary}; }}")

    def go(self, i: int):
        self.stack.setCurrentIndex(i)
        self.step_btns[i].setChecked(True)

    # ------------------------------------------------------------ ciclo de vida
    def first_show(self):
        from app.network.interfaces import get_interfaces

        def fill(ifaces):
            for i in ifaces:
                if i.kind in ("Ethernet", "Wi-Fi"):
                    self.iface.addItem(f"{i.display}" + (f" · {i.ipv4}" if i.ipv4 else ""), i.name)
        run_task(lambda _c: get_interfaces(), on_result=fill)
        self._suggest()
        self._load_profiles()
        self._update_state()

    def on_show(self):
        super().on_show()
        self._update_state()

    def preset_target(self, ip: str):
        self.cur_ip.setText(ip)
        self.go(1)

    def preset_new_ip(self, ip: str):
        """IP libre elegida en el escáner: se propone como nueva IP de la impresora."""
        self.new_ip.setText(ip)
        from app.network.interfaces import default_gateway
        gw, _ = default_gateway()
        if gw and not self.gw.text().strip():
            try:
                if ipaddress.IPv4Address(gw) in ipaddress.IPv4Network(
                        f"{ip}/{self.mask.text() or '255.255.255.0'}", strict=False):
                    self.gw.setText(gw)
            except ValueError:
                pass
        self.banner.set(f"Nueva IP: {ip}. Ahora busque la impresora en el paso ①.", "info")
        self.go(0)

    def _load_profiles(self):
        from app.network.ip_recovery import load_profiles
        self.profile.clear()
        for p in sorted(load_profiles(), key=lambda x: (x.get("manufacturer") != "*", x.get("enabled") is False)):
            label = p.get("name", p.get("id"))
            if p.get("enabled") is False:
                label += "  (incompleto)"
            self.profile.addItem(label, p)

    def _suggest(self):
        from app.network.ip_recovery import suggest_temp_ip
        try:
            self.temp_ip.setText(suggest_temp_ip(self.subnet.currentText()))
        except (ValueError, ValidationError):
            self.temp_ip.clear()

    def _update_state(self):
        from app.network.ip_recovery import active_sessions
        ss = active_sessions()
        self.session = ss[-1] if ss else None
        pal = theme.current()
        if self.session:
            self.state.set(f"IP temporal activa: {self.session.ip}", pal.warning)
            self.add_btn.setText("Escanear de nuevo")
        else:
            self.state.set("Red original", pal.success)
            self.add_btn.setText("Activar y escanear")
        self.rm_btn.setEnabled(bool(ss))

    # ------------------------------------------------------------ ① buscar
    def mdns(self):
        from app.network import mdns

        def done(devs):
            for d in devs:
                model = d.txt.get("ty") or d.txt.get("product", "").strip("()") or ", ".join(sorted(d.names))[:60]
                self._add_row(d.ip, model, "Bonjour", ", ".join(map(str, sorted(d.ports))), "")
            self._render()
            notify("ok" if devs else "info", f"{len(devs)} encontradas por Bonjour")
        self.task.run(lambda ctx: (ctx.progress(30, "Escuchando respuestas Bonjour…"), mdns.discover(4))[1],
                      on_result=done)

    def _add_row(self, ip, name, origin, ports, vendor):
        self.rows = [r for r in self.rows if r[0] != ip]
        self.rows.append([ip, name, origin, ports, vendor])

    def _render(self):
        fill_table(self.table, self.rows, data=[r[0] for r in self.rows])
        sort_table(self.table, 0)

    def _to_change(self):
        ip = selected_data(self.table)
        if not ip:
            notify("info", "Seleccione una impresora de la tabla")
            return
        self.cur_ip.setText(ip)
        self.banner.set("", "info")
        self.go(1)
        (self.new_ip if not self.new_ip.text().strip() else self.profile).setFocus()

    def activate_or_scan(self):
        if self.session:
            self.scan_subnet()
        else:
            self.add_temp()

    def add_temp(self):
        try:
            net = ipaddress.IPv4Network(self.subnet.currentText().strip(), strict=False)
            ip = validate_ipv4(self.temp_ip.text())
            if ipaddress.IPv4Address(ip) not in net:
                raise ValidationError(f"{ip} no pertenece a {net}")
            mask = validate_netmask(str(net.netmask))
        except (ValueError, ValidationError) as e:
            notify("error", "Datos inválidos", str(e))
            return
        iface = self.iface.currentData()
        if not iface:
            notify("error", "Seleccione una interfaz")
            return
        if not confirm(self, "Activar IP temporal", f"Se añadirá {ip}/{net.prefixlen} a {iface}.<br>"
                       "Tu IP y gateway no cambian. Se quita con Restaurar red o al salir.",
                       ok_text="Activar"):
            return
        from app.network.ip_recovery import add_temporary_ip

        def done(res):
            r, _sess = res
            notify("ok" if r.ok else "error", r.message, r.detail)
            self._update_state()
            if r.ok:
                self.scan_subnet()
        self.task.run(lambda _c: add_temporary_ip(iface, ip, mask), on_result=done, cancellable=False)

    def scan_subnet(self):
        from app.core.validation import parse_target_range
        from app.network.scanner import scan
        try:
            targets = parse_target_range(self.subnet.currentText().strip())
        except ValidationError as e:
            notify("error", "Subred inválida", str(e))
            return

        def done(res):
            for h in res["hosts"]:
                if h.is_self:
                    continue
                self._add_row(h.ip, h.hostname or h.dev_type, "Escaneo", h.ports_text, h.vendor or h.mac)
            self._render()
            notify("ok", f"{len(res['hosts'])} equipos en {self.subnet.currentText()}")
        self.task.run(scan, targets, list(config.get("printer_ports")) + [80, 443, 161], True, on_result=done)

    def restore(self):
        from app.network.ip_recovery import active_sessions, remove_temporary_ip

        def work(_c):
            return [remove_temporary_ip(s) for s in active_sessions()]

        def done(results):
            for r in results:
                notify("ok" if r.ok else "error", r.message, r.detail)
            self._update_state()
        self.task.run(work, on_result=done, cancellable=False)

    # ------------------------------------------------------------ ② cambiar IP
    def apply(self):
        from app.network.ip_recovery import apply_profile
        p = self.profile.currentData()
        if not p:
            return
        cur, new, mask, gw = self.cur_ip.text(), self.new_ip.text(), self.mask.text(), self.gw.text().strip()
        try:
            validate_ipv4(cur)
            validate_ipv4(new)
        except ValidationError as e:
            self.banner.set(str(e), "error")
            return
        if p.get("method") != "web" and not confirm(
                self, "Cambiar IP de la impresora", f"{cur} → {new}", "\n".join(p.get("instructions", [])),
                ok_text="Aplicar"):
            return
        if p.get("method") == "web":
            self.banner.set("\n".join(p.get("instructions", [])), "info")

        def done(r):
            self.banner.set(r.message + (f"\n{r.detail}" if r.detail else ""), "ok" if r.ok else "error")
        run_task(lambda _c: apply_profile(p, cur, new, mask, gw), on_result=done,
                 on_error=lambda m: self.banner.set(m, "error"))

    def verify(self):
        from app.network.ip_recovery import verify_new_ip
        new = self.new_ip.text().strip()
        if not new:
            self.banner.set("Escriba la nueva IP.", "warning")
            return
        self.banner.set(f"Verificando {new}…", "info")
        run_task(lambda _c: verify_new_ip(new), on_result=lambda r: self.banner.set(
            r.message + "\n" + r.detail, "ok" if r.ok else "error"), on_error=lambda m: self.banner.set(m, "error"))

    def _create(self):
        ip = self.new_ip.text().strip() or self.cur_ip.text().strip()
        if ip:
            bus.new_printer.emit(ip, 9100)
