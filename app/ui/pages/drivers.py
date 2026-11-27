"""Biblioteca local de drivers: Fabricante → Modelo → Paquetes."""
from __future__ import annotations

import json

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLineEdit,
                               QListWidget, QListWidgetItem, QMenu)

from app.core import paths
from app.core.validation import ValidationError

from .. import theme
from ..bus import bus, notify
from ..widgets import Card, TaskPanel, button, confirm, fill_table, info_dialog, make_table, selected_data
from .base import BasePage

SIG_LABEL = {"valida": "Válida", "invalida": "Inválida", "sin_firma": "Sin firma", "no_verificable": "No verificable",
             "no_verificada": "No verificada", "no_aplica": "N/A (hash)"}


def _lib():
    from app.drivers.library import DriverLibrary
    return DriverLibrary()


class DriversPage(BasePage):
    key = "drivers"
    title = "Drivers"

    def __init__(self):
        super().__init__()
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar driver, modelo o fabricante…")
        self.search.setMinimumHeight(40)
        self.search.returnPressed.connect(self.do_search)
        bar.addWidget(self.search, 1)
        bar.addWidget(button("Sincronizar", "", "mdi6.refresh", self.sync, "Leer de nuevo la carpeta DRIVERS"))
        bar.addWidget(button("Abrir carpeta", "", "mdi6.folder-open-outline",
                             lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.drivers_dir())))))
        tools = button("Herramientas", "", "mdi6.shield-check")
        m = QMenu(tools)
        m.addAction("Verificar integridad (SHA-256)", self.verify_all)
        m.addAction("Buscar duplicados", self.duplicates)
        tools.setMenu(m)
        bar.addWidget(tools)
        self.root.addLayout(bar)

        cols = QHBoxLayout()
        cols.setSpacing(14)
        # fabricantes
        mc = Card("Fabricantes", icon_name="mdi6.factory")
        self.mfr = QListWidget()
        self.mfr.setObjectName("Master")
        self.mfr.currentItemChanged.connect(lambda *_: self.load_models())
        mc.add(self.mfr, 1)
        mb = QHBoxLayout()
        mb.addWidget(button("", "", "mdi6.plus", self.add_mfr, "Añadir fabricante"))
        mb.addWidget(button("", "", "mdi6.pencil-outline", self.edit_mfr, "Editar fabricante"))
        mb.addWidget(button("", "", "mdi6.delete-outline", self.del_mfr, "Eliminar fabricante"))
        mc.add(mb)
        mc.setMaximumWidth(270)
        cols.addWidget(mc, 2)
        # modelos
        oc = Card("Modelos", icon_name="mdi6.chip")
        self.models = QListWidget()
        self.models.setObjectName("Master")
        self.models.currentItemChanged.connect(lambda *_: self.load_packages())
        oc.add(self.models, 1)
        ob = QHBoxLayout()
        ob.addWidget(button("", "", "mdi6.plus", self.add_model, "Añadir modelo"))
        ob.addWidget(button("", "", "mdi6.pencil-outline", self.edit_model, "Renombrar modelo"))
        ob.addWidget(button("", "", "mdi6.delete-outline", self.del_model, "Eliminar modelo"))
        oc.add(ob)
        oc.setMaximumWidth(270)
        cols.addWidget(oc, 2)
        # paquetes
        pc = Card("Paquetes", icon_name="mdi6.package-variant-closed")
        self.pk = make_table(["Archivo", "Tipo", "Sistema", "Arquitectura", "Versión", "Firma", "Instalado",
                              "Drivers"], stretch_col=7)
        self.pk.doubleClicked.connect(lambda _i: self.details())
        pc.add(self.pk, 1)
        pb = QHBoxLayout()
        pb.setSpacing(8)
        imp = button("Importar", "Primary", "mdi6.download")
        im = QMenu(imp)
        im.addAction("Archivo (INF, EXE, MSI, PKG, PPD, DMG)", self.import_file)
        im.addAction("Archivo ZIP", self.import_zip)
        im.addAction("Carpeta completa", self.import_folder)
        imp.setMenu(im)
        pb.addWidget(imp)
        pb.addWidget(button("Instalar", "", "mdi6.play", self.install))
        pb.addWidget(button("Detalles", "", "mdi6.eye-outline", self.details))
        more = button("Más", "", "mdi6.cog-outline")
        mm = QMenu(more)
        mm.addAction("Editar metadatos (SO, arquitectura, versión)", self.edit_meta)
        mm.addAction("Actualizar a nueva versión…", self.update_pkg)
        mm.addAction("Verificar integridad y firma", self.verify_one)
        more.setMenu(mm)
        pb.addWidget(more)
        pb.addStretch(1)
        pb.addWidget(button("Eliminar", "Danger", "mdi6.delete-outline", self.del_pkg))
        pc.add(pb)
        cols.addWidget(pc, 6)
        self.root.addLayout(cols, 1)
        self.task = TaskPanel()
        self.root.addWidget(self.task)

    def first_show(self):
        self.sync()

    # ------------------------------------------------------------ carga
    def sync(self):
        def done(rep):
            self.load_mfrs()
            if rep.added or rep.updated or rep.removed:
                notify("ok", "Biblioteca sincronizada", f"+{rep.added} · ~{rep.updated} · -{rep.removed}")
            if rep.errors:
                info_dialog(self, "Errores al leer DRIVERS", f"{len(rep.errors)} archivos con error", "\n".join(rep.errors),
                            "warning")
        self.task.run(lambda ctx: _lib().sync(ctx.progress), on_result=done)

    def load_mfrs(self):
        cur = self.mfr.currentItem().data(256) if self.mfr.currentItem() else None
        self.mfr.blockSignals(True)
        self.mfr.clear()
        for m in _lib().manufacturers():
            it = QListWidgetItem(f"{m['name']}   {m['n_packages']}")
            it.setData(256, m["id"])
            it.setToolTip(m["notes"] or m["name"])
            self.mfr.addItem(it)
            if m["id"] == cur:
                self.mfr.setCurrentItem(it)
        self.mfr.blockSignals(False)
        if not self.mfr.currentItem() and self.mfr.count():
            self.mfr.setCurrentRow(0)
        else:
            self.load_models()

    def _mid(self):
        it = self.mfr.currentItem()
        return it.data(256) if it else None

    def _moid(self):
        it = self.models.currentItem()
        return it.data(256) if it else None

    def load_models(self):
        mid = self._mid()
        cur = self._moid()
        self.models.blockSignals(True)
        self.models.clear()
        if mid:
            for mo in _lib().models(mid):
                it = QListWidgetItem(f"{mo['name']}   {mo['n_packages']}")
                it.setData(256, mo["id"])
                self.models.addItem(it)
                if mo["id"] == cur:
                    self.models.setCurrentItem(it)
        self.models.blockSignals(False)
        if not self.models.currentItem() and self.models.count():
            self.models.setCurrentRow(0)
        else:
            self.load_packages()

    def load_packages(self, rows=None):
        moid = self._moid()
        if rows is None:
            rows = _lib().packages(moid) if moid else []
        pal = theme.current()
        data = [[r["filename"], r["pkg_type"], r["os"], r["arch"], r["version"],
                 SIG_LABEL.get(r["signature_status"], r["signature_status"]), "Sí" if r["installed"] else "",
                 ", ".join(json.loads(r["driver_names"] or "[]"))[:120]] for r in rows]
        fill_table(self.pk, data, data=[r["id"] for r in rows], colors={
            5: lambda s: pal.success if s == "Válida" else (pal.danger if s in ("Inválida", "Sin firma") else None),
            6: lambda s: pal.success if s == "Sí" else None})

    def do_search(self):
        q = self.search.text().strip()
        if not q:
            self.load_packages()
            return
        rows = _lib().packages(search=q)
        self.load_packages(rows)
        notify("info", f"{len(rows)} resultados", q)

    # ------------------------------------------------------------ fabricantes / modelos
    def _ask(self, title, label, text=""):
        val, ok = QInputDialog.getText(self, title, label, text=text)
        return val.strip() if ok and val.strip() else None

    def _try(self, fn, *a, ok_msg=""):
        try:
            r = fn(*a)
            if ok_msg:
                notify("ok", ok_msg)
            return r
        except (ValidationError, OSError) as e:
            notify("error", "No se pudo completar", str(e))
            return None

    def add_mfr(self):
        name = self._ask("Nuevo fabricante", "Nombre:")
        if name and self._try(_lib().add_manufacturer, name, ok_msg=f"Fabricante {name.upper()} creado"):
            self.load_mfrs()

    def edit_mfr(self):
        mid = self._mid()
        if not mid:
            return
        lib = _lib()
        row = lib.db.one("SELECT * FROM manufacturers WHERE id=?", (mid,))
        d = QDialog(self)
        d.setWindowTitle("Editar fabricante")
        f = QFormLayout(d)
        name = QLineEdit(row["name"])
        notes = QLineEdit(row["notes"])
        lim = QLineEdit(row["limitations"])
        f.addRow("Nombre", name)
        f.addRow("Notas", notes)
        f.addRow("Limitaciones", lim)
        b = button("Guardar", "Primary", None, d.accept)
        f.addRow(b)
        if d.exec():
            self._try(lib.rename_manufacturer, mid, name.text(), notes.text(), lim.text(), ok_msg="Guardado")
            self.load_mfrs()

    def del_mfr(self):
        mid = self._mid()
        if not mid:
            return
        name = self.mfr.currentItem().text().split()[0]
        if confirm(self, f"Eliminar {name}", "La carpeta se moverá a la papelera de eREDES.", danger=True,
                   ok_text="Eliminar"):
            if self._try(_lib().delete_manufacturer, mid, ok_msg=f"{name} eliminado"):
                self.load_mfrs()

    def add_model(self):
        mid = self._mid()
        if not mid:
            notify("info", "Seleccione un fabricante")
            return
        name = self._ask("Nuevo modelo", "Modelo:")
        if name and self._try(_lib().add_model, mid, name, ok_msg=f"Modelo {name} creado"):
            self.load_models()

    def edit_model(self):
        moid = self._moid()
        if not moid:
            return
        name = self._ask("Renombrar modelo", "Modelo:", self.models.currentItem().text().split("   ")[0])
        if name and self._try(_lib().rename_model, moid, name, ok_msg="Modelo renombrado") is not False:
            self.load_models()

    def del_model(self):
        moid = self._moid()
        if moid and confirm(self, "Eliminar modelo", "La carpeta se moverá a la papelera de eREDES.", danger=True,
                            ok_text="Eliminar"):
            self._try(_lib().delete_model, moid, ok_msg="Modelo eliminado")
            self.load_models()

    # ------------------------------------------------------------ importación
    def _import(self, src):
        moid = self._moid()
        if not moid:
            notify("info", "Seleccione un modelo")
            return

        def work(ctx):
            lib = _lib()
            created = lib.import_into_model(moid, src, ctx.progress)
            lib.sync(ctx.progress)
            return created

        def done(created):
            self.load_mfrs()
            notify("ok", "Importado", ", ".join(c.name for c in created))
            bus.data_changed.emit("drivers")
        self.task.run(work, on_result=done)

    def import_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Importar driver", "",
                                           "Drivers (*.inf *.exe *.msi *.pkg *.mpkg *.ppd *.gz *.dmg);;Todos (*)")
        if f:
            self._import(f)

    def import_zip(self):
        f, _ = QFileDialog.getOpenFileName(self, "Importar ZIP", "", "ZIP (*.zip)")
        if f:
            self._import(f)

    def import_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Importar carpeta de drivers")
        if d:
            self._import(d)

    # ------------------------------------------------------------ paquetes
    def _pid(self):
        pid = selected_data(self.pk)
        if pid is None:
            notify("info", "Seleccione un paquete")
        return pid

    def details(self):
        pid = self._pid()
        if pid is None:
            return
        from app.drivers.installers import precheck
        lib = _lib()
        r = lib.package(pid)
        pc = precheck(lib, pid)
        txt = (f"Fabricante: {r['manufacturer']}\nModelo: {r['model']}\nArchivo: {r['rel_path']}\n"
               f"Tipo: {r['pkg_type']}  ·  SO: {r['os']}  ·  Arquitectura: {r['arch']}\nVersión: {r['version']}\n"
               f"Proveedor: {r['provider']}\nSHA-256: {r['sha256']}\nTamaño: {r['size']:,} bytes\n"
               f"Firma: {SIG_LABEL.get(r['signature_status'])} — {r['signature_detail']}\n"
               f"Drivers: {', '.join(json.loads(r['driver_names'] or '[]')) or '—'}\n"
               f"Notas: {r['requirements'] or '—'}\n\n"
               + "\n".join("✗ " + x for x in pc.blocking) + ("\n" if pc.blocking else "")
               + "\n".join("! " + x for x in pc.warnings) + ("\n" if pc.warnings else "")
               + "\n".join("✓ " + x for x in pc.info))
        info_dialog(self, r["filename"], "Listo para instalar" if pc.ok else "No instalable en este equipo", txt,
                    "ok" if pc.ok else "error")

    def install(self, confirmed=False):
        pid = self._pid()
        if pid is None:
            return
        from app.drivers.installers import install_package
        lib = _lib()
        names = json.loads(lib.package(pid)["driver_names"] or "[]")
        chosen = None
        if len(names) > 1:
            n, ok = QInputDialog.getItem(self, "Modelo del INF", "Controlador a instalar:", names, 0, False)
            if not ok:
                return
            chosen = [n]

        def done(r):
            if r.ok:
                notify("ok", "Driver instalado", r.message)
                self.load_packages()
            elif r.data.get("needs_confirmation"):
                if confirm(self, "Confirmar instalación", "Revise los avisos antes de continuar.", r.detail,
                           danger=True, ok_text="Instalar", require_check="Confío en el origen de este driver"):
                    self.task.run(lambda _c: install_package(_lib(), pid, True, chosen), on_result=done)
            else:
                info_dialog(self, "Instalación", r.message, r.detail, "error")
        self.task.run(lambda _c: install_package(lib, pid, confirmed, chosen), on_result=done)

    def del_pkg(self):
        pid = self._pid()
        if pid is not None and confirm(self, "Eliminar paquete", "Se moverá a la papelera de eREDES.", danger=True,
                                       ok_text="Eliminar"):
            if self._try(_lib().delete_package, pid, ok_msg="Paquete eliminado"):
                self.load_mfrs()

    def edit_meta(self):
        pid = self._pid()
        if pid is None:
            return
        lib = _lib()
        r = lib.package(pid)
        d = QDialog(self)
        d.setWindowTitle("Metadatos del paquete")
        d.setMinimumWidth(460)
        f = QFormLayout(d)
        os_ = QComboBox()
        os_.addItems(["windows", "macos", "multiplataforma", "desconocido"])
        os_.setCurrentText(r["os"])
        arch = QLineEdit(r["arch"])
        arch.setToolTip("x64, x86, arm64, universal (separadas por coma)")
        ver = QLineEdit(r["version"])
        names = QLineEdit(", ".join(json.loads(r["driver_names"] or "[]")))
        names.setToolTip("Nombre exacto del controlador en el sistema (separados por coma)")
        f.addRow("Sistema", os_)
        f.addRow("Arquitectura", arch)
        f.addRow("Versión", ver)
        f.addRow("Drivers", names)
        f.addRow(button("Guardar", "Primary", None, d.accept))
        if d.exec():
            self._try(lambda: lib.update_package_metadata(
                pid, os_=os_.currentText(), arch=arch.text().strip(), version=ver.text().strip(),
                driver_names=[x.strip() for x in names.text().split(",") if x.strip()]), ok_msg="Metadatos guardados")
            self.load_packages()

    def update_pkg(self):
        pid = self._pid()
        if pid is None:
            return
        f, _ = QFileDialog.getOpenFileName(self, "Nueva versión del paquete")
        if f and confirm(self, "Actualizar paquete", "La versión actual irá a la papelera de eREDES.",
                         ok_text="Actualizar"):
            if self._try(_lib().update_package_file, pid, f, ok_msg="Paquete actualizado"):
                self.sync()

    def verify_one(self):
        pid = self._pid()
        if pid is None:
            return

        def work(_c):
            lib = _lib()
            integ = lib.verify_integrity(pid)
            sig = lib.reverify_signature(pid)
            return integ, sig

        def done(res):
            integ, sig = res
            _, st, det = integ[0]
            info_dialog(self, "Verificación", f"Integridad: {st.upper()} · Firma: {SIG_LABEL.get(sig[0], sig[0])}",
                        f"{det}\n{sig[1]}", "ok" if st == "ok" else "error")
            self.load_packages()
        self.task.run(work, on_result=done)

    def verify_all(self):
        def done(res):
            bad = [(r, st, d) for r, st, d in res if st != "ok"]
            info_dialog(self, "Integridad de la biblioteca",
                        f"{len(res) - len(bad)} correctos · {len(bad)} con problemas",
                        "\n".join(f"{r['manufacturer']}/{r['model']}/{r['filename']}: {st} — {d}" for r, st, d in bad),
                        "ok" if not bad else "warning")
        self.task.run(lambda ctx: _lib().verify_integrity(progress=ctx.progress), on_result=done)

    def duplicates(self):
        groups = _lib().duplicates()
        if not groups:
            notify("ok", "Sin duplicados")
            return
        txt = "\n\n".join("\n".join(f"{r['manufacturer']}/{r['model']}/{r['rel_path']}" for r in g) for g in groups)
        info_dialog(self, "Archivos duplicados", f"{len(groups)} grupos con el mismo contenido (SHA-256)", txt,
                    "warning")

