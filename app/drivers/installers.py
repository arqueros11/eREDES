"""Adaptadores de instalación de drivers con validaciones previas.

Antes de instalar se comprueba SIEMPRE:
  1. Que el paquete esté dentro de la biblioteca DRIVERS/ (no se ejecutan archivos externos).
  2. Integridad: el SHA-256 actual coincide con el registrado al importarlo.
  3. Compatibilidad con el SO y la arquitectura del equipo.
  4. Firma digital (Authenticode en Windows, pkgutil en macOS) cuando el formato lo permite.
     Un paquete sin firma o con firma inválida requiere confirmación explícita del técnico.
  5. Requisitos (por ejemplo, filtros CUPS de un PPD).
La instalación se delega al adaptador del sistema operativo (pnputil/Add-PrinterDriver,
msiexec, installer -pkg...).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.core.platform_info import arch_key, os_key
from app.database.db import get_db
from app.platform import printer_backend
from app.platform.base import DriverRef, OpResult

from . import analyzers
from .library import DriverLibrary

log = logging.getLogger("eredes.drivers.install")

SUPPORTED = {
    "windows": {"INF", "EXE", "MSI"},
    "macos": {"PPD", "PKG", "DMG"},
    "linux": {"PPD"},
}


@dataclass
class PreCheck:
    ok: bool
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    needs_confirmation: bool = False


def precheck(lib: DriverLibrary, pkg_id: int) -> PreCheck:
    pc = PreCheck(ok=True)
    row = lib.package(pkg_id)
    if not row:
        return PreCheck(False, ["El paquete no existe en la biblioteca."])
    try:
        f = lib.abs(row["rel_path"])
    except Exception as e:  # noqa: BLE001
        return PreCheck(False, [str(e)])
    if not f.exists():
        pc.blocking.append("El archivo ya no existe en DRIVERS/. Sincronice la biblioteca.")
    else:
        h = lib._hash(f)
        if h != row["sha256"]:
            pc.blocking.append("El archivo fue modificado desde que se importó (SHA-256 distinto). "
                               "Sincronice la biblioteca para volver a analizarlo antes de instalarlo.")
        else:
            pc.info.append(f"Integridad verificada (SHA-256 {h[:16]}…).")
    cur_os = os_key()
    if row["pkg_type"] not in SUPPORTED.get(cur_os, set()):
        pc.blocking.append(f"Los paquetes {row['pkg_type']} no se instalan en {cur_os}.")
    ok, why = analyzers.is_compatible(row["os"], row["arch"], cur_os, arch_key())
    (pc.info if ok else pc.blocking).append(why)
    sig = row["signature_status"]
    if sig == "valida":
        pc.info.append(f"Firma digital válida: {row['signature_detail']}")
    elif sig in ("sin_firma", "invalida"):
        pc.warnings.append(f"Firma digital {sig.replace('_', ' ')}: {row['signature_detail']}")
        pc.needs_confirmation = True
    elif sig in ("no_verificada", "no_verificable"):
        pc.warnings.append(f"No se pudo verificar la firma digital ({row['signature_detail'] or sig}).")
        pc.needs_confirmation = row["pkg_type"] in ("EXE", "MSI", "PKG", "DMG")
    if row["pkg_type"] == "PPD" and f.exists():
        info = analyzers.analyze_ppd(f)
        missing = analyzers.missing_cups_filters(info)
        if missing:
            pc.blocking.append("El PPD requiere filtros CUPS que no están instalados: " + ", ".join(missing)
                               + ". Instale primero el paquete (PKG) del fabricante.")
    if row["pkg_type"] == "INF" and not json.loads(row["driver_names"] or "[]"):
        pc.blocking.append("El INF no declara modelos de impresora instalables.")
    if row["pkg_type"] in ("EXE", "MSI", "PKG", "DMG"):
        pc.warnings.append("Se ejecutará el instalador del fabricante con permisos de administrador.")
        pc.needs_confirmation = True
    pc.ok = not pc.blocking
    return pc


def install_package(lib: DriverLibrary, pkg_id: int, confirmed: bool = False,
                    driver_names: list[str] | None = None) -> OpResult:
    pc = precheck(lib, pkg_id)
    row = lib.package(pkg_id)
    target = f"{row['manufacturer']}/{row['model']}/{row['filename']}" if row else str(pkg_id)
    if not pc.ok:
        get_db().log_operation("drivers", "Instalar driver", target, "BLOQUEADO", " | ".join(pc.blocking))
        return OpResult(False, "Instalación bloqueada por las validaciones.", "\n".join(pc.blocking))
    if pc.needs_confirmation and not confirmed:
        return OpResult(False, "Se requiere confirmación del técnico.", "\n".join(pc.warnings),
                        data={"needs_confirmation": True})
    names = driver_names or json.loads(row["driver_names"] or "[]")
    f = lib.abs(row["rel_path"])
    res = printer_backend().install_driver_package(str(f), row["pkg_type"], names)
    installed = res.data.get("installed") or []
    if res.ok:
        lib.mark_installed(pkg_id, installed or None)
    get_db().log_operation("drivers", "Instalar driver", target, "OK" if res.ok else "ERROR",
                           res.message + ("\n" + res.detail if res.detail else ""))
    return res


def driver_ref_for_package(lib: DriverLibrary, pkg_id: int, driver_name: str | None = None) -> DriverRef | None:
    """Traduce un paquete de la biblioteca al controlador que usa el SO al crear la impresora."""
    row = lib.package(pkg_id)
    if not row:
        return None
    names = json.loads(row["driver_names"] or "[]")
    if row["pkg_type"] == "PPD":
        return DriverRef("ppd_file", str(lib.abs(row["rel_path"])), label=names[0] if names else row["filename"],
                         package_id=pkg_id)
    if os_key() == "windows":
        name = driver_name or (names[0] if names else "")
        if not name:
            return None
        return DriverRef("installed", name, label=name, package_id=pkg_id)
    # macOS PKG/DMG: los PPD nuevos se registran tras instalar
    if driver_name:
        kind = "ppd_file" if driver_name.startswith("/") else "model"
        return DriverRef(kind, driver_name, label=Path(driver_name).name, package_id=pkg_id)
    return None
