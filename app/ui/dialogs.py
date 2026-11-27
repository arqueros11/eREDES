"""Diálogos de impresoras: alta (4 campos) y edición, con selector de controladores."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from app.core.platform_info import os_key
from app.core.tasks import run_task
from app.core.validation import (ValidationError, suggest_cups_name, validate_ipv4, validate_port,
                                 validate_printer_name)
from app.platform.base import DriverRef

from . import icons, theme
from .bus import bus, notify
from .widgets import Banner, TaskPanel, button, confirm, info_dialog

SYSTEM = "__system__"


def _load_driver_sources(ctx):
    from app.drivers.library import DriverLibrary
    from app.platform import printer_backend
    lib = DriverLibrary()
    lib.sync()
    mfrs = [(m["id"], m["name"], m["n_packages"]) for m in lib.manufacturers()]
    models = {mid: [(mo["id"], mo["name"], mo["n_packages"]) for mo in lib.models(mid)] for mid, _, _ in mfrs}
    system = printer_backend().list_drivers()
    return {"mfrs": mfrs, "models": models, "system": system}


def _choices_for_model(ctx, model_id):
    from app.drivers.library import DriverLibrary
    from app.printers.manager import package_driver_choices
    return package_driver_choices(DriverLibrary(), model_id)


class DriverSelector(QWidget):
    """Fabricante → Modelo → Controlador (biblioteca) o controladores instalados en el sistema."""

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        self.src_lib = QPushButton("Biblioteca eREDES")
        self.src_sys = QPushButton("Instalados en el sistema")
        self.group = QButtonGroup(self)
        for i, b in enumerate((self.src_lib, self.src_sys)):
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.group.addButton(b, i)
            seg.addWidget(b)
        self.src_lib.setChecked(True)
        seg.addStretch(1)
        v.addLayout(seg)
        row = QHBoxLayout()
        self.mfr = QComboBox()
        self.model = QComboBox()
        self.mfr.setMinimumWidth(150)
        self.model.setMinimumWidth(150)
        row.addWidget(self.mfr, 1)
        row.addWidget(self.model, 1)
        v.addLayout(row)
        self.drv = QComboBox()
        self.drv.setMinimumWidth(360)
        v.addWidget(self.drv)
        self.why = QLabel("")
        self.why.setObjectName("Muted")
        self.why.setWordWrap(True)
        v.addWidget(self.why)
        self.data = None
        self._preselect = ""
        self.group.idClicked.connect(self._source_changed)
        self.mfr.currentIndexChanged.connect(self._mfr_changed)
        self.model.currentIndexChanged.connect(self._model_changed)
        self.drv.currentIndexChanged.connect(self._drv_changed)
        self._style_segment()
        self.drv.addItem("Cargando controladores…", None)
        run_task(_load_driver_sources, on_result=self._loaded,
                 on_error=lambda m: notify("error", "No se pudieron cargar los controladores", m))

    def _style_segment(self):
        p = theme.current()
        base = (f"QPushButton {{ border:1px solid {p.border}; padding:5px 14px; background:{p.surface2}; font-weight:500; }}"
                f"QPushButton:checked {{ background:{p.primary}; color:white; border-color:{p.primary}; }}")
        self.src_lib.setStyleSheet(base + "QPushButton { border-top-right-radius:0; border-bottom-right-radius:0; }")
        self.src_sys.setStyleSheet(base + "QPushButton { border-top-left-radius:0; border-bottom-left-radius:0; }")

    def preselect(self, driver_name: str):
        self._preselect = driver_name

    def _loaded(self, data):
        self.data = data
        self.mfr.blockSignals(True)
        self.mfr.clear()
        for mid, name, n in sorted(data["mfrs"], key=lambda x: (x[2] == 0, x[1])):
            self.mfr.addItem(name if n == 0 else f"{name}  ·  {n}", mid)
        self.mfr.blockSignals(False)
        has_lib = any(n for _, _, n in data["mfrs"])
        if self._preselect or not has_lib:
            self.src_sys.setChecked(True)
            self._source_changed(1)
            i = self.drv.findData(self._preselect, Qt.UserRole + 1)
            if i >= 0:
                self.drv.setCurrentIndex(i)
        else:
            self._source_changed(0)

    def _source_changed(self, idx: int):
        lib = idx == 0
        self.mfr.setVisible(lib)
        self.model.setVisible(lib)
        if not self.data:
            return
        if lib:
            self._mfr_changed()
        else:
            self.drv.blockSignals(True)
            self.drv.clear()
            for d in self.data["system"]:
                label = d.name + (f"  · v{d.version}" if d.version else "")
                self.drv.addItem(label, DriverRef(d.kind if d.kind != "model" else "model", d.ref, d.name))
                self.drv.setItemData(self.drv.count() - 1, d.name, Qt.UserRole + 1)
            if not self.data["system"]:
                self.drv.addItem("Sin controladores instalados", None)
            self.drv.blockSignals(False)
            self._drv_changed()

    def _mfr_changed(self):
        if not self.data:
            return
        mid = self.mfr.currentData()
        self.model.blockSignals(True)
        self.model.clear()
        for moid, name, n in self.data["models"].get(mid, []):
            self.model.addItem(name if n == 0 else f"{name}  ·  {n}", moid)
        self.model.blockSignals(False)
        if self.model.count() == 0:
            self.drv.clear()
            self.drv.addItem("Sin modelos", None)
            self._drv_changed()
        else:
            self._model_changed()

    def _model_changed(self):
        moid = self.model.currentData()
        if moid is None:
            return
        self.drv.clear()
        self.drv.addItem("Cargando…", None)
        run_task(_choices_for_model, moid, on_result=self._fill_choices)

    def _fill_choices(self, choices):
        self.drv.blockSignals(True)
        self.drv.clear()
        for ref, label, ok, why in choices:
            self.drv.addItem(("✓ " if ok else "✗ ") + label, ref if ok else None)
            self.drv.setItemData(self.drv.count() - 1, why, Qt.ToolTipRole)
        if not choices:
            self.drv.addItem("Sin drivers", None)
        self.drv.blockSignals(False)
        for i in range(self.drv.count()):
            if self.drv.itemData(i) is not None:
                self.drv.setCurrentIndex(i)
                break
        self._drv_changed()

    def _drv_changed(self):
        why = self.drv.itemData(self.drv.currentIndex(), Qt.ToolTipRole) or ""
        ref = self.drv.currentData()
        msg = why if (ref is None or why not in ("Compatible.", "")) else ""
        self.why.setText(msg)
        self.why.setVisible(bool(msg))

    def value(self) -> DriverRef | None:
        return self.drv.currentData()


class PrinterDialog(QDialog):
    """Alta (mode='create') o edición (mode='edit') de una impresora."""

    def __init__(self, parent=None, mode: str = "create", ip: str = "", port: int = 9100, printer=None):
        super().__init__(parent)
        self.mode = mode
        self.printer = printer
        self.created_name = ""
        self.setWindowTitle("Nueva impresora" if mode == "create" else f"Editar {printer.name}")
        self.setMinimumWidth(620)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 18)
        v.setSpacing(14)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.pixmap("mdi6.printer-pos", theme.current().primary, 34))
        head.addWidget(ic)
        tt = QLabel(f"<b style='font-size:20px'>{'Nueva impresora' if mode == 'create' else 'Editar impresora'}</b>")
        head.addWidget(tt, 1)
        v.addLayout(head)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        self.name = QLineEdit(printer.name if printer else "")
        self.name.setPlaceholderText("COCINA")
        self.name.textChanged.connect(self._validate_name)
        self.driver = DriverSelector()
        if printer and printer.driver:
            self.driver.preselect(printer.driver)
        iprow = QHBoxLayout()
        self.ip = QLineEdit(printer.ip if printer else ip)
        self.ip.setPlaceholderText("192.168.1.240")
        self.ip.textChanged.connect(self._validate_ip)
        self.check_btn = button("Comprobar conexión", "", "mdi6.lan-connect", self._check_conn)
        iprow.addWidget(self.ip, 1)
        iprow.addWidget(self.check_btn)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int((printer.port if printer and printer.port else port) or 9100))
        self.port.setFixedWidth(140)
        form.addRow("1. Nombre", self.name)
        form.addRow("2. Controlador", self.driver)
        form.addRow("3. Dirección IP", iprow)
        form.addRow("4. Puerto TCP/IP", self.port)
        v.addLayout(form)
        self.hint = Banner("", "info")
        v.addWidget(self.hint)

        self.steps_box = QFrame()
        self.steps_lay = QVBoxLayout(self.steps_box)
        self.steps_lay.setContentsMargins(0, 0, 0, 0)
        self.steps_lay.setSpacing(4)
        self.steps_box.hide()
        v.addWidget(self.steps_box)
        self.task = TaskPanel()
        v.addWidget(self.task)

        btns = QHBoxLayout()
        self.test_btn = button("Imprimir prueba", "", "mdi6.printer-check", self._test_print)
        self.test_btn.hide()
        btns.addWidget(self.test_btn)
        btns.addStretch(1)
        self.cancel_btn = button("Cerrar", "Ghost", None, self.reject)
        self.ok_btn = button("Crear impresora" if mode == "create" else "Guardar cambios", "Primary",
                             "mdi6.check-circle", self._submit)
        btns.addWidget(self.cancel_btn)
        btns.addWidget(self.ok_btn)
        v.addLayout(btns)
        if mode == "create" and not ip:
            self.name.setFocus()

    # ------------------------------------------------------------ validación en vivo
    def _validate_name(self):
        txt = self.name.text()
        try:
            if txt:
                validate_printer_name(txt)
            self.name.setProperty("invalid", False)
            self.hint.set("", "info")
        except ValidationError as e:
            self.name.setProperty("invalid", True)
            self.hint.set(str(e), "warning")
        self.name.style().polish(self.name)

    def _validate_ip(self):
        txt = self.ip.text().strip()
        bad = bool(txt) and txt.count(".") == 3 and not _ok_ip(txt)
        self.ip.setProperty("invalid", bad)
        self.ip.style().polish(self.ip)

    def _check_conn(self):
        try:
            ip = validate_ipv4(self.ip.text())
            port = validate_port(self.port.value())
        except ValidationError as e:
            self.hint.set(str(e), "error")
            return
        from app.network import probes
        self.check_btn.setEnabled(False)
        self.hint.set(f"Comprobando {ip}:{port}…", "info")

        def done(r):
            state, rtt = r
            if state == "open":
                self.hint.set(f"Conexión correcta con {ip}:{port} ({rtt:.0f} ms).", "ok")
            elif state == "closed":
                self.hint.set(f"{ip} responde pero el puerto {port} está cerrado. ¿Puerto correcto?", "warning")
            else:
                self.hint.set(f"Sin respuesta de {ip}:{port}. Revise IP, cable y alimentación.", "error")
        run_task(lambda _c: probes.tcp_probe(ip, port, 2.5), on_result=done,
                 on_finished=lambda: self.check_btn.setEnabled(True))

    # ------------------------------------------------------------ alta / edición
    def _submit(self):
        try:
            name = validate_printer_name(self.name.text())
            ip = validate_ipv4(self.ip.text())
            port = validate_port(self.port.value())
        except ValidationError as e:
            self.hint.set(str(e), "error")
            if os_key() != "windows" and " " in self.name.text():
                self.name.setText(suggest_cups_name(self.name.text()))
            return
        drv = self.driver.value()
        if self.mode == "create":
            if drv is None:
                self.hint.set("Seleccione un controlador compatible.", "error")
                return
            self._run_create(name, drv, ip, port, False, False)
        else:
            self._run_edit(name, drv, ip, port)

    def _run_create(self, name, drv, ip, port, skip_conn, confirmed):
        from app.printers.manager import PrinterManager
        self.ok_btn.setEnabled(False)
        self.hint.set("", "info")
        self.task.run(lambda ctx: PrinterManager().create(ctx, name, drv, ip, port, skip_conn, confirmed),
                      on_result=lambda r: self._created(r, name, drv, ip, port, skip_conn, confirmed),
                      on_error=lambda m: (self.hint.set(m, "error"), self.ok_btn.setEnabled(True)))

    def _show_steps(self, steps):
        while self.steps_lay.count():
            w = self.steps_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        p = theme.current()
        icon_for = {"ok": ("mdi6.check-circle", p.success), "error": ("mdi6.close-circle", p.danger),
                    "aviso": ("mdi6.alert-circle", p.warning), "pendiente": ("mdi6.timer-outline", p.muted)}
        for s in steps:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            ic = QLabel()
            name, col = icon_for.get(s.status, icon_for["pendiente"])
            ic.setPixmap(icons.pixmap(name, col, 18))
            h.addWidget(ic)
            t = QLabel(f"<b>{s.name}</b>" + (f" — <span style='color:{p.muted}'>{s.message}</span>" if s.message else ""))
            t.setWordWrap(True)
            h.addWidget(t, 1)
            self.steps_lay.addWidget(row)
        self.steps_box.show()

    def _created(self, r, name, drv, ip, port, skip_conn, confirmed):
        self._show_steps(r.steps)
        self.ok_btn.setEnabled(True)
        if r.ok:
            self.created_name = name
            self.hint.set(r.message + " Ahora puede imprimir una prueba.", "ok")
            self.ok_btn.setText("Listo")
            self.ok_btn.clicked.disconnect()
            self.ok_btn.clicked.connect(self.accept)
            self.test_btn.show()
            for w in (self.name, self.ip, self.port, self.driver):
                w.setEnabled(False)
            notify("ok", "Impresora creada", f"{name} · {ip}:{port}")
            bus.data_changed.emit("printers")
            return
        failed = next((s for s in r.steps if s.status == "error"), None)
        if failed and failed.name == "Comprobar conexión" and not skip_conn:
            if confirm(self, "Sin conexión con la impresora", r.message,
                       ok_text="Crear igualmente",
                       details="Puede crearla igualmente (por ejemplo, si la impresora está apagada) y "
                               "probarla más tarde."):
                self._run_create(name, drv, ip, port, True, confirmed)
                return
        if "confirmación del técnico" in r.message and not confirmed:
            if confirm(self, "Driver sin firma verificada", "El paquete no tiene una firma digital válida "
                       "o no se pudo verificar.", r.message, danger=True, ok_text="Instalar igualmente",
                       require_check="Confío en el origen de este driver"):
                self._run_create(name, drv, ip, port, skip_conn, True)
                return
        self.hint.set(r.message, "error")

    def _run_edit(self, name, drv, ip, port):
        from app.printers.manager import PrinterManager
        p = self.printer
        changes = []
        if drv is not None and (drv.label or drv.ref) != p.driver and drv.ref != p.driver:
            changes.append("driver")
        if ip != p.ip or port != (p.port or 9100):
            changes.append("address")
        if name != p.name:
            changes.append("name")
        if not changes:
            self.hint.set("No hay cambios.", "info")
            return
        self.ok_btn.setEnabled(False)

        def work(ctx):
            m = PrinterManager()
            out = []
            current = p.name
            if "address" in changes:
                ctx.progress(20, "Cambiando IP/puerto…")
                out.append(m.change_address(current, ip, port))
            if "driver" in changes:
                ctx.progress(50, "Cambiando controlador…")
                out.append(m.change_driver(current, drv))
            if "name" in changes:
                ctx.progress(80, "Renombrando…")
                r = m.rename(current, name)
                out.append(r)
            return out

        def done(results):
            self.ok_btn.setEnabled(True)
            ok = all(r.ok for r in results)
            txt = "\n".join(("✓ " if r.ok else "✗ ") + r.message + (f" ({r.detail})" if r.detail and not r.ok else "")
                            for r in results)
            self.hint.set(txt, "ok" if ok else "error")
            bus.data_changed.emit("printers")
            if ok:
                notify("ok", "Impresora actualizada", name)
                self.accept()
        self.task.run(work, on_result=done, on_error=lambda m: (self.hint.set(m, "error"), self.ok_btn.setEnabled(True)))

    def _test_print(self):
        run_test_print(self, self.created_name)


def _ok_ip(txt: str) -> bool:
    try:
        validate_ipv4(txt)
        return True
    except ValidationError:
        return False


def run_test_print(parent, name: str, mode: str = "escpos", ip: str = "", port: int = 9100):
    """Prueba de impresión con confirmación real del SO y del técnico."""
    from app.printers.manager import PrinterManager

    def work(ctx):
        ctx.progress(30, "Enviando trabajo…")
        m = PrinterManager()
        if mode == "direct":
            return m.test_print_direct(name, ip, port)
        return m.test_print_os(name, escpos_ticket=(mode == "escpos"))

    def done(r):
        if not r.ok:
            info_dialog(parent, "Prueba de impresión", r.message, r.detail, "error")
            return
        notify("info", "Trabajo entregado", r.message)
        from .widgets import confirm as _c
        printed = _c(parent, "Resultado físico", f"{r.message}<br><br>¿Salió el ticket/página impreso "
                     "correctamente?", ok_text="Sí, se imprimió")
        PrinterManager().record_physical_result(name, printed)
        notify("ok" if printed else "warning", "Resultado registrado",
               "Impresión confirmada por el técnico" if printed else "Registrado: no se imprimió correctamente")
    panel = getattr(parent, "task", None)
    if isinstance(panel, TaskPanel):
        panel.run(work, on_result=done)
    else:
        run_task(work, on_result=done, on_error=lambda m: notify("error", "Prueba de impresión", m))
