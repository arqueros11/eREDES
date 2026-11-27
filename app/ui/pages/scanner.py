"""Escáner de red: rango Desde/Hasta, IPs en uso y libres."""
from __future__ import annotations

import ipaddress
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                               QVBoxLayout)

from app.core import config
from app.core.tasks import run_task
from app.core.validation import ValidationError, parse_target_range, validate_ipv4

from .. import theme
from ..bus import bus, notify
from ..widgets import Card, TaskPanel, button, fill_table, info_dialog, make_table, selected_data
from .base import BasePage

FILTERS = ("Todas", "En uso", "Libres", "Impresoras")


def _load_net(ctx):
    from app.network.interfaces import network_summary
    return network_summary()


class ScannerPage(BasePage):
    key = "scanner"
    title = "Escáner"

    def __init__(self):
        super().__init__()
        # ---- barra de escaneo: Desde · Hasta · Escanear
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.ip_from = QLineEdit()
        self.ip_to = QLineEdit()
        for w, ph in ((self.ip_from, "192.168.1.1"), (self.ip_to, "192.168.1.254")):
            w.setPlaceholderText(ph)
            w.setMinimumHeight(46)
            w.setMinimumWidth(190)
            w.returnPressed.connect(self.start_scan)
        lf, lt = QLabel("Desde"), QLabel("Hasta")
        for lb in (lf, lt):
            lb.setObjectName("FormLabel")
        bar.addWidget(lf)
        bar.addWidget(self.ip_from, 2)
        bar.addWidget(lt)
        bar.addWidget(self.ip_to, 2)
        self.scan_btn = button("Escanear", "Primary", "mdi6.radar", self.start_scan)
        self.scan_btn.setMinimumHeight(46)
        self.scan_btn.setMinimumWidth(160)
        bar.addWidget(self.scan_btn)
        bar.addWidget(button("Mi red", "", "mdi6.lan", self._auto_range, "Rellenar con la red de este equipo"))
        bar.addStretch(1)
        bar.addWidget(button("", "Ghost", "mdi6.ethernet", self._show_interfaces, "Interfaces de red"))
        self.root.addLayout(bar)

        self.net_info = QLabel("")
        self.net_info.setObjectName("Muted")
        self.root.addWidget(self.net_info)
        self.task = TaskPanel()
        self.task.finished.connect(lambda: self.scan_btn.setEnabled(True))
        self.root.addWidget(self.task)

        # ---- filtros + tabla
        card = Card()
        fl = QHBoxLayout()
        fl.setSpacing(0)
        self.fgroup = QButtonGroup(self)
        self.fbtns: list[QPushButton] = []
        for i, name in enumerate(FILTERS):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(38)
            self.fgroup.addButton(b, i)
            self.fbtns.append(b)
            fl.addWidget(b)
        self.fbtns[0].setChecked(True)
        self.fgroup.idClicked.connect(lambda _i: self._render())
        self._style_filters()
        fl.addSpacing(16)
        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        fl.addWidget(self.summary)
        fl.addStretch(1)
        card.add(fl)
        self.table = make_table(["IP", "Estado", "Tipo", "Fabricante", "Nombre", "MAC", "Puertos"], stretch_col=2)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.doubleClicked.connect(lambda _i: self._primary_action())
        self.table.itemSelectionChanged.connect(self._update_actions)
        card.add(self.table, 1)
        acts = QHBoxLayout()
        acts.setSpacing(10)
        self.b_main = button("Nueva impresora con esta IP", "Primary", "mdi6.printer-pos", self._primary_action)
        self.b_diag = button("Diagnosticar", "", "mdi6.stethoscope", self._diagnose)
        self.b_web = button("Web", "", "mdi6.open-in-new", self._open_web)
        self.b_check = button("Confirmar libre", "", "mdi6.shield-check", self._check)
        self.b_copy = button("Copiar IP", "Ghost", "mdi6.content-copy", self._copy)
        for b in (self.b_main, self.b_diag, self.b_web, self.b_check, self.b_copy):
            b.setMinimumHeight(44)
            acts.addWidget(b)
        acts.addStretch(1)
        card.add(acts)
        self.root.addWidget(card, 1)
        self.hosts = []
        self.primary = None
        self.ns = None
        self._update_actions()
        bus.theme_changed.connect(lambda _t: self._style_filters())

    def _style_filters(self):
        p = theme.current()
        n = len(self.fbtns)
        for i, b in enumerate(self.fbtns):
            radius = ("border-top-left-radius:8px; border-bottom-left-radius:8px;" if i == 0 else "") + \
                     ("border-top-right-radius:8px; border-bottom-right-radius:8px;" if i == n - 1 else "")
            b.setStyleSheet(
                f"QPushButton {{ border:1px solid {p.border}; border-radius:0; {radius} padding:6px 18px;"
                f" background:{p.surface2}; font-weight:500; }}"
                f"QPushButton:checked {{ background:{p.primary}; color:white; border-color:{p.primary}; }}")

    # ------------------------------------------------------------ red local
    def first_show(self):
        run_task(_load_net, on_result=self._apply_net, on_error=lambda m: notify("error", "Red", m))
        self._load_last()

    def _apply_net(self, ns):
        self.ns = ns
        self.primary = ns.primary
        p = ns.primary
        if p and p.ipv4:
            self.net_info.setText(f"Este equipo: <b>{p.ipv4}</b> &nbsp;·&nbsp; Gateway: <b>{ns.gateway or '—'}</b>"
                                  f" &nbsp;·&nbsp; {p.kind}")
            if not self.ip_from.text().strip():
                self._auto_range()
        else:
            self.net_info.setText("Sin conexión de red")

    def _auto_range(self):
        p = self.primary
        if not (p and p.network):
            notify("warning", "Sin red", "No hay una interfaz con IPv4 activa.")
            return
        net = ipaddress.IPv4Network(p.network)
        if net.num_addresses > 1024:  # redes grandes: proponer el /24 del equipo
            net = ipaddress.IPv4Network(f"{p.ipv4}/24", strict=False)
        hosts = list(net.hosts())
        self.ip_from.setText(str(hosts[0]))
        self.ip_to.setText(str(hosts[-1]))

    def _show_interfaces(self):
        if not self.ns:
            return
        d = QDialog(self)
        d.setWindowTitle("Interfaces de red")
        d.resize(900, 360)
        v = QVBoxLayout(d)
        t = make_table(["Interfaz", "Tipo", "IPv4", "Máscara", "MAC", "Estado"], stretch_col=0)
        pal = theme.current()
        fill_table(t, [[i.display, i.kind, i.ipv4, i.netmask, i.mac, "Activa" if i.is_up else "Inactiva"]
                       for i in self.ns.interfaces],
                   colors={5: lambda s: pal.success if s == "Activa" else pal.muted})
        v.addWidget(t)
        v.addWidget(QLabel(f"Gateway: {self.ns.gateway or '—'}   ·   DNS: {', '.join(self.ns.dns) or '—'}"))
        d.exec()

    # ------------------------------------------------------------ escaneo
    def start_scan(self):
        a, b = self.ip_from.text().strip(), self.ip_to.text().strip()
        try:
            validate_ipv4(a, allow_network=True)
            targets = parse_target_range(f"{a}-{b}" if b else a)
        except ValidationError as e:
            notify("error", "Rango inválido", str(e))
            return
        from app.network.scanner import scan
        self.scan_btn.setEnabled(False)
        self.summary.setText("")
        self.task.run(scan, targets, list(config.get("scan_ports")), True, on_result=self._done)

    def _done(self, res):
        self.hosts = res["all"]
        used = sum(1 for h in self.hosts if h.status == "En uso")
        self._render()
        if not res["cancelled"]:
            notify("ok", "Escaneo terminado", f"{used} en uso · {res['free']} libres")
            bus.data_changed.emit("scan")

    def _load_last(self):
        from app.database.db import get_db
        from app.network.scanner import HostResult
        db = get_db()
        sc = db.one("SELECT * FROM scans ORDER BY id DESC LIMIT 1")
        if not sc:
            return
        hosts = []
        for r in db.query("SELECT * FROM scan_results WHERE scan_id=?", (sc["id"],)):
            h = HostResult(ip=r["ip"], alive=True, mac=r["mac"], vendor=r["vendor"], hostname=r["hostname"],
                           dev_type=r["dev_type"], status="En uso")
            h.open_ports = [int(x) for x in r["ports"].split(",") if x.strip().isdigit()]
            hosts.append(h)
        self.hosts = hosts
        self._render()

    def _render(self):
        pal = theme.current()
        f = self.fgroup.checkedId()
        counts = {
            0: len(self.hosts),
            1: sum(1 for h in self.hosts if h.status == "En uso"),
            2: sum(1 for h in self.hosts if h.status == "Libre"),
            3: sum(1 for h in self.hosts if "mpresora" in h.dev_type),
        }
        for i, b in enumerate(self.fbtns):
            b.setText(f"{FILTERS[i]}  {counts[i]}" if self.hosts else FILTERS[i])
        hosts = [h for h in self.hosts if f == 0 or (f == 1 and h.status == "En uso")
                 or (f == 2 and h.status == "Libre") or (f == 3 and "mpresora" in h.dev_type)]
        rows = [[h.ip, h.status, h.dev_type, h.vendor, h.hostname, h.mac, h.ports_text] for h in hosts]
        st_color = {"En uso": pal.primary, "Libre": pal.success, "Reservada": pal.warning, "Sin respuesta": pal.muted}

        def type_color(s):
            return pal.success if s.startswith("Impresora") else (pal.warning if s.startswith("Posible") else None)
        fill_table(self.table, rows, data=hosts, colors={1: lambda s: st_color.get(s), 2: type_color})
        self.table.sortItems(0, Qt.AscendingOrder)
        if any(h.status == "Sin respuesta" for h in hosts):
            self.summary.setText("Fuera de tu red no se puede confirmar si una IP está libre")
        elif counts[2]:
            self.summary.setText("Libre = no respondió a ping, TCP ni ARP")
        else:
            self.summary.setText("")
        self._update_actions()

    # ------------------------------------------------------------ acciones
    def _sel(self):
        return selected_data(self.table)

    def _update_actions(self):
        h = self._sel()
        used = bool(h and h.status == "En uso")
        free = bool(h and h.status in ("Libre", "Sin respuesta"))
        self.b_main.setEnabled(bool(h))
        self.b_main.setText("Asignar a una impresora" if free else "Nueva impresora con esta IP")
        self.b_diag.setEnabled(used)
        self.b_web.setEnabled(used and any(p in h.open_ports for p in (80, 443, 8080)))
        self.b_check.setEnabled(free)
        self.b_copy.setEnabled(bool(h))

    def _primary_action(self):
        h = self._sel()
        if not h:
            return
        if h.status in ("Libre", "Sin respuesta"):
            bus.assign_free_ip.emit(h.ip)  # Recuperar IP con esta IP como nueva
        else:
            port = next((p for p in (9100, 9101, 9102, 515, 631) if p in h.open_ports), 9100)
            bus.new_printer.emit(h.ip, port)

    def _diagnose(self):
        h = self._sel()
        if h:
            port = next((p for p in (9100, 9101, 9102, 515, 631) if p in h.open_ports), 9100)
            bus.diagnose_printer.emit(h.ip, port, "")

    def _open_web(self):
        h = self._sel()
        if h:
            scheme = "https" if 443 in h.open_ports and 80 not in h.open_ports else "http"
            port = ":8080" if (8080 in h.open_ports and 80 not in h.open_ports and 443 not in h.open_ports) else ""
            webbrowser.open(f"{scheme}://{h.ip}{port}/")

    def _copy(self):
        h = self._sel()
        if h:
            QGuiApplication.clipboard().setText(h.ip)
            notify("ok", "IP copiada", h.ip)

    def _check(self):
        """Comprobación a fondo de una IP libre antes de asignarla."""
        h = self._sel()
        if not h:
            return
        from app.network.scanner import check_ip_availability

        def done(r):
            kind = {"EN_USO": "error", "NO_CONFIRMADA": "ok", "INDETERMINADA": "warning"}[r.verdict]
            title = {"EN_USO": "En uso", "NO_CONFIRMADA": "Libre", "INDETERMINADA": "No se puede confirmar"}[r.verdict]
            info_dialog(self, f"{r.ip}: {title}", r.title, "\n".join(r.evidence + [""] + r.warnings), kind)
        self.task.run(lambda _c: check_ip_availability(h.ip), on_result=done, cancellable=False)

    def _menu(self, pos):
        h = self._sel()
        if not h:
            return
        m = QMenu(self)
        m.addAction(self.b_main.text(), self._primary_action)
        if h.status == "En uso":
            m.addAction("Diagnosticar", self._diagnose)
            m.addAction("Abrir web", self._open_web)
        else:
            m.addAction("Confirmar libre", self._check)
        m.addAction("Copiar IP", self._copy)
        m.exec(self.table.viewport().mapToGlobal(pos))
