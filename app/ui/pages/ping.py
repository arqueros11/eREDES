"""Ping continuo: ver en vivo qué IPs responden y avisar cuando una empieza (o deja) de responder."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QListWidget

from app.core.tasks import run_task
from app.core.validation import ValidationError, parse_target_range

from .. import theme
from ..bus import notify
from ..widgets import Card, button, fill_table, make_table
from .base import BasePage


def _printers(ctx):
    from app.platform import printer_backend
    return [(p.name, p.ip) for p in printer_backend().list_printers() if p.ip]


def _ago(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h {s % 3600 // 60} min"


class PingPage(BasePage):
    key = "ping"
    title = "Ping"

    def __init__(self):
        super().__init__()
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.target = QLineEdit()
        self.target.setPlaceholderText("192.168.1.250   ·   192.168.1.200-210   ·   varias separadas por coma")
        self.target.setMinimumHeight(46)
        self.target.returnPressed.connect(self.start)
        bar.addWidget(self.target, 1)
        self.go_btn = button("Iniciar", "Primary", "mdi6.play", self.toggle)
        self.go_btn.setMinimumHeight(46)
        self.go_btn.setMinimumWidth(140)
        bar.addWidget(self.go_btn)
        bar.addWidget(button("Mis impresoras", "", "mdi6.printer-outline", self.load_printers,
                             "Hacer ping a todas las impresoras instaladas en este equipo"))
        self.root.addLayout(bar)

        # resumen grande
        self.big = QLabel("Escriba una IP y pulse Iniciar")
        self.big.setAlignment(Qt.AlignCenter)
        self.big.setMinimumHeight(86)
        self._big_style(None)
        self.root.addWidget(self.big)

        card = Card()
        self.table = make_table(["IP", "Nombre", "Estado", "Tiempo", "Promedio", "Respuestas", "Perdidos", "Hace"])
        self.table.horizontalHeader().setStretchLastSection(True)
        for c, w in enumerate((150, 150, 140, 90, 100, 120, 100)):
            self.table.setColumnWidth(c, w)
        card.add(self.table, 1)
        self.root.addWidget(card, 1)

        log_card = Card("Cambios", icon_name="mdi6.history")
        self.log = QListWidget()
        self.log.setMaximumHeight(130)
        log_card.add(self.log)
        self.root.addWidget(log_card)

        self.states: dict = {}
        self.names: dict[str, str] = {}
        self.task = None

    # ------------------------------------------------------------ control
    def preset(self, ips: list, names: dict | None = None):
        if names:
            self.names.update(names)
        self.target.setText(", ".join(ips))
        self.stop()
        self.start()

    def load_printers(self):
        def done(items):
            if not items:
                notify("info", "No hay impresoras de red instaladas")
                return
            self.names.update({ip: n for n, ip in items})
            seen = []
            for _n, ip in items:
                if ip not in seen:
                    seen.append(ip)
            self.preset(seen)
        run_task(_printers, on_result=done, on_error=lambda m: notify("error", "Impresoras", m))

    def toggle(self):
        if self.task:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.task:
            return
        from app.network.live_ping import MAX_IPS, PingState, ping_loop
        try:
            ips = parse_target_range(self.target.text(), max_hosts=MAX_IPS)
        except ValidationError as e:
            notify("error", "IP inválida", str(e))
            return
        self.states = {ip: PingState(ip, self.names.get(ip, "")) for ip in ips}
        self.log.clear()
        self._add_log(f"Inicio: {len(ips)} IP" + ("s" if len(ips) != 1 else ""))
        self.task = run_task(ping_loop, ips, on_partial=self._round, on_finished=self._stopped,
                             on_error=lambda m: notify("error", "Ping", m))
        self.go_btn.setText("Detener")
        self.go_btn.setIcon(self._icon("mdi6.stop"))
        self._render()

    def stop(self):
        if self.task:
            self.task.cancel()

    def _stopped(self):
        self.task = None
        self.go_btn.setText("Iniciar")
        self.go_btn.setIcon(self._icon("mdi6.play"))
        if self.states:
            self._add_log("Detenido")

    def _icon(self, name):
        from .. import icons
        return icons.icon(name, "#FFFFFF")

    # ------------------------------------------------------------ datos
    def _round(self, data):
        replies = data["replies"]
        changed = False
        for ip, st in self.states.items():
            ev = st.update(replies.get(ip))
            if ev:
                changed = True
                who = f"{ip}" + (f" ({st.name})" if st.name else "")
                if ev == "up":
                    self._add_log(f"✓ {who} empezó a responder")
                    notify("ok", f"{who} responde", f"{st.last_rtt:.0f} ms" if st.last_rtt is not None else "")
                else:
                    self._add_log(f"✗ {who} dejó de responder")
                    notify("warning", f"{who} no responde", "")
        if changed:
            QApplication.beep()
        self._render()

    def _render(self):
        pal = theme.current()
        rows, data = [], []
        for st in self.states.values():
            estado = "—" if st.up is None else ("Responde" if st.up else "Sin respuesta")
            rows.append([st.ip, st.name, estado,
                         f"{st.last_rtt:.0f} ms" if (st.up and st.last_rtt is not None) else "",
                         f"{st.avg_rtt:.0f} ms" if st.avg_rtt is not None else "",
                         f"{st.received}/{st.sent}", f"{st.loss_pct:.0f} %" if st.sent else "",
                         _ago(st.since) if st.sent else ""])
            data.append(st)
        fill_table(self.table, rows, data=data, autosize=False,
                   colors={2: lambda s: pal.success if s == "Responde" else (pal.danger if s == "Sin respuesta"
                                                                            else None)})
        self._summary()

    def _summary(self):
        sts = list(self.states.values())
        if not sts or all(s.up is None for s in sts):
            self.big.setText("Haciendo ping…" if self.task else "Escriba una IP y pulse Iniciar")
            self._big_style(None)
            return
        if len(sts) == 1:
            s = sts[0]
            if s.up:
                self.big.setText(f"{s.ip}   RESPONDE   {s.last_rtt:.0f} ms")
            else:
                self.big.setText(f"{s.ip}   SIN RESPUESTA   ·   {_ago(s.since)}")
            self._big_style(bool(s.up))
            return
        up = sum(1 for s in sts if s.up)
        self.big.setText(f"{up} de {len(sts)} responden")
        self._big_style(True if up == len(sts) else (False if up == 0 else None), partial=0 < up < len(sts))

    def _big_style(self, ok: bool | None, partial: bool = False):
        p = theme.current()
        color = p.warning if partial else (p.muted if ok is None else (p.success if ok else p.danger))
        self.big.setStyleSheet(f"QLabel {{ font-size:30px; font-weight:700; color:{color};"
                               f" border:2px solid {color}; border-radius:14px; background:{p.surface2}; }}")

    def _add_log(self, text: str):
        self.log.insertItem(0, f"{time.strftime('%H:%M:%S')}   {text}")
        while self.log.count() > 300:
            self.log.takeItem(self.log.count() - 1)
