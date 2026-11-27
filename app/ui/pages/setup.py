"""PC nueva: buscar → identificar → nombrar e instalar las impresoras del local.

Cada fila tiene su botón Instalar: nada se crea sin un nombre escrito por el técnico, y las
impresoras que ya existen en este equipo no se tocan.
"""
from __future__ import annotations

import ipaddress

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QTableWidget, QTableWidgetItem, QWidget)

from app.core.platform_info import IS_WINDOWS, is_admin
from app.core.tasks import run_task
from app.core.validation import ValidationError, parse_target_range

from .. import theme
from ..bus import bus, notify
from ..widgets import Card, TaskPanel, button, confirm, info_dialog
from .base import BasePage

COLS = ["", "Impresora", "Estado", "Nombre", "Controlador", ""]
C_CHK, C_DEV, C_ST, C_NAME, C_DRV, C_ACT = range(6)


def _load_context(ctx):
    from app.network.interfaces import network_summary
    from app.platform import printer_backend
    be = printer_backend()
    ns = network_summary()
    try:
        drivers = be.list_drivers()
    except Exception:  # noqa: BLE001
        drivers = []
    try:
        names = [p.name for p in be.list_printers()]
    except Exception:  # noqa: BLE001
        names = []
    return {"ns": ns, "drivers": drivers, "names": names}


class StepPill(QLabel):
    def __init__(self, n: int, text: str):
        super().__init__(f"{n}   {text}")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(40)
        self.setMinimumWidth(170)
        self.set_state("pending")

    def set_state(self, state: str):
        p = theme.current()
        if state == "active":
            css = f"background:{p.primary}; color:white; border:1px solid {p.primary};"
        elif state == "done":
            css = f"background:{p.surface2}; color:{p.success}; border:1px solid {p.success};"
        else:
            css = f"background:{p.surface2}; color:{p.muted}; border:1px solid {p.border};"
        self.setStyleSheet(f"QLabel {{ {css} border-radius:20px; font-size:16px; font-weight:600;"
                           " padding:0 18px; }")


class SetupPage(BasePage):
    key = "setup"
    title = "PC nueva"

    def __init__(self):
        super().__init__()
        # ---- pasos
        steps = QHBoxLayout()
        steps.setSpacing(10)
        self.pills = [StepPill(1, "Buscar"), StepPill(2, "Identificar"), StepPill(3, "Nombrar e instalar")]
        for i, pl in enumerate(self.pills):
            steps.addWidget(pl)
            if i < 2:
                arrow = QLabel("›")
                arrow.setObjectName("Muted")
                arrow.setStyleSheet("font-size:24px;")
                steps.addWidget(arrow)
        steps.addStretch(1)
        self.root.addLayout(steps)

        # ---- buscar
        bar = QHBoxLayout()
        bar.setSpacing(10)
        lb = QLabel("Red")
        lb.setObjectName("FormLabel")
        bar.addWidget(lb)
        self.net = QLineEdit()
        self.net.setPlaceholderText("192.168.1.0/24")
        self.net.setMinimumHeight(46)
        self.net.setMinimumWidth(230)
        self.net.returnPressed.connect(self.find)
        bar.addWidget(self.net)
        self.find_btn = button("Buscar impresoras", "Primary", "mdi6.printer-search", self.find)
        self.find_btn.setMinimumHeight(46)
        bar.addWidget(self.find_btn)
        bar.addStretch(1)
        self.root.addLayout(bar)
        self.task = TaskPanel()
        self.task.finished.connect(self._idle)
        self.root.addWidget(self.task)

        # ---- tabla
        card = Card()
        head = QHBoxLayout()
        self.all_chk = QCheckBox("Todas")
        self.all_chk.clicked.connect(self._toggle_all)
        head.addWidget(self.all_chk)
        head.addSpacing(14)
        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        head.addWidget(self.summary)
        head.addStretch(1)
        lbd = QLabel("Controlador para todas")
        lbd.setObjectName("Muted")
        head.addWidget(lbd)
        self.drv_all = QComboBox()
        self.drv_all.setMinimumWidth(240)
        self.drv_all.addItem("Cargando…", None)
        self.drv_all.activated.connect(self._apply_driver_all)
        head.addWidget(self.drv_all)
        card.add(head)

        t = QTableWidget(0, len(COLS))
        t.setHorizontalHeaderLabels(COLS)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(64)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        t.setSortingEnabled(False)
        t.setWordWrap(True)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.setFocusPolicy(Qt.NoFocus)
        hh = t.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(C_DEV, QHeaderView.Stretch)
        hh.setMinimumSectionSize(40)
        for c, w in ((C_CHK, 44), (C_ST, 180), (C_NAME, 170), (C_DRV, 210), (C_ACT, 262)):
            t.setColumnWidth(c, w)
        self.table = t
        card.add(t, 1)
        t.hide()
        self.empty = QLabel("Pulse «Buscar impresoras»")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setObjectName("Muted")
        self.empty.setStyleSheet("font-size:20px; padding:30px;")
        card.add(self.empty, 1)
        self.root.addWidget(card, 1)

        # ---- acciones en lote (sobre las filas marcadas)
        acts = QHBoxLayout()
        acts.setSpacing(10)
        self.ident_btn = button("Imprimir número", "", "mdi6.numeric", self.identify,
                                "Imprime en cada impresora marcada su número de IP en grande")
        self.install_btn = button("Instalar marcadas", "Primary", "mdi6.check-all", self.install_checked,
                                  "Instala las impresoras marcadas y saca un ticket de prueba en cada una")
        for b in (self.ident_btn, self.install_btn):
            b.setMinimumHeight(48)
            b.setMinimumWidth(b.sizeHint().width() + 30)
        acts.addWidget(self.ident_btn)
        acts.addStretch(1)
        acts.addWidget(self.install_btn)
        self.root.addLayout(acts)

        self.rows: list[dict] = []
        self.drivers = []
        self.existing: list[str] = []
        self.ns = None
        self._set_step(0)
        self._update_buttons()
        bus.theme_changed.connect(lambda _t: self._set_step(self._step))

    # ------------------------------------------------------------ estado general
    def first_show(self):
        run_task(_load_context, on_result=self._context, on_error=lambda m: notify("error", "PC nueva", m))

    def _context(self, data):
        self.ns = data["ns"]
        self.drivers = data["drivers"]
        self.existing = data["names"]
        p = self.ns.primary if self.ns else None
        if p and p.ipv4 and not self.net.text().strip():
            net = ipaddress.IPv4Network(f"{p.ipv4}/24", strict=False)
            if p.network and ipaddress.IPv4Network(p.network).num_addresses <= 1024:
                net = ipaddress.IPv4Network(p.network)
            self.net.setText(str(net))
        self.drv_all.clear()
        self.drv_all.addItem("Automático", None)
        for d in self.drivers:
            self.drv_all.addItem(d.name, d)
        for r in self.rows:
            if not r["locked"]:
                self._fill_driver_combo(r)

    def _set_step(self, i: int):
        self._step = i
        for k, pl in enumerate(self.pills):
            pl.set_state("done" if k < i else ("active" if k == i else "pending"))

    def _idle(self):
        self.find_btn.setEnabled(True)
        self._update_buttons()

    def _open_rows(self) -> list[dict]:
        return [r for r in self.rows if not r["locked"]]

    def _checked(self) -> list[dict]:
        return [r for r in self._open_rows() if r["chk"].isChecked()]

    def _update_buttons(self):
        n = len(self._checked())
        busy = self.task.busy
        self.ident_btn.setEnabled(n > 0 and not busy)
        self.install_btn.setEnabled(n > 0 and not busy)
        self.install_btn.setText(f"Instalar marcadas ({n})" if n else "Instalar marcadas")
        for r in self.rows:
            if not r["locked"]:
                r["install"].setEnabled(not busy)
            r["test"].setEnabled(not busy)
        opens = self._open_rows()
        self.all_chk.setEnabled(bool(opens))
        self.all_chk.setChecked(bool(opens) and n == len(opens))

    def _toggle_all(self, on: bool):
        for r in self._open_rows():
            r["chk"].setChecked(on)
        self._update_buttons()

    def _busy_msg(self) -> bool:
        if self.task.busy:
            notify("info", "Espere a que termine la operación en curso")
            return True
        return False

    # ------------------------------------------------------------ 1. buscar
    def find(self):
        txt = self.net.text().strip() or self.net.placeholderText()
        try:
            targets = parse_target_range(txt)
        except ValidationError as e:
            notify("error", "Red inválida", str(e))
            return
        from app.printers.batch import find_printers
        self.find_btn.setEnabled(False)
        self.summary.setText("")
        self.task.run(find_printers, targets, on_result=self._found)

    def _found(self, res):
        self.table.setRowCount(0)
        self.rows = []
        printers = res["printers"]
        for c in printers:
            self._add_row(c)
        self.empty.setVisible(not printers)
        self.table.setVisible(bool(printers))
        self.empty.setText("No se encontraron impresoras en esta red. Revise que estén encendidas y "
                           "con cable." if not printers else "")
        new = sum(1 for c in printers if not c.installed_as)
        self.summary.setText(f"{len(printers)} impresoras · {new} sin instalar · {res['alive']} equipos en la red")
        if printers:
            self._set_step(1)
            notify("ok", f"{len(printers)} impresoras encontradas",
                   "Use «Prueba» para saber cuál es cuál, escriba el nombre y pulse Instalar")
        self._update_buttons()

    def _add_row(self, c):
        from app.printers.batch import COMMON_NAMES
        pal = theme.current()
        t = self.table
        r = t.rowCount()
        t.insertRow(r)

        chk = QCheckBox()
        chk.stateChanged.connect(lambda _s: self._update_buttons())
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setAlignment(Qt.AlignCenter)
        hl.addWidget(chk)
        t.setCellWidget(r, C_CHK, holder)

        dev = QLabel(f"<span style='font-size:17px; font-weight:700'>{c.ip}</span><br>"
                     f"<span style='color:{pal.muted}'>{c.label}"
                     + (f" · puerto {c.port}" if c.port != 9100 else "") + "</span>")
        dev.setContentsMargins(6, 0, 6, 0)
        dev.setToolTip("\n".join(x for x in (c.vendor, c.model, c.hostname, c.mac) if x))
        t.setCellWidget(r, C_DEV, dev)
        st = QTableWidgetItem("")
        t.setItem(r, C_ST, st)

        name = QComboBox()
        name.setEditable(True)
        name.addItems([""] + COMMON_NAMES)
        name.setCurrentIndex(0)
        name.lineEdit().setPlaceholderText("Nombre")
        name.setMinimumHeight(40)
        t.setCellWidget(r, C_NAME, self._pad(name))
        drv = QComboBox()
        drv.setMinimumHeight(40)
        t.setCellWidget(r, C_DRV, self._pad(drv))

        act = QWidget()
        al = QHBoxLayout(act)
        al.setContentsMargins(6, 0, 6, 0)
        al.setSpacing(8)
        test = button("Prueba", "", "mdi6.printer-eye", None, "Identificar la impresora o imprimir su autotest")
        menu = QMenu(test)
        menu.addAction("Imprimir número de IP", lambda: self._identify_rows([row]))
        menu.addAction("Autotest de la ticketera (su configuración)", lambda: self._self_test(row))
        test.clicked.connect(lambda: menu.exec(test.mapToGlobal(test.rect().bottomLeft())))
        install = button("Instalar", "Primary", "mdi6.check", lambda: self._install_rows([row]))
        for b in (test, install):
            b.setMinimumHeight(40)
        install.setMinimumWidth(install.fontMetrics().horizontalAdvance("Instalada") + 56)
        al.addWidget(test)
        al.addWidget(install)
        t.setCellWidget(r, C_ACT, act)

        row = {"c": c, "chk": chk, "name": name, "drv": drv, "status": st, "test": test, "install": install,
               "locked": False}
        self.rows.append(row)
        self._fill_driver_combo(row)
        name.lineEdit().textChanged.connect(lambda _t: self._mark_name(row, False))
        name.lineEdit().returnPressed.connect(lambda: self._install_rows([row]))
        if c.installed_as:
            self._lock(row, c.installed_as, f"Ya instalada · {c.installed_as}", pal.primary)
        else:
            self._status(row, "Nueva", pal.success)

    @staticmethod
    def _pad(w: QWidget) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.addWidget(w)
        return box

    def _lock(self, row, name: str, status: str, color: str):
        """Fila terminada o que ya existía: no se puede volver a tocar desde aquí."""
        row["locked"] = True
        row["chk"].setChecked(False)
        row["chk"].setEnabled(False)
        row["name"].setEditText(name)
        row["name"].setEnabled(False)
        row["drv"].setEnabled(False)
        row["install"].setEnabled(False)
        row["install"].setText("Instalada")
        self._status(row, status, color)

    def _mark_name(self, row, bad: bool):
        le = row["name"].lineEdit()
        le.setProperty("invalid", bad)
        row["name"].setProperty("invalid", bad)
        for w in (le, row["name"]):
            w.style().unpolish(w)
            w.style().polish(w)

    def _fill_driver_combo(self, row):
        from app.printers.batch import default_driver
        cb = row["drv"]
        cb.clear()
        if not self.drivers:
            cb.addItem("Cargando…", None)
            return
        for d in self.drivers:
            cb.addItem(d.name, d)
            cb.setItemData(cb.count() - 1, d.name, Qt.ToolTipRole)
        best = default_driver(self.drivers, row["c"].vendor, row["c"].model)
        if best is not None:
            cb.setCurrentIndex(self.drivers.index(best))

    def _apply_driver_all(self, _i: int):
        d = self.drv_all.currentData()
        for r in self._open_rows():
            if d is None:
                self._fill_driver_combo(r)
            else:
                r["drv"].setCurrentIndex(self.drivers.index(d))

    def _status(self, row, text: str, color: str | None = None):
        it = row["status"]
        it.setText(text)
        it.setToolTip(text)
        if color:
            it.setForeground(QColor(color))
            f = it.font()
            f.setBold(True)
            it.setFont(f)

    # ------------------------------------------------------------ 2. identificar
    def identify(self):
        self._identify_rows(self._checked())

    def _identify_rows(self, rows):
        if not rows or self._busy_msg():
            return
        from app.printers.batch import identify
        pal = theme.current()
        by_ip = {r["c"].ip: r for r in rows}

        def done(results):
            ok = 0
            for ip, good, msg in results:
                r = by_ip.get(ip)
                if not r:
                    continue
                if good:
                    ok += 1
                    if not r["locked"]:
                        self._status(r, f"Número {r['c'].last_octet} impreso", pal.success)
                else:
                    self._status(r, f"No imprimió: {msg}", pal.danger)
            if self._step < 2:
                self._set_step(2)
            notify("ok" if ok == len(results) else "warning", f"Número impreso en {ok} de {len(results)}",
                   "Pregunte qué número salió en cada impresora y escriba su nombre")
            first = next((r for r in rows if not r["locked"] and not r["name"].currentText().strip()), None)
            if first:
                first["name"].setFocus()
        self.task.run(identify, [(r["c"].ip, r["c"].port) for r in rows], on_result=done)
        self._update_buttons()

    def _self_test(self, row):
        if self._busy_msg():
            return
        from app.printers.batch import self_test
        pal = theme.current()

        def done(res):
            ok, msg = res
            if ok:
                if not row["locked"]:
                    self._status(row, "Autotest enviado", pal.success)
                notify("ok", f"Autotest enviado a {row['c'].ip}",
                       "Sale la hoja de configuración de la ticketera (IP de LAN y Wi-Fi según modelo). "
                       "Si no sale nada: apáguela y enciéndala manteniendo FEED.")
            else:
                self._status(row, f"Autotest: {msg}", pal.danger)
        self.task.run(self_test, row["c"].ip, row["c"].port, on_result=done, cancellable=False)
        self._update_buttons()

    # ------------------------------------------------------------ 3. instalar
    def install_checked(self):
        self._install_rows(self._checked())

    def _install_rows(self, rows):
        rows = [r for r in rows if not r["locked"]]
        if not rows or self._busy_msg():
            return
        if IS_WINDOWS and not is_admin():
            info_dialog(self, "Hace falta administrador",
                        "Pulse «Reiniciar como administrador» (arriba a la derecha) y vuelva a intentarlo.",
                        kind="warning")
            return
        from app.printers.batch import InstallJob, check_names, clean_name, driver_ref
        jobs, unnamed = [], []
        for r in rows:
            raw = r["name"].currentText().strip()
            if not raw:
                unnamed.append(r)
                self._mark_name(r, True)
                continue
            d = r["drv"].currentData()
            if d is None:
                notify("error", "Falta el controlador", r["c"].ip)
                return
            jobs.append(InstallJob(clean_name(raw), driver_ref(d), r["c"].ip, r["c"].port))
        if unnamed:
            unnamed[0]["name"].setFocus()
            notify("warning", "Escriba el nombre", "Ponga un nombre a " +
                   ", ".join(r["c"].ip for r in unnamed) + " antes de instalar")
            return
        errs = check_names([j.name for j in jobs], self.existing)
        if errs:
            info_dialog(self, "Revise los nombres", "\n".join(errs), kind="error")
            return
        if len(jobs) > 1:
            listing = "<br>".join(f"<b>{j.name}</b> → {j.ip}" for j in jobs)
            if not confirm(self, f"Instalar {len(jobs)} impresoras",
                           f"{listing}<br><br>Cada una sacará un ticket de prueba con su nombre.",
                           ok_text=f"Instalar {len(jobs)}"):
                return
        from app.printers.batch import install_batch
        pal = theme.current()
        by_ip = {r["c"].ip: r for r in rows}
        for r in rows:
            self._status(r, "Instalando…" if len(rows) == 1 else "En cola…", pal.muted)

        def partial(item):
            _i, o = item
            r = by_ip.get(o.ip)
            if not r:
                return
            if o.created:
                self._lock(r, o.name, "✓ " + o.message, pal.success if o.printed else pal.warning)
            else:
                self._status(r, "✗ " + o.message, pal.danger)

        def done(results):
            created = [o for o in results if o.created]
            self.existing += [o.name for o in created]
            bus.data_changed.emit("printers")
            bus.data_changed.emit("history")
            if created and len(created) == len(results):
                if not self._open_rows():
                    self._set_step(3)
                notify("ok", ", ".join(o.name for o in created) + (" instalada" if len(created) == 1
                                                                    else " instaladas"),
                       "Confirme que salió el ticket de prueba")
            else:
                bad = [o for o in results if not o.created]
                info_dialog(self, f"{len(created)} de {len(results)} instaladas",
                            "No se pudo instalar:",
                            "\n".join(f"{o.name} ({o.ip}): {o.message}" for o in bad), "warning")
        self.task.run(install_batch, jobs, on_partial=partial, on_result=done)
        self._update_buttons()
