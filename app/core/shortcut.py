"""Acceso directo "eREDES" en el Escritorio de Windows, creado por la propia app al abrirse.

Un .lnk guardado dentro del ZIP no serviría: apunta a una ruta fija y se rompe si el ZIP se
descomprime en otra carpeta. Por eso la app lo crea la primera vez que se abre desde cada carpeta.
Si el técnico lo borra, no se vuelve a crear (salvo que el programa se mueva a otra carpeta).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from .platform_info import IS_WINDOWS

log = logging.getLogger("eredes.shortcut")


def _script(exe: str) -> str:
    from .shell import ps_quote
    folder = str(Path(exe).parent)
    return (
        "$u=[Environment]::GetFolderPath('Desktop');"
        "$c=[Environment]::GetFolderPath('CommonDesktopDirectory');"
        # si ya hay uno en el Escritorio del usuario, se actualiza ese; si no, se crea en el
        # Escritorio público (eREDES se abre como administrador y así siempre lo ve el usuario)
        "$l=Join-Path $u 'eREDES.lnk'; if(-not (Test-Path $l)){ $l=Join-Path $c 'eREDES.lnk' };"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($l);"
        f"$s.TargetPath={ps_quote(exe)}; $s.WorkingDirectory={ps_quote(folder)};"
        f"$s.IconLocation={ps_quote(exe + ',0')}; $s.Description='eREDES - Printer & Network Manager';"
        "$s.Save(); if(Test-Path $l){'OK ' + $l}"
    )


def ensure_desktop_shortcut(force: bool = False) -> str | None:
    """Crea o corrige el acceso directo. Devuelve la ruta del .lnk si lo creó."""
    if not (IS_WINDOWS and getattr(sys, "frozen", False)):
        return None
    from . import config
    from .shell import run_powershell
    exe = sys.executable
    if not force and config.get("desktop_shortcut_for") == exe:
        return None
    r = run_powershell(_script(exe), timeout=60)
    out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if out.startswith("OK "):
        config.set("desktop_shortcut_for", exe)
        log.info("Acceso directo del Escritorio: %s", out[3:])
        return out[3:]
    log.warning("No se pudo crear el acceso directo: %s", r.short_error())
    return None
