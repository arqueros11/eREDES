"""Análisis de paquetes de drivers.

Identifica tipo de paquete, sistema operativo, arquitectura, versión, fabricante
y nombres de controlador (cuando el formato los expone: INF y PPD).
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.platform_info import IS_MACOS, IS_WINDOWS
from app.core.shell import ps_quote, run, run_powershell

log = logging.getLogger("eredes.drivers.analyzers")

# Extensiones que se consideran "paquete instalable" (cada una tiene un adaptador)
PACKAGE_EXTENSIONS = {
    ".inf": "INF",
    ".exe": "EXE",
    ".msi": "MSI",
    ".pkg": "PKG",
    ".mpkg": "PKG",
    ".ppd": "PPD",
    ".dmg": "DMG",
    ".zip": "ZIP",
}
# .ppd.gz también es PPD
SUPPORT_EXTENSIONS = {".cat", ".dll", ".gpd", ".cab", ".sys", ".ini", ".dat", ".ntf", ".bud", ".gdl", ".ico",
                      ".pdf", ".txt", ".xml", ".icns", ".plist"}


@dataclass
class PackageInfo:
    path: Path
    pkg_type: str
    os: str = "desconocido"           # windows | macos | multiplataforma | desconocido
    arch: str = "desconocida"         # x64, x86, arm64, universal, "x64,x86"...
    version: str = ""
    provider: str = ""
    driver_names: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    signature_status: str = "no_verificada"  # valida | invalida | sin_firma | no_verificable | no_verificada
    signature_detail: str = ""
    notes: list[str] = field(default_factory=list)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def package_type(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".ppd.gz"):
        return "PPD"
    if path.is_dir() and name.endswith((".pkg", ".mpkg")):
        return "PKG"
    return PACKAGE_EXTENSIONS.get(path.suffix.lower())


def _path_hints(path: Path) -> tuple[str | None, str | None]:
    """Deduce OS/arquitectura a partir de nombres de carpeta (WIN64, x86, MAC...)."""
    parts = " ".join(p.lower() for p in path.parts[-5:])
    os_hint = None
    arch_hint = None
    if re.search(r"\b(win|windows|win10|win11|w10|w11)", parts):
        os_hint = "windows"
    if re.search(r"\b(mac|macos|osx|os_x|darwin)", parts):
        os_hint = "macos"
    if re.search(r"(x64|amd64|win64|64bit|64-bit)", parts):
        arch_hint = "x64"
    elif re.search(r"(arm64|aarch64)", parts):
        arch_hint = "arm64"
    elif re.search(r"(x86|win32|32bit|32-bit|i386)", parts):
        arch_hint = "x86"
    return os_hint, arch_hint


# ----------------------------------------------------------------- INF
def _read_text_any(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace")
    # UTF-16 sin BOM (heurística: muchos bytes nulos)
    if raw[:200].count(b"\x00") > 50:
        return raw.decode("utf-16-le", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def parse_inf_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        # quitar comentarios fuera de comillas
        if ";" in line and line.count('"') % 2 == 0:
            idx = _comment_index(line)
            if idx >= 0:
                line = line[:idx].strip()
        m = re.match(r"^\[(.+?)\]$", line)
        if m:
            current = m.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def _comment_index(line: str) -> int:
    in_q = False
    for i, c in enumerate(line):
        if c == '"':
            in_q = not in_q
        elif c == ";" and not in_q:
            return i
    return -1


def _kv(line: str) -> tuple[str, str] | None:
    if "=" not in line:
        return None
    k, v = line.split("=", 1)
    return k.strip(), v.strip()


def _resolve(value: str, strings: dict[str, str]) -> str:
    value = value.strip()
    def rep(m):
        return strings.get(m.group(1).lower(), m.group(0))
    value = re.sub(r"%([^%]+)%", rep, value)
    return value.strip().strip('"')


def analyze_inf(path: Path) -> PackageInfo:
    info = PackageInfo(path=path, pkg_type="INF", os="windows")
    try:
        text = _read_text_any(path)
    except OSError as e:
        info.notes.append(f"No se pudo leer: {e}")
        return info
    sec = parse_inf_sections(text)
    strings: dict[str, str] = {}
    for key in sec:
        if key == "strings" or key.startswith("strings."):
            if key != "strings" and strings:
                continue
            for line in sec[key]:
                kv = _kv(line)
                if kv:
                    strings[kv[0].lower()] = kv[1].strip().strip('"')
    version = {}
    for line in sec.get("version", []):
        kv = _kv(line)
        if kv:
            version[kv[0].lower()] = _resolve(kv[1], strings)
    cls = version.get("class", "")
    info.provider = version.get("provider", "")
    dv = version.get("driverver", "")
    if dv:
        parts = [p.strip() for p in dv.split(",")]
        info.version = parts[1] if len(parts) > 1 else parts[0]
        if len(parts) > 1:
            info.notes.append(f"Fecha del driver: {parts[0]}")
    catalog = version.get("catalogfile") or version.get("catalogfile.ntamd64") or version.get("catalogfile.nt")
    if cls and cls.lower() != "printer":
        info.notes.append(f"Clase de dispositivo: {cls} (no es 'Printer').")
    archs: set[str] = set()
    names: list[str] = []
    for line in sec.get("manufacturer", []):
        kv = _kv(line)
        if not kv:
            continue
        mfg_name = _resolve(kv[0], strings)
        if not info.provider and mfg_name:
            info.provider = mfg_name
        targets = [t.strip() for t in kv[1].split(",")]
        base = targets[0]
        decorations = targets[1:]
        model_sections = []
        if decorations:
            for d in decorations:
                dl = d.lower()
                if "amd64" in dl or "x64" in dl:
                    archs.add("x64")
                elif "arm64" in dl:
                    archs.add("arm64")
                elif "x86" in dl:
                    archs.add("x86")
                elif dl.startswith("nt"):
                    archs.add("x86")  # NT sin arquitectura = x86/genérico
                model_sections.append(f"{base}.{d}".lower())
        else:
            archs.add("x86")
        model_sections.append(base.lower())
        for ms in model_sections:
            for ml in sec.get(ms, []):
                kv2 = _kv(ml)
                if kv2:
                    n = _resolve(kv2[0], strings)
                    if n and n not in names:
                        names.append(n)
    info.driver_names = names
    info.arch = ",".join(sorted(archs)) if archs else "desconocida"
    if catalog:
        cat_path = path.parent / catalog
        if cat_path.exists():
            info.notes.append(f"Catálogo de firma: {catalog}")
        else:
            info.notes.append(f"Falta el catálogo de firma referenciado: {catalog}")
            info.signature_status = "invalida"
            info.signature_detail = f"El INF referencia {catalog} pero no existe en la carpeta."
    else:
        info.signature_status = "sin_firma"
        info.signature_detail = "El INF no declara CatalogFile (driver sin firma)."
    if not names:
        info.notes.append("No se encontraron modelos de impresora en el INF.")
    return info


# ----------------------------------------------------------------- PPD
def _read_ppd(path: Path) -> str:
    if path.name.lower().endswith(".gz"):
        with gzip.open(path, "rb") as f:
            return f.read().decode("latin-1", errors="replace")
    return path.read_bytes().decode("latin-1", errors="replace")


def analyze_ppd(path: Path) -> PackageInfo:
    info = PackageInfo(path=path, pkg_type="PPD", os="macos", arch="universal")
    try:
        text = _read_ppd(path)
    except OSError as e:
        info.notes.append(f"No se pudo leer: {e}")
        return info
    if not text.startswith("*PPD-Adobe"):
        info.notes.append("El archivo no comienza con *PPD-Adobe (¿PPD válido?).")

    def field_(name: str) -> str:
        m = re.search(rf'^\*{name}:\s*"?([^"\r\n]*)"?', text, re.M)
        return m.group(1).strip() if m else ""

    info.provider = field_("Manufacturer")
    info.version = field_("FileVersion")
    nick = field_("NickName") or field_("ModelName")
    if nick:
        info.driver_names = [nick]
    filters = re.findall(r'^\*cupsFilter2?:\s*"([^"]+)"', text, re.M)
    for f in filters:
        prog = f.split()[-1]
        if prog != "-":
            info.requirements.append(f"filtro:{prog}")
    info.signature_status = "no_aplica"
    info.signature_detail = "Los PPD son archivos de texto: se verifica integridad por SHA-256."
    return info


def missing_cups_filters(info: PackageInfo) -> list[str]:
    """Devuelve filtros CUPS requeridos por un PPD que no están instalados en este equipo."""
    missing = []
    dirs = [Path("/usr/libexec/cups/filter"), Path("/usr/lib/cups/filter"),
            Path("/Library/Printers")]
    for req in info.requirements:
        if not req.startswith("filtro:"):
            continue
        prog = req.split(":", 1)[1]
        if prog.startswith("/"):
            if not Path(prog).exists():
                missing.append(prog)
            continue
        found = any((d / prog).exists() for d in dirs[:2])
        if not found and dirs[2].exists():
            try:
                found = any(p.name == prog for p in dirs[2].rglob(prog))
            except OSError:
                found = False
        if not found:
            missing.append(prog)
    return missing


# ----------------------------------------------------------------- PKG / DMG / EXE / MSI
def analyze_pkg(path: Path) -> PackageInfo:
    info = PackageInfo(path=path, pkg_type="PKG", os="macos", arch="universal")
    if IS_MACOS:
        r = run(["pkgutil", "--check-signature", str(path)], timeout=30)
        txt = r.stdout.strip()
        m = re.search(r"Status:\s*(.+)", txt)
        status = m.group(1).strip().lower() if m else ""
        cert = re.search(r"^\s*1\.\s*(.+)$", txt, re.M)
        if "no signature" in status:
            info.signature_status = "sin_firma"
            info.signature_detail = "Paquete sin firma digital."
        elif status.startswith("signed") and not any(w in status for w in ("untrusted", "revoked", "expired", "invalid")):
            info.signature_status = "valida"
            info.signature_detail = cert.group(1).strip() if cert else status
        elif status:
            info.signature_status = "invalida"
            info.signature_detail = status
        else:
            info.signature_status = "no_verificada"
            info.signature_detail = txt[:400] or r.short_error()
        # versión desde Distribution / PackageInfo (sin instalar)
        r2 = run(["pkgutil", "--payload-files", str(path)], timeout=20)
        if r2.ok and "/Library/Printers" in r2.stdout:
            info.notes.append("Instala componentes en /Library/Printers (driver de impresora).")
    else:
        info.signature_status = "no_verificable"
        info.signature_detail = "La firma de un PKG solo puede verificarse en macOS."
    return info


def analyze_dmg(path: Path) -> PackageInfo:
    info = PackageInfo(path=path, pkg_type="DMG", os="macos", arch="universal")
    if IS_MACOS:
        r = run(["codesign", "-dv", "--verbose=2", str(path)], timeout=30)
        if r.ok or "Authority=" in r.stderr:
            auth = re.findall(r"Authority=(.+)", r.stderr)
            info.signature_status = "valida" if auth else "no_verificada"
            info.signature_detail = auth[0] if auth else r.stderr.strip()[:300]
        else:
            info.signature_status = "sin_firma"
            info.signature_detail = "Imagen DMG sin firma de código."
        info.notes.append("Abra el DMG y ejecute el instalador interno (PKG) desde la biblioteca.")
    else:
        info.signature_status = "no_verificable"
    return info


def _authenticode(path: Path) -> tuple[str, str]:
    if not IS_WINDOWS:
        return "no_verificable", "La firma Authenticode solo puede verificarse en Windows."
    script = (
        f"$s=Get-AuthenticodeSignature -LiteralPath {ps_quote(path)};"
        "$o=[pscustomobject]@{Status=[string]$s.Status;Signer=if($s.SignerCertificate){$s.SignerCertificate.Subject}else{''};Msg=$s.StatusMessage};"
        "$o|ConvertTo-Json -Compress"
    )
    r = run_powershell(script, timeout=60)
    if not r.ok:
        return "no_verificada", r.short_error()
    import json
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return "no_verificada", r.stdout[:300]
    st = d.get("Status", "")
    if st == "Valid":
        return "valida", d.get("Signer", "")
    if st == "NotSigned":
        return "sin_firma", "Archivo sin firma Authenticode."
    return "invalida", f"{st}: {d.get('Msg', '')}"


def _win_file_version(path: Path) -> str:
    if not IS_WINDOWS:
        return ""
    r = run_powershell(f"(Get-Item -LiteralPath {ps_quote(path)}).VersionInfo.ProductVersion", timeout=30)
    return r.stdout.strip() if r.ok else ""


def _pe_arch(path: Path) -> str:
    """Lee la cabecera PE para saber si el EXE es x86/x64/arm64 (válido en cualquier SO)."""
    try:
        with open(path, "rb") as f:
            mz = f.read(64)
            if mz[:2] != b"MZ":
                return "desconocida"
            off = int.from_bytes(mz[60:64], "little")
            f.seek(off)
            pe = f.read(6)
            if pe[:4] != b"PE\x00\x00":
                return "desconocida"
            machine = int.from_bytes(pe[4:6], "little")
            return {0x14c: "x86", 0x8664: "x64", 0xaa64: "arm64"}.get(machine, "desconocida")
    except OSError:
        return "desconocida"


def analyze_exe(path: Path) -> PackageInfo:
    info = PackageInfo(path=path, pkg_type="EXE", os="windows")
    info.arch = _pe_arch(path)
    if info.arch == "x86":
        info.notes.append("Instalador de 32 bits: en Windows x64 puede instalar drivers de 64 bits; "
                          "verifique tras la instalación.")
        info.arch = "x86,x64"
    info.signature_status, info.signature_detail = _authenticode(path)
    info.version = _win_file_version(path)
    info.notes.append("Instalador del fabricante: el nombre del driver se detecta tras instalarlo.")
    return info


def analyze_msi(path: Path) -> PackageInfo:
    info = PackageInfo(path=path, pkg_type="MSI", os="windows", arch="desconocida")
    info.signature_status, info.signature_detail = _authenticode(path)
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                info.notes.append("El archivo no tiene cabecera OLE de MSI.")
                info.signature_status = "invalida"
    except OSError:
        pass
    return info


def analyze(path: Path) -> PackageInfo | None:
    t = package_type(path)
    if t is None:
        return None
    try:
        if t == "INF":
            info = analyze_inf(path)
        elif t == "PPD":
            info = analyze_ppd(path)
        elif t == "PKG":
            info = analyze_pkg(path)
        elif t == "DMG":
            info = analyze_dmg(path)
        elif t == "EXE":
            info = analyze_exe(path)
        elif t == "MSI":
            info = analyze_msi(path)
        else:
            info = PackageInfo(path=path, pkg_type=t)
    except Exception as e:  # noqa: BLE001
        log.exception("Error analizando %s", path)
        info = PackageInfo(path=path, pkg_type=t, notes=[f"Error de análisis: {e}"])
    os_hint, arch_hint = _path_hints(path)
    if info.os == "desconocido" and os_hint:
        info.os = os_hint
    if info.arch in ("desconocida", "") and arch_hint:
        info.arch = arch_hint
    return info


def is_compatible(pkg_os: str, pkg_arch: str, cur_os: str, cur_arch: str) -> tuple[bool, str]:
    """Comprueba compatibilidad de un paquete con el SO/arquitectura actuales."""
    if pkg_os not in (cur_os, "multiplataforma"):
        if pkg_os == "desconocido":
            return False, "Sistema operativo del paquete desconocido: revise y edite los metadatos."
        return False, f"Paquete para {pkg_os}; este equipo es {cur_os}."
    archs = {a.strip() for a in (pkg_arch or "").split(",") if a.strip()}
    if not archs or archs & {"universal", "desconocida", "any"}:
        if "desconocida" in archs:
            return True, "Arquitectura no declarada en el paquete (se intentará; verifique)."
        return True, "Compatible."
    if cur_arch in archs:
        return True, "Compatible."
    if cur_os == "macos":
        return True, "macOS: el instalador resolverá la arquitectura (Rosetta/Universal)."
    if cur_os == "windows" and cur_arch == "arm64" and "x64" in archs:
        return False, "Driver x64 en Windows ARM64: los drivers de impresora deben ser nativos ARM64."
    return False, f"Arquitectura del paquete ({pkg_arch}) incompatible con este equipo ({cur_arch})."
