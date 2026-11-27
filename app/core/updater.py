"""Actualización de eREDES desde GitHub Releases.

Flujo:
  1. check()    -> consulta https://api.github.com/repos/<repo>/releases/latest
                   y compara la etiqueta (v1.2.3) con APP_VERSION.
  2. download() -> descarga el ZIP de este sistema (eREDES-Windows.zip / eREDES-macOS.zip),
                   comprueba tamaño y, si la release publica <zip>.sha256, el hash.
  3. install()  -> prepara un pequeño script que espera a que eREDES se cierre, reemplaza
                   los archivos del programa (NUNCA toca DRIVERS, data, logs ni reports)
                   y vuelve a abrir la app.
Solo funciona en la app empaquetada (.exe / .app). En desarrollo se indica usar git pull.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import config, paths
from .platform_info import IS_MACOS, IS_WINDOWS

log = logging.getLogger("eredes.updater")

API = "https://api.github.com/repos/{repo}/releases/latest"
USER_DATA = {"DRIVERS", "data", "logs", "reports"}   # nunca se reemplazan


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    current: str
    latest: str
    available: bool
    notes: str = ""
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    sha256_url: str = ""
    page_url: str = ""


def _ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def _parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "eREDES-updater"}
    token = (config.get("github_token") or "").strip()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def asset_suffix() -> str:
    return "Windows" if IS_WINDOWS else ("macOS" if IS_MACOS else "Linux")


def check(repo: str | None = None, timeout: float = 10) -> UpdateInfo:
    repo = (repo or config.get("github_repo") or "").strip().strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise UpdateError("Configure el repositorio de GitHub en Ajustes (formato usuario/repositorio).")
    req = urllib.request.Request(API.format(repo=repo), headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError(f"No hay versiones publicadas en {repo} (o el repositorio es privado).")
        if e.code in (401, 403):
            raise UpdateError("GitHub rechazó el acceso (repositorio privado o límite de consultas). "
                              "Añada un token en Ajustes.")
        raise UpdateError(f"GitHub respondió HTTP {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"Sin conexión con GitHub: {getattr(e, 'reason', e)}")
    latest = data.get("tag_name", "")
    info = UpdateInfo(current=paths.APP_VERSION, latest=re.sub(r"^[^\d]*", "", latest),
                      available=_parse_version(latest) > _parse_version(paths.APP_VERSION),
                      notes=(data.get("body") or "").strip()[:3000], page_url=data.get("html_url", ""))
    suffix = asset_suffix()
    for a in data.get("assets", []):
        name = a.get("name", "")
        if name.endswith(".zip") and suffix.lower() in name.lower():
            info.asset_name, info.asset_url, info.asset_size = name, a["browser_download_url"], int(a.get("size", 0))
        elif name.endswith(".sha256") and suffix.lower() in name.lower():
            info.sha256_url = a["browser_download_url"]
    if info.available and not info.asset_url:
        raise UpdateError(f"La versión {info.latest} no incluye el paquete para {suffix}.")
    return info


def download(info: UpdateInfo, progress=None, cancelled=lambda: False) -> Path:
    dest_dir = Path(tempfile.mkdtemp(prefix="eredes_update_"))
    dest = dest_dir / info.asset_name
    req = urllib.request.Request(info.asset_url, headers={"User-Agent": "eREDES-updater",
                                                          "Accept": "application/octet-stream"})
    h = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=60, context=_ctx()) as r, open(dest, "wb") as out:
        total = int(r.headers.get("Content-Length") or info.asset_size or 0)
        while True:
            if cancelled():
                raise UpdateError("Descarga cancelada.")
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(int(done * 100 / total), f"Descargando {done / 1e6:.1f} / {total / 1e6:.1f} MB")
    if info.asset_size and done != info.asset_size:
        raise UpdateError(f"Descarga incompleta ({done} de {info.asset_size} bytes).")
    if info.sha256_url:
        with urllib.request.urlopen(urllib.request.Request(info.sha256_url, headers={"User-Agent": "eREDES"}),
                                    timeout=20, context=_ctx()) as r:
            expected = r.read().decode().split()[0].strip().lower()
        if expected != h.hexdigest():
            raise UpdateError("El archivo descargado no coincide con su SHA-256 publicado. No se instala.")
    try:
        with zipfile.ZipFile(dest) as z:
            if z.testzip():
                raise UpdateError("El ZIP descargado está dañado.")
    except zipfile.BadZipFile:
        raise UpdateError("El archivo descargado no es un ZIP válido.")
    return dest


def install_dir() -> Path:
    """Carpeta que se reemplaza: la del .exe (Windows) o el bundle .app (macOS)."""
    exe = Path(sys.executable).resolve()
    if IS_MACOS:
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
        raise UpdateError("No se encontró el paquete .app.")
    return exe.parent


def can_self_update() -> bool:
    return paths.is_frozen() and (IS_WINDOWS or IS_MACOS)


def _extract(zip_path: Path) -> Path:
    out = zip_path.parent / "nuevo"
    out.mkdir()
    if IS_MACOS:
        # ditto conserva permisos, enlaces y firma del bundle
        r = subprocess.run(["ditto", "-x", "-k", str(zip_path), str(out)], capture_output=True)
        if r.returncode != 0:
            raise UpdateError("No se pudo descomprimir la actualización.")
        apps = list(out.glob("*.app"))
        if not apps:
            raise UpdateError("La actualización no contiene eREDES.app.")
        return apps[0]
    with zipfile.ZipFile(zip_path) as z:
        base = out.resolve()
        for m in z.infolist():
            target = (out / m.filename).resolve()
            if not str(target).startswith(str(base)):
                raise UpdateError("Ruta peligrosa dentro del ZIP.")
        z.extractall(out)
    exes = list(out.rglob("eREDES.exe"))
    if not exes:
        raise UpdateError("La actualización no contiene eREDES.exe.")
    return exes[0].parent


def install(zip_path: Path) -> None:
    """Lanza el script de reemplazo y devuelve; la app debe cerrarse justo después."""
    if not can_self_update():
        raise UpdateError("La actualización automática solo funciona en la app instalada (.exe / .app).")
    new = _extract(zip_path)
    target = install_dir()
    pid = os.getpid()
    if IS_WINDOWS:
        excl = " ".join(f'"{d}"' for d in USER_DATA)
        script = zip_path.parent / "actualizar.cmd"
        script.write_text(
            "@echo off\r\n"
            f":wait\r\ntasklist /FI \"PID eq {pid}\" | find \"{pid}\" >nul && (timeout /t 1 /nobreak >nul & goto wait)\r\n"
            f"robocopy \"{new}\" \"{target}\" /E /R:3 /W:1 /XD {excl} /NFL /NDL /NJH /NJS >nul\r\n"
            f"start \"\" \"{target / 'eREDES.exe'}\"\r\n",
            encoding="ascii", errors="ignore")
        subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=0x08000000 | 0x00000008)  # sin ventana, detached
    else:
        backup = target.with_name(target.name + ".anterior")
        script = zip_path.parent / "actualizar.sh"
        script.write_text(
            "#!/bin/bash\n"
            f"while kill -0 {pid} 2>/dev/null; do sleep 0.5; done\n"
            f"rm -rf \"{backup}\"\n"
            f"mv \"{target}\" \"{backup}\" && mv \"{new}\" \"{target}\" || mv \"{backup}\" \"{target}\"\n"
            f"xattr -dr com.apple.quarantine \"{target}\" 2>/dev/null\n"
            f"rm -rf \"{backup}\"\n"
            f"open \"{target}\"\n")
        script.chmod(0o755)
        subprocess.Popen(["/bin/bash", str(script)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log.info("Actualización preparada: %s -> %s", new, target)


def cleanup_old() -> None:
    """Borra temporales de actualizaciones anteriores."""
    tmp = Path(tempfile.gettempdir())
    for d in tmp.glob("eredes_update_*"):
        shutil.rmtree(d, ignore_errors=True)
