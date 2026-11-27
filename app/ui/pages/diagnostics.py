"""Diagnóstico de impresoras y de conectividad."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QSpinBox

from app.core import config
from app.core.tasks import run_task
from app.core.validation import ValidationError, validate_ipv4

from .. import theme
from ..widgets import Badge, Card, TaskPanel, button, fill_table, make_table
from .base import BasePage


def _queues(ctx):
    from app.platform import printer_backend
    return printer_backend().list_printers()


class DiagnosticsPage(BasePage):
    key = "diagnostics"
    title = "Diagnóstico"

    def __init__(self):
        super().__init__()
        top = QHBoxLayout()
        top.setSpacing(10)
        self.queue = QComboBox()
        self.queue.setMinimumWidth(220)
        self.queue.currentIndexChanged.connect(self._queue_picked)
        self.ip = QLineEdit()
        self.ip.setPlaceholderText("IP")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(9100)
        self.escpos = QCheckBox("ESC/POS")
        self.escpos.setChecked(bool(config.get("escpos_status_enabled")))
        self.escpos.setToolTip("Lee el estado en tiempo real (papel, tapa, cortador) en impresoras térmicas POS")
        self.snmp = QCheckBox("SNMP")
        self.snmp.setChecked(True)
        run_b = button("Diagnosticar", "Primary", "mdi6.stethoscope", self.run)
        run_b.setMinimumHeight(44)
        for w in (self.queue, self.ip, self.port, self.escpos, self.snmp):
            top.addWidget(w)
        top.addWidget(run_b)
        top.addStretch(1)
        net_b = button("Probar internet", "", "mdi6.web", self.run_net)
        net_b.setMinimumHeight(44)
        top.addWidget(net_b)
        self.root.addLayout(top)

        card = Card()
        head = QHBoxLayout()
        self.overall = Badge("", theme.current().muted)
        self.overall.hide()
        head.addWidget(self.overall)
        head.addStretch(1)
        card.add(head)
        self.table = make_table(["Grupo", "Comprobación", "Estado", "Detalle"], stretch_col=3)
        card.add(self.table, 1)
        self.root.addWidget(card, 1)
        self.task = TaskPanel()
        self.root.addWidget(self.task)
        self.queues = []

    def first_show(self):
        self.queue.addItem("Sin cola (solo IP)", None)
        run_task(_queues, on_result=self._fill_queues)

    def _fill_queues(self, printers):
        self.queues = printers
        for p in printers:
            self.queue.addItem(p.name, p)

    def _queue_picked(self):
        p = self.queue.currentData()
        if p and p.ip:
            self.ip.setText(p.ip)
            self.port.setValue(p.port or 9100)

    def preset(self, ip: str, port: int, queue: str):
        self.ip.setText(ip)
        self.port.setValue(port or 9100)
        if queue:
            i = self.queue.findText(queue)
            if i < 0:
                self.queue.addItem(queue, None)
                i = self.queue.count() - 1
            self.queue.setCurrentIndex(i)
            self.ip.setText(ip)
        if ip:
            self.run()

    def run(self):
        try:
            ip = validate_ipv4(self.ip.text())
        except ValidationError as e:
            from ..bus import notify
            notify("error", "IP inválida", str(e))
            return
        from app.diagnostics.printer_diag import diagnose
        q = self.queue.currentText() if self.queue.currentIndex() > 0 else None
        self.task.run(diagnose, ip, self.port.value(), q, self.escpos.isChecked(), self.snmp.isChecked(),
                      on_result=self._show)

    def _show(self, res):
        from app.diagnostics.printer_diag import LABELS
        pal = theme.current()
        colors = {"OK": pal.success, "ERROR": pal.danger, "DESCONECTADO": pal.danger, "NO_DISPONIBLE": pal.muted,
                  "NO_COMPATIBLE": pal.warning, "INDETERMINADO": pal.warning}
        rows = [[c.group, c.name, LABELS[c.status], c.detail] for c in res["checks"]]
        inv = {v: k for k, v in LABELS.items()}
        fill_table(self.table, rows, colors={2: lambda s: colors.get(inv.get(s, ""), None)})
        ov = res["overall"]
        self.overall.set(LABELS[ov].upper(), colors.get(ov, pal.muted))
        self.overall.show()

    def run_net(self):
        from app.diagnostics.connectivity import run_connectivity

        def done(checks):
            pal = theme.current()
            rows = [["Internet", c.name, "Correcto" if c.ok else ("Error" if c.ok is False else "No se pudo determinar"),
                     c.detail] for c in checks]
            fill_table(self.table, rows, colors={2: lambda s: pal.success if s == "Correcto" else (
                pal.danger if s == "Error" else pal.warning)})
            ok = all(c.ok is not False for c in checks)
            self.overall.set("CONECTIVIDAD CORRECTA" if ok else "HAY PROBLEMAS", pal.success if ok else pal.danger)
            self.overall.show()
        self.task.run(run_connectivity, on_result=done)
