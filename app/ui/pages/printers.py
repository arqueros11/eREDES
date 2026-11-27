"""Impresoras instaladas en el sistema operativo: alta, edición, pruebas y colas."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QMenu

from app.core.tasks import run_task

from .. import theme
from ..bus import bus, notify
from ..dialogs import PrinterDialog, run_test_print
from ..widgets import (Card, TaskPanel, button, confirm, fill_table, filter_table, info_dialog, make_table,
                       selected_data)
from .base import BasePage


def _load(ctx):
    from app.printers.manager import PrinterManager
    m = PrinterManager()
    printers = m.system_printers()
    for p in printers:
        m.adopt(p)
    return printers


class PrintersPage(BasePage):
    key = "printers"
    title = "Impresoras"

    def __init__(self):
        super().__init__()
        bar = QHBoxLayout()
        bar.setSpacing(10)
        nb = button("Nueva impresora", "Primary", "mdi6.plus", lambda: self.open_create())
        nb.setMinimumHeight(46)
        bar.addWidget(nb)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar…")
        self.search.setMinimumHeight(40)
        self.search.textChanged.connect(lambda t: filter_table(self.table, t))
        bar.addWidget(self.search, 1)
        bar.addWidget(button("Spooler", "", "mdi6.restart", self.restart_spooler, "Reiniciar servicio de impresión"))
        bar.addWidget(button("", "Ghost", "mdi6.refresh", self.refresh, "Actualizar"))
        self.root.addLayout(bar)

        card = Card()
        self.table = make_table(["Nombre", "Controlador", "IP", "Puerto", "Estado", "Cola", "Sistema"], stretch_col=1)
        self.table.doubleClicked.connect(lambda _i: self.edit())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        card.add(self.table, 1)
        self.root.addWidget(card, 1)

        acts = QHBoxLayout()
        acts.setSpacing(10)
        for text, tip, ic, fn, kind in (
                ("Prueba", "Imprimir prueba", "mdi6.printer-check", self.test_menu, "Primary"),
                ("Editar", "Editar impresora", "mdi6.pencil-outline", self.edit, ""),
                ("Diagnóstico", "Diagnosticar", "mdi6.stethoscope", self.diagnose, ""),
                ("Reiniciar", "Reiniciar cola de impresión", "mdi6.restore", self.restart_queue, ""),
                ("Vaciar", "Vaciar cola de impresión", "mdi6.broom", self.clear_queue, ""),
                ("Eliminar", "Eliminar impresora", "mdi6.delete-outline", self.remove, "Danger")):
            b = button(text, kind, ic, fn, tip)
            b.setMinimumHeight(44)
            b.setMinimumWidth(b.sizeHint().width())   # nunca recortar el texto
            acts.addWidget(b)
        acts.addStretch(1)
        self.root.addLayout(acts)
        self.task = TaskPanel()
        self.root.addWidget(self.task)
        self.printers = []
        bus.data_changed.connect(lambda a: a == "printers" and self.refresh())

    def on_show(self):
        super().on_show()
        self.refresh()

    def refresh(self):
        run_task(_load, on_result=self._fill, on_error=lambda m: notify("error", "Impresoras", m))

    def _fill(self, printers):
        self.printers = printers
        pal = theme.current()
        rows = [[p.name, p.driver, p.ip, p.port or "", p.status, p.jobs, p.os] for p in printers]
        fill_table(self.table, rows, data=printers, colors={
            4: lambda s: pal.success if ("lista" in s.lower() or "imprimiendo" in s.lower())
            else (pal.danger if s else None)})
        filter_table(self.table, self.search.text())

    # ------------------------------------------------------------ acciones
    def open_create(self, ip: str = "", port: int = 9100):
        d = PrinterDialog(self, "create", ip, port)
        d.exec()
        self.refresh()

    def _sel(self):
        p = selected_data(self.table)
        if not p:
            notify("info", "Seleccione una impresora")
        return p

    def edit(self):
        p = self._sel()
        if p:
            PrinterDialog(self, "edit", printer=p).exec()
            self.refresh()

    def diagnose(self):
        p = self._sel()
        if p:
            bus.diagnose_printer.emit(p.ip or "", p.port or 9100, p.name)

    def test_menu(self):
        p = self._sel()
        if not p:
            return
        m = QMenu(self)
        m.addAction("Ticket ESC/POS (por la cola)", lambda: run_test_print(self, p.name, "escpos"))
        m.addAction("Página de prueba del sistema", lambda: run_test_print(self, p.name, "system"))
        if p.ip:
            m.addAction("Directa TCP (sin cola)", lambda: run_test_print(self, p.name, "direct", p.ip, p.port or 9100))
        m.exec(self.cursor().pos())

    def _op(self, title, fn, *args):
        def done(r):
            if r.ok:
                notify("ok", title, r.message)
            else:
                info_dialog(self, title, r.message, r.detail, "error")
            self.refresh()
        self.task.run(lambda _c: fn(*args), on_result=done, cancellable=False)

    def clear_queue(self):
        p = self._sel()
        if p and confirm(self, "Vaciar cola", f"Se cancelarán los trabajos pendientes de {p.name}.", danger=True,
                         ok_text="Vaciar"):
            from app.printers.manager import PrinterManager
            self._op("Vaciar cola", PrinterManager().clear_queue, p.name)

    def restart_queue(self):
        p = self._sel()
        if p:
            from app.printers.manager import PrinterManager
            self._op("Reiniciar cola", PrinterManager().restart_queue, p.name)

    def restart_spooler(self):
        if confirm(self, "Reiniciar servicio de impresión", "Afecta a todas las impresoras del equipo.",
                   ok_text="Reiniciar"):
            from app.printers.manager import PrinterManager
            self._op("Servicio de impresión", PrinterManager().restart_spooler)

    def remove(self):
        p = self._sel()
        if p and confirm(self, f"Eliminar {p.name}", "Se quitará la impresora del sistema operativo.", danger=True,
                         ok_text="Eliminar"):
            from app.printers.manager import PrinterManager
            self._op("Eliminar impresora", PrinterManager().remove, p.name)
            bus.data_changed.emit("history")

    def _menu(self, pos):
        if not selected_data(self.table):
            return
        m = QMenu(self)
        m.addAction("Imprimir prueba", self.test_menu)
        m.addAction("Editar", self.edit)
        m.addAction("Diagnosticar", self.diagnose)
        m.addAction("Reiniciar cola", self.restart_queue)
        m.addAction("Vaciar cola", self.clear_queue)
        m.addSeparator()
        m.addAction("Eliminar", self.remove)
        m.exec(self.table.viewport().mapToGlobal(pos))
