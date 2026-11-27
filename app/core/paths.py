"""Rutas de la aplicación.

Reglas:
  * Variable de entorno EREDES_HOME: tiene prioridad siempre.
  * Desarrollo: la carpeta raíz del proyecto (donde está main.py).
  * Ejecutable Windows (PyInstaller): carpeta donde está eREDES.exe
    (así DRIVERS/ queda junto al ejecutable y el técnico puede copiar drivers).
  * App macOS (.app): ~/Documents/eREDES (no se escribe dentro del bundle).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "eREDES"
APP_SUBTITLE = "PRINTER & NETWORK MANAGER"
APP_VERSION = "1.0.1"
VERSION_PREFIX = "ERMA.V"


def version_label(v: str | None = None) -> str:
    """Nombre visible de la versión: ERMA.V1.0.0"""
    return f"{VERSION_PREFIX}{v or APP_VERSION}"

DEFAULT_MANUFACTURERS = ["EPSON", "BIENEX", "POS-D", "XPRINTER", "CBX", "OTRAS_MARCAS"]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """Recursos de solo lectura empaquetados (iconos, oui, perfiles)."""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "app" / "resources"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / "resources"


def home_dir() -> Path:
    env = os.environ.get("EREDES_HOME")
    if env:
        p = Path(env).expanduser()
    elif is_frozen():
        if sys.platform == "darwin":
            p = Path.home() / "Documents" / APP_NAME
        else:
            p = Path(sys.executable).resolve().parent
    else:
        p = Path(__file__).resolve().parent.parent.parent
    p.mkdir(parents=True, exist_ok=True)
    return p


def drivers_dir() -> Path:
    d = home_dir() / "DRIVERS"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    d = home_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = home_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    d = home_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def backups_dir() -> Path:
    d = data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def trash_dir() -> Path:
    d = data_dir() / "papelera_drivers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "eredes.db"


def ensure_layout() -> None:
    """Crea la estructura mínima (incluye las 5 marcas iniciales + OTRAS_MARCAS)."""
    drv = drivers_dir()
    for m in DEFAULT_MANUFACTURERS:
        (drv / m).mkdir(exist_ok=True)
    for f in (data_dir, logs_dir, reports_dir, backups_dir):
        f()
