"""Biblioteca local de drivers (carpeta física DRIVERS/).

Estructura dinámica:  DRIVERS/<FABRICANTE>/<MODELO>/<archivos o subcarpetas>
  * Los fabricantes y modelos NO están codificados: se leen de las carpetas.
  * Archivos colocados directamente en DRIVERS/<FABRICANTE>/ se asignan al
    modelo "_GENERAL".
  * Las carpetas que empiezan por '.' o '_' (excepto _GENERAL) se ignoran.

La BD (tablas manufacturers, models, driver_packages) es un índice de la carpeta:
`sync()` reconcilia ambos. Si un archivo cambia, su hash cambia y se vuelve a analizar.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core import paths
from app.core.platform_info import arch_key, os_key
from app.core.validation import ValidationError, validate_folder_name
from app.database.db import Database, get_db, now

from . import analyzers

log = logging.getLogger("eredes.drivers")

GENERAL_MODEL = "_GENERAL"
MAX_ZIP_TOTAL = 2 * 1024 ** 3      # 2 GB descomprimido
MAX_ZIP_FILES = 20000
INF_SUPPORT_TYPES = {"INF"}


@dataclass
class SyncReport:
    manufacturers: int = 0
    models: int = 0
    packages: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    errors: list[str] | None = None


def _skip(name: str) -> bool:
    return (name.startswith(".") or name.startswith("_")) and name != GENERAL_MODEL


class DriverLibrary:
    def __init__(self, root: Path | None = None, db: Database | None = None):
        self.root = Path(root or paths.drivers_dir())
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = db or get_db()

    # ------------------------------------------------------------ utilidades
    def rel(self, p: Path) -> str:
        return Path(p).resolve().relative_to(self.root.resolve()).as_posix()

    def abs(self, rel_path: str) -> Path:
        p = (self.root / rel_path).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValidationError("Ruta fuera de la biblioteca de drivers.")
        return p

    def _inside(self, p: Path) -> bool:
        try:
            Path(p).resolve().relative_to(self.root.resolve())
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------ sincronización
    def sync(self, progress=None) -> SyncReport:
        """Lee DRIVERS/ y actualiza la BD. Solo re-analiza archivos nuevos o modificados."""
        rep = SyncReport(errors=[])
        db = self.db
        seen_mfr: set[int] = set()
        seen_models: set[int] = set()
        seen_pkgs: set[str] = set()
        mfr_dirs = sorted([d for d in self.root.iterdir() if d.is_dir() and not _skip(d.name)],
                          key=lambda d: d.name.upper())
        total = max(1, len(mfr_dirs))
        for i, mdir in enumerate(mfr_dirs):
            if progress:
                progress(int(i * 100 / total), f"Leyendo {mdir.name}…")
            mid = self._ensure_manufacturer(mdir.name)
            seen_mfr.add(mid)
            model_dirs = [d for d in mdir.iterdir() if d.is_dir() and not _skip(d.name)]
            entries: dict[str, list[Path]] = {d.name: self._package_files(d) for d in model_dirs}
            loose = [f for f in mdir.iterdir() if f.is_file() and not f.name.startswith(".")
                     and analyzers.package_type(f) not in (None, "ZIP")]
            if loose:
                entries.setdefault(GENERAL_MODEL, []).extend(loose)
            for model_name, files in entries.items():
                moid = self._ensure_model(mid, model_name, f"{mdir.name}/{model_name}")
                seen_models.add(moid)
                for f in files:
                    try:
                        relp = self.rel(f)
                        seen_pkgs.add(relp)
                        changed = self._index_package(moid, f, relp)
                        if changed == "added":
                            rep.added += 1
                        elif changed == "updated":
                            rep.updated += 1
                    except Exception as e:  # noqa: BLE001
                        rep.errors.append(f"{f}: {e}")
                        log.exception("Error indexando %s", f)
        # eliminar del índice lo que ya no existe en disco
        for row in db.query("SELECT id, rel_path FROM driver_packages"):
            if row["rel_path"] not in seen_pkgs:
                db.execute("DELETE FROM driver_packages WHERE id=?", (row["id"],))
                rep.removed += 1
        for row in db.query("SELECT id FROM models"):
            if row["id"] not in seen_models:
                db.execute("DELETE FROM models WHERE id=?", (row["id"],))
        for row in db.query("SELECT id FROM manufacturers"):
            if row["id"] not in seen_mfr:
                db.execute("DELETE FROM manufacturers WHERE id=?", (row["id"],))
        rep.manufacturers = db.scalar("SELECT COUNT(*) FROM manufacturers")
        rep.models = db.scalar("SELECT COUNT(*) FROM models")
        rep.packages = db.scalar("SELECT COUNT(*) FROM driver_packages")
        if progress:
            progress(100, "Biblioteca sincronizada")
        return rep

    def _package_files(self, folder: Path) -> list[Path]:
        """Devuelve los archivos instalables de un modelo (recursivo).

        Dentro de un .pkg (bundle de carpeta) no se profundiza.
        """
        out: list[Path] = []
        if not folder.exists():
            return out
        for dirpath, dirnames, filenames in os.walk(folder):
            dp = Path(dirpath)
            keep_dirs = []
            for d in dirnames:
                if d.startswith("."):
                    continue
                if d.lower().endswith((".pkg", ".mpkg")):
                    out.append(dp / d)
                    continue
                if d.lower().endswith(".app"):
                    continue
                keep_dirs.append(d)
            dirnames[:] = keep_dirs
            for fn in filenames:
                if fn.startswith("."):
                    continue
                f = dp / fn
                t = analyzers.package_type(f)
                if t and t != "ZIP":
                    out.append(f)
        return sorted(out)

    def _ensure_manufacturer(self, name: str) -> int:
        r = self.db.one("SELECT id FROM manufacturers WHERE name=?", (name,))
        if r:
            self.db.execute("UPDATE manufacturers SET folder=? WHERE id=?", (name, r["id"]))
            return r["id"]
        cur = self.db.execute("INSERT INTO manufacturers(name,folder,created_at) VALUES(?,?,?)",
                              (name, name, now()))
        return cur.lastrowid

    def _ensure_model(self, mid: int, name: str, folder: str) -> int:
        r = self.db.one("SELECT id FROM models WHERE manufacturer_id=? AND name=?", (mid, name))
        if r:
            self.db.execute("UPDATE models SET folder=? WHERE id=?", (folder, r["id"]))
            return r["id"]
        cur = self.db.execute("INSERT INTO models(manufacturer_id,name,folder,created_at) VALUES(?,?,?,?)",
                              (mid, name, folder, now()))
        return cur.lastrowid

    def _size(self, p: Path) -> int:
        if p.is_dir():
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        return p.stat().st_size

    def _hash(self, p: Path) -> str:
        if p.is_dir():  # bundle .pkg: hash de todos los archivos
            import hashlib
            h = hashlib.sha256()
            for f in sorted(x for x in p.rglob("*") if x.is_file()):
                h.update(f.relative_to(p).as_posix().encode())
                h.update(analyzers.sha256_file(f).encode())
            return h.hexdigest()
        return analyzers.sha256_file(p)

    def _index_package(self, model_id: int, f: Path, relp: str) -> str:
        size = self._size(f)
        existing = self.db.one("SELECT * FROM driver_packages WHERE rel_path=?", (relp,))
        if existing and existing["size"] == size and existing["model_id"] == model_id:
            mtime = datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
            if mtime <= existing["updated_at"]:
                return "same"
        digest = self._hash(f)
        if existing and existing["sha256"] == digest and existing["model_id"] == model_id:
            self.db.execute("UPDATE driver_packages SET updated_at=? WHERE id=?", (now(), existing["id"]))
            return "same"
        info = analyzers.analyze(f)
        if info is None:
            return "skip"
        meta = self._read_meta_override(f)
        os_ = meta.get("os", info.os)
        arch = meta.get("arch", info.arch)
        version = meta.get("version", info.version)
        names = meta.get("driver_names", info.driver_names)
        reqs = "; ".join(info.requirements + info.notes)
        values = (model_id, relp, f.name, info.pkg_type, os_, arch, version, info.provider,
                  json.dumps(names, ensure_ascii=False), digest, size, info.signature_status,
                  info.signature_detail, reqs, now())
        if existing:
            self.db.execute(
                "UPDATE driver_packages SET model_id=?, rel_path=?, filename=?, pkg_type=?, os=?, arch=?, version=?,"
                " provider=?, driver_names=?, sha256=?, size=?, signature_status=?, signature_detail=?,"
                " requirements=?, updated_at=? WHERE id=?", values + (existing["id"],))
            return "updated"
        self.db.execute(
            "INSERT INTO driver_packages(model_id,rel_path,filename,pkg_type,os,arch,version,provider,driver_names,"
            "sha256,size,signature_status,signature_detail,requirements,updated_at,imported_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values + (now(),))
        return "added"

    # metadatos editados por el técnico: <archivo>.eredes.json
    def _meta_file(self, f: Path) -> Path:
        return f.parent / f".{f.name}.eredes.json"

    def _read_meta_override(self, f: Path) -> dict:
        mf = self._meta_file(f)
        if mf.exists():
            try:
                return json.loads(mf.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                return {}
        return {}

    def update_package_metadata(self, pkg_id: int, *, os_: str | None = None, arch: str | None = None,
                                version: str | None = None, driver_names: list[str] | None = None) -> None:
        row = self.package(pkg_id)
        if not row:
            raise ValidationError("Paquete no encontrado.")
        f = self.abs(row["rel_path"])
        meta = self._read_meta_override(f)
        if os_ is not None:
            meta["os"] = os_
        if arch is not None:
            meta["arch"] = arch
        if version is not None:
            meta["version"] = version
        if driver_names is not None:
            meta["driver_names"] = driver_names
        self._meta_file(f).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self.db.execute(
            "UPDATE driver_packages SET os=?, arch=?, version=?, driver_names=?, updated_at=? WHERE id=?",
            (meta.get("os", row["os"]), meta.get("arch", row["arch"]), meta.get("version", row["version"]),
             json.dumps(meta.get("driver_names", json.loads(row["driver_names"] or "[]")), ensure_ascii=False),
             now(), pkg_id))

    # ------------------------------------------------------------ consultas
    def manufacturers(self):
        return self.db.query(
            "SELECT m.*, (SELECT COUNT(*) FROM models mo WHERE mo.manufacturer_id=m.id) AS n_models,"
            " (SELECT COUNT(*) FROM driver_packages p JOIN models mo ON p.model_id=mo.id"
            "  WHERE mo.manufacturer_id=m.id) AS n_packages FROM manufacturers m ORDER BY m.name")

    def models(self, manufacturer_id: int):
        return self.db.query(
            "SELECT mo.*, (SELECT COUNT(*) FROM driver_packages p WHERE p.model_id=mo.id) AS n_packages"
            " FROM models mo WHERE manufacturer_id=? ORDER BY mo.name", (manufacturer_id,))

    def packages(self, model_id: int | None = None, search: str = ""):
        sql = ("SELECT p.*, mo.name AS model, m.name AS manufacturer FROM driver_packages p"
               " JOIN models mo ON p.model_id=mo.id JOIN manufacturers m ON mo.manufacturer_id=m.id WHERE 1=1")
        params: list = []
        if model_id is not None:
            sql += " AND p.model_id=?"
            params.append(model_id)
        if search:
            like = f"%{search}%"
            sql += (" AND (p.filename LIKE ? OR p.driver_names LIKE ? OR mo.name LIKE ? OR m.name LIKE ?"
                    " OR p.provider LIKE ? OR p.version LIKE ?)")
            params += [like] * 6
        sql += " ORDER BY m.name, mo.name, p.filename"
        return self.db.query(sql, params)

    def package(self, pkg_id: int):
        return self.db.one(
            "SELECT p.*, mo.name AS model, m.name AS manufacturer FROM driver_packages p"
            " JOIN models mo ON p.model_id=mo.id JOIN manufacturers m ON mo.manufacturer_id=m.id"
            " WHERE p.id=?", (pkg_id,))

    def compatible_packages(self, model_id: int):
        """Paquetes del modelo con la evaluación de compatibilidad con este equipo."""
        out = []
        for p in self.packages(model_id):
            ok, why = analyzers.is_compatible(p["os"], p["arch"], os_key(), arch_key())
            out.append((p, ok, why))
        return out

    def duplicates(self) -> list[list]:
        groups = []
        for r in self.db.query("SELECT sha256, COUNT(*) c FROM driver_packages GROUP BY sha256 HAVING c>1"):
            groups.append(self.db.query(
                "SELECT p.*, mo.name AS model, m.name AS manufacturer FROM driver_packages p"
                " JOIN models mo ON p.model_id=mo.id JOIN manufacturers m ON mo.manufacturer_id=m.id"
                " WHERE sha256=?", (r["sha256"],)))
        return groups

    def verify_integrity(self, pkg_id: int | None = None, progress=None) -> list[tuple]:
        """Recalcula SHA-256 y compara con el registrado. Devuelve [(row, estado, detalle)]."""
        rows = [self.package(pkg_id)] if pkg_id else self.packages()
        res = []
        for i, r in enumerate(rows):
            if r is None:
                continue
            if progress:
                progress(int(i * 100 / max(1, len(rows))), r["filename"])
            f = self.abs(r["rel_path"])
            if not f.exists():
                res.append((r, "falta", "El archivo ya no existe en disco."))
                continue
            h = self._hash(f)
            if h == r["sha256"]:
                res.append((r, "ok", "Hash SHA-256 coincide."))
            else:
                res.append((r, "modificado", f"Hash actual {h[:16]}… ≠ registrado {r['sha256'][:16]}…"))
        return res

    def reverify_signature(self, pkg_id: int) -> tuple[str, str]:
        r = self.package(pkg_id)
        f = self.abs(r["rel_path"])
        info = analyzers.analyze(f)
        if info:
            self.db.execute("UPDATE driver_packages SET signature_status=?, signature_detail=? WHERE id=?",
                            (info.signature_status, info.signature_detail, pkg_id))
            return info.signature_status, info.signature_detail
        return "no_verificada", ""

    # ------------------------------------------------------------ fabricantes
    def add_manufacturer(self, name: str, notes: str = "") -> int:
        name = validate_folder_name(name, "nombre del fabricante").upper()
        d = self.root / name
        if d.exists() or self.db.one("SELECT 1 FROM manufacturers WHERE name=?", (name,)):
            raise ValidationError(f"El fabricante {name} ya existe.")
        d.mkdir()
        mid = self._ensure_manufacturer(name)
        self.db.execute("UPDATE manufacturers SET notes=? WHERE id=?", (notes, mid))
        return mid

    def rename_manufacturer(self, mid: int, new_name: str, notes: str | None = None,
                            limitations: str | None = None) -> None:
        row = self.db.one("SELECT * FROM manufacturers WHERE id=?", (mid,))
        if not row:
            raise ValidationError("Fabricante no encontrado.")
        new_name = validate_folder_name(new_name, "nombre del fabricante").upper()
        if new_name != row["name"]:
            if (self.root / new_name).exists():
                raise ValidationError(f"Ya existe la carpeta {new_name}.")
            (self.root / row["folder"]).rename(self.root / new_name)
            old_prefix = row["folder"] + "/"
            for p in self.db.query("SELECT id, rel_path FROM driver_packages WHERE rel_path LIKE ?",
                                   (old_prefix + "%",)):
                self.db.execute("UPDATE driver_packages SET rel_path=? WHERE id=?",
                                (new_name + "/" + p["rel_path"][len(old_prefix):], p["id"]))
            for mo in self.db.query("SELECT id, folder FROM models WHERE manufacturer_id=?", (mid,)):
                self.db.execute("UPDATE models SET folder=? WHERE id=?",
                                (new_name + "/" + mo["folder"].split("/", 1)[-1], mo["id"]))
            self.db.execute("UPDATE manufacturers SET name=?, folder=? WHERE id=?", (new_name, new_name, mid))
        if notes is not None:
            self.db.execute("UPDATE manufacturers SET notes=? WHERE id=?", (notes, mid))
        if limitations is not None:
            self.db.execute("UPDATE manufacturers SET limitations=? WHERE id=?", (limitations, mid))

    def manufacturer_dependencies(self, mid: int) -> list[str]:
        return [r["name"] for r in self.db.query(
            "SELECT pr.name FROM printers pr JOIN driver_packages p ON pr.driver_package_id=p.id"
            " JOIN models mo ON p.model_id=mo.id WHERE mo.manufacturer_id=?", (mid,))]

    def model_dependencies(self, model_id: int) -> list[str]:
        return [r["name"] for r in self.db.query(
            "SELECT pr.name FROM printers pr JOIN driver_packages p ON pr.driver_package_id=p.id"
            " WHERE p.model_id=?", (model_id,))]

    def _to_trash(self, p: Path) -> Path:
        dest = paths.trash_dir() / f"{datetime.now():%Y%m%d_%H%M%S}_{p.name}"
        shutil.move(str(p), str(dest))
        return dest

    def delete_manufacturer(self, mid: int) -> Path:
        row = self.db.one("SELECT * FROM manufacturers WHERE id=?", (mid,))
        if not row:
            raise ValidationError("Fabricante no encontrado.")
        deps = self.manufacturer_dependencies(mid)
        if deps:
            raise ValidationError("No se puede eliminar: impresoras activas usan sus drivers: " + ", ".join(deps))
        dest = self._to_trash(self.root / row["folder"])
        self.db.execute("DELETE FROM manufacturers WHERE id=?", (mid,))
        return dest

    # ------------------------------------------------------------ modelos
    def add_model(self, mid: int, name: str, notes: str = "") -> int:
        mrow = self.db.one("SELECT * FROM manufacturers WHERE id=?", (mid,))
        if not mrow:
            raise ValidationError("Fabricante no encontrado.")
        name = validate_folder_name(name, "nombre del modelo")
        d = self.root / mrow["folder"] / name
        if d.exists():
            raise ValidationError(f"El modelo {name} ya existe.")
        d.mkdir(parents=True)
        moid = self._ensure_model(mid, name, self.rel(d))
        self.db.execute("UPDATE models SET notes=? WHERE id=?", (notes, moid))
        return moid

    def rename_model(self, model_id: int, new_name: str, notes: str | None = None) -> None:
        row = self.db.one("SELECT * FROM models WHERE id=?", (model_id,))
        if not row:
            raise ValidationError("Modelo no encontrado.")
        new_name = validate_folder_name(new_name, "nombre del modelo")
        if new_name != row["name"]:
            old = self.root / row["folder"]
            new = old.parent / new_name
            if new.exists():
                raise ValidationError(f"Ya existe el modelo {new_name}.")
            if old.exists():
                old.rename(new)
            else:
                new.mkdir(parents=True)
            old_prefix = row["folder"] + "/"
            new_folder = self.rel(new)
            for p in self.db.query("SELECT id, rel_path FROM driver_packages WHERE model_id=?", (model_id,)):
                if p["rel_path"].startswith(old_prefix):
                    self.db.execute("UPDATE driver_packages SET rel_path=? WHERE id=?",
                                    (new_folder + "/" + p["rel_path"][len(old_prefix):], p["id"]))
            self.db.execute("UPDATE models SET name=?, folder=? WHERE id=?", (new_name, new_folder, model_id))
        if notes is not None:
            self.db.execute("UPDATE models SET notes=? WHERE id=?", (notes, model_id))

    def delete_model(self, model_id: int) -> Path | None:
        row = self.db.one("SELECT * FROM models WHERE id=?", (model_id,))
        if not row:
            raise ValidationError("Modelo no encontrado.")
        deps = self.model_dependencies(model_id)
        if deps:
            raise ValidationError("No se puede eliminar: impresoras activas usan este modelo: " + ", ".join(deps))
        d = self.root / row["folder"]
        dest = self._to_trash(d) if d.exists() else None
        self.db.execute("DELETE FROM models WHERE id=?", (model_id,))
        return dest

    def model_dir(self, model_id: int) -> Path:
        row = self.db.one("SELECT folder FROM models WHERE id=?", (model_id,))
        if not row:
            raise ValidationError("Modelo no encontrado.")
        d = self.root / row["folder"]
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------ paquetes
    def delete_package(self, pkg_id: int) -> Path:
        r = self.package(pkg_id)
        if not r:
            raise ValidationError("Paquete no encontrado.")
        used = self.db.query("SELECT name FROM printers WHERE driver_package_id=?", (pkg_id,))
        if used:
            raise ValidationError("Paquete en uso por: " + ", ".join(u["name"] for u in used))
        f = self.abs(r["rel_path"])
        # para INF se mueve la carpeta completa solo si contiene únicamente ese INF
        target = f
        if r["pkg_type"] == "INF":
            infs = list(f.parent.glob("*.inf")) + list(f.parent.glob("*.INF"))
            if len(set(infs)) == 1 and f.parent != self.abs(self.db.one(
                    "SELECT folder FROM models WHERE id=?", (r["model_id"],))["folder"]):
                target = f.parent
        dest = self._to_trash(target)
        mf = self._meta_file(f)
        if mf.exists():
            mf.unlink()
        self.db.execute("DELETE FROM driver_packages WHERE id=?", (pkg_id,))
        return dest

    def import_into_model(self, model_id: int, source: Path, progress=None) -> list[Path]:
        """Importa un archivo, carpeta o ZIP dentro del modelo. Devuelve rutas creadas."""
        source = Path(source)
        if not source.exists():
            raise ValidationError(f"No existe: {source}")
        dest_root = self.model_dir(model_id)
        if self._inside(source):
            raise ValidationError("El origen ya está dentro de la biblioteca.")
        created: list[Path] = []
        if source.is_dir() and not source.name.lower().endswith((".pkg", ".mpkg")):
            dest = self._unique(dest_root / source.name)
            shutil.copytree(source, dest, symlinks=False,
                            ignore=shutil.ignore_patterns(".DS_Store", "__MACOSX"))
            created.append(dest)
        elif source.suffix.lower() == ".zip":
            created.append(self._extract_zip(source, dest_root, progress))
        else:
            if analyzers.package_type(source) is None:
                raise ValidationError(
                    f"Tipo de archivo no admitido: {source.suffix or source.name}. "
                    "Formatos: INF, EXE, MSI, PKG, PPD, DMG, ZIP o carpetas.")
            dest = self._unique(dest_root / source.name)
            if source.is_dir():
                shutil.copytree(source, dest)
            else:
                shutil.copy2(source, dest)
            created.append(dest)
        return created

    def update_package_file(self, pkg_id: int, new_file: Path) -> Path:
        """Reemplaza el archivo de un paquete por una nueva versión (el anterior va a la papelera)."""
        r = self.package(pkg_id)
        if not r:
            raise ValidationError("Paquete no encontrado.")
        new_file = Path(new_file)
        if analyzers.package_type(new_file) != r["pkg_type"]:
            raise ValidationError(f"La nueva versión debe ser del mismo tipo ({r['pkg_type']}).")
        f = self.abs(r["rel_path"])
        self._to_trash(f)
        if new_file.is_dir():
            shutil.copytree(new_file, f)
        else:
            shutil.copy2(new_file, f)
        os.utime(f, None)
        return f

    def _unique(self, p: Path) -> Path:
        if not p.exists():
            return p
        i = 2
        while True:
            cand = p.with_name(f"{p.stem}_{i}{p.suffix}")
            if not cand.exists():
                return cand
            i += 1

    def _extract_zip(self, zpath: Path, dest_root: Path, progress=None) -> Path:
        dest = self._unique(dest_root / zpath.stem)
        try:
            zf = zipfile.ZipFile(zpath)
        except zipfile.BadZipFile:
            raise ValidationError("El ZIP está dañado o no es un ZIP válido.")
        with zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_FILES:
                raise ValidationError("El ZIP contiene demasiados archivos.")
            total = sum(i.file_size for i in infos)
            if total > MAX_ZIP_TOTAL:
                raise ValidationError("El contenido descomprimido supera 2 GB.")
            bad = zf.testzip()
            if bad:
                raise ValidationError(f"Archivo corrupto dentro del ZIP: {bad}")
            dest.mkdir(parents=True)
            base = dest.resolve()
            for n, info in enumerate(infos):
                name = info.filename
                if name.startswith("__MACOSX/") or name.endswith(".DS_Store"):
                    continue
                target = (dest / name).resolve()
                if not str(target).startswith(str(base) + os.sep) and target != base:
                    shutil.rmtree(dest, ignore_errors=True)
                    raise ValidationError(f"Ruta peligrosa dentro del ZIP (zip-slip): {name}")
                # no extraer enlaces simbólicos
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    continue
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                if progress and n % 20 == 0:
                    progress(int(n * 100 / max(1, len(infos))), f"Extrayendo {Path(name).name}")
        # ZIP dentro de ZIP: extraer también (un nivel)
        for inner in list(dest.rglob("*.zip")):
            try:
                self._extract_zip(inner, inner.parent, None)
                inner.unlink()
            except ValidationError as e:
                log.warning("ZIP interno no extraído %s: %s", inner, e)
        return dest

    def mark_installed(self, pkg_id: int, driver_names: list[str] | None = None) -> None:
        self.db.execute("UPDATE driver_packages SET installed=1 WHERE id=?", (pkg_id,))
        if driver_names:
            r = self.package(pkg_id)
            current = json.loads(r["driver_names"] or "[]")
            merged = current + [n for n in driver_names if n not in current]
            self.update_package_metadata(pkg_id, driver_names=merged)
