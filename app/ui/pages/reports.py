"""Informes PDF e historial."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QTabWidget

from app.core import config
from app.database.db import get_db

from .. import theme
from ..bus import notify
from ..widgets import Card, TaskPanel, button, fill_table, filter_table, make_table, selected_data
from .base import BasePage


class ReportsPage(BasePage):
    key = "reports"
    title = "Informes"

    def __init__(self):
        super().__init__()
        top = QHBoxLayout()
        top.setSpacing(14)
        gen = Card("Nuevo informe", icon_name="mdi6.file-pdf-box")
        f = QFormLayout()
        f.setVerticalSpacing(10)
        self.client = QLineEdit()
        self.client.setPlaceholderText("Restaurante / cliente")
        self.tech = QLineEdit(config.get("technician") or "")
        self.ttl = QLineEdit("Intervención técnica")
        self.period = QComboBox()
        self.period.addItems(["Hoy", "Últimos 7 días", "Todo"])
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Observaciones")
        self.notes.setMaximumHeight(80)
        f.addRow("Cliente", self.client)
        f.addRow("Técnico", self.tech)
        f.addRow("Título", self.ttl)
        f.addRow("Periodo", self.period)
        f.addRow("Notas", self.notes)
        gen.add(f)
        b = button("Generar PDF", "Primary", "mdi6.file-pdf-box", self.generate)
        b.setMinimumHeight(46)
        gen.add(b)
        self.task = TaskPanel()
        gen.add(self.task)
        top.addWidget(gen, 2)

        lst = Card("Informes generados", icon_name="mdi6.file-document-outline")
        self.reports = make_table(["Fecha", "Cliente", "Título", "Archivo"], stretch_col=3)
        self.reports.doubleClicked.connect(lambda _i: self.open_report())
        lst.add(self.reports, 1)
        rb = QHBoxLayout()
        rb.addWidget(button("Abrir", "", "mdi6.open-in-new", self.open_report))
        rb.addWidget(button("Carpeta", "", "mdi6.folder-open-outline", self.open_folder))
        rb.addStretch(1)
        lst.add(rb)
        top.addWidget(lst, 3)
        self.root.addLayout(top)

        tabs = QTabWidget()
        self.ops = make_table(["Fecha", "Área", "Acción", "Objetivo", "Estado", "Detalle"], stretch_col=5)
        self.logs = make_table(["Fecha", "Nivel", "Módulo", "Mensaje"], stretch_col=3)
        tabs.addTab(self.ops, "Historial")
        tabs.addTab(self.logs, "Logs")
        hist = Card()
        sh = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar…")
        self.search.textChanged.connect(lambda t: (filter_table(self.ops, t), filter_table(self.logs, t)))
        sh.addWidget(self.search, 1)
        sh.addWidget(button("", "Ghost", "mdi6.refresh", self.load, "Actualizar"))
        hist.add(sh)
        hist.add(tabs, 1)
        self.root.addWidget(hist, 1)

    def on_show(self):
        super().on_show()
        self.load()

    def load(self):
        db = get_db()
        pal = theme.current()
        reps = db.query("SELECT * FROM reports ORDER BY id DESC LIMIT 100")
        fill_table(self.reports, [[r["ts"].replace("T", " ")[:16], r["client"], r["title"], Path(r["path"]).name]
                                  for r in reps], data=[r["path"] for r in reps])
        ops = db.query("SELECT * FROM operations ORDER BY id DESC LIMIT 500")
        fill_table(self.ops, [[r["ts"].replace("T", " "), r["category"], r["action"], r["target"], r["status"],
                               r["detail"][:200]] for r in ops],
                   colors={4: lambda s: pal.success if s == "OK" else (pal.danger if s in ("ERROR", "BLOQUEADO")
                                                                      else pal.warning)})
        logs = db.query("SELECT * FROM logs ORDER BY id DESC LIMIT 500")
        fill_table(self.logs, [[r["ts"].replace("T", " "), r["level"], r["logger"], r["message"][:300]] for r in logs],
                   colors={1: lambda s: pal.danger if s in ("ERROR", "CRITICAL") else (
                       pal.warning if s == "WARNING" else None)})

    def generate(self):
        from datetime import datetime, timedelta

        from app.reports.pdf_report import generate_report
        since = {0: datetime.now().replace(hour=0, minute=0, second=0).isoformat(timespec="seconds"),
                 1: (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds"), 2: None}[self.period.currentIndex()]
        if self.tech.text().strip():
            config.set("technician", self.tech.text().strip())

        def done(path):
            notify("ok", "Informe generado", Path(path).name)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            self.load()
        self.task.run(generate_report, self.ttl.text().strip() or "Intervención técnica", self.client.text().strip(),
                      self.tech.text().strip(), self.notes.toPlainText().strip(), {"since": since},
                      on_result=done)

    def open_report(self):
        p = selected_data(self.reports)
        if p and Path(p).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))
        elif p:
            notify("error", "El archivo ya no existe", p)

    def open_folder(self):
        from app.core import paths
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.reports_dir())))
