"""Adaptador de impresoras para macOS (CUPS: lpadmin, lpstat, lp, lpinfo)."""
from __future__ import annotations

import glob
import logging
import os
import re
import shlex
import tempfile
import time
from pathlib import Path

from app.core.shell import run, run_admin_macos
from app.platform.base import (DriverRef, OpResult, PrinterBackend, PrintJob, SystemDriver,
                               SystemPrinter)

log = logging.getLogger("eredes.macos.printers")

C_ENV = {**os.environ, "LANG": "C", "LC_ALL": "C"}

URI_RE = re.compile(r"^(?P<scheme>[a-z]+)://(?P<host>[^:/]+)(?::(?P<port>\d+))?", re.I)


def parse_uri(uri: str) -> tuple[str, int | None]:
    m = URI_RE.match(uri or "")
    if not m:
        return "", None
    port = int(m.group("port")) if m.group("port") else None
    if port is None:
        port = {"socket": 9100, "lpd": 515, "ipp": 631, "ipps": 631, "http": 631}.get(m.group("scheme").lower())
    return m.group("host"), port


BUILTIN_PPD_NAME = "eREDES ESC/POS Generic RAW (texto y datos directos)"


def builtin_escpos_ppd() -> Path:
    from app.core.paths import resource_dir
    return resource_dir() / "ppd" / "eREDES-ESCPOS-RAW.ppd"


def _run(args, timeout=30):
    return run(args, timeout=timeout, env=C_ENV)


def _ids(text: str) -> set[str]:
    return {line.split()[0] for line in text.splitlines() if line.strip()}


class MacPrinterBackend(PrinterBackend):
    os_name = "macos"

    # ------------------------------------------------------------ consulta
    def list_printers(self) -> list[SystemPrinter]:
        """CUPS en macOS localiza los textos de lpstat (ignora LANG), por eso se usan
        únicamente salidas estructuradas: `lpstat -e` (nombres) y `lpoptions -p` (atributos IPP)."""
        r = _run(["lpstat", "-e"])
        names = [n.strip() for n in r.stdout.splitlines() if n.strip()] if r.ok else []
        if not names:
            return []
        counts: dict[str, int] = {}
        for j in self.list_jobs():
            counts[j.printer] = counts.get(j.printer, 0) + 1
        out = []
        for name in names:
            o = self._options(name)
            uri = o.get("device-uri", "")
            ip, port = parse_uri(uri)
            state = o.get("printer-state", "")
            reasons = o.get("printer-state-reasons", "none")
            accepting = o.get("printer-is-accepting-jobs", "true") == "true"
            status = {"3": "Inactiva (lista)", "4": "Imprimiendo", "5": "Detenida"}.get(state, "Desconocido")
            if not accepting:
                status += " · no acepta trabajos"
            p = SystemPrinter(name=name, uri=uri, port_name=uri, ip=ip, port=port, os="macOS",
                              driver=o.get("printer-make-and-model", ""), status=status,
                              enabled=state != "5" and accepting, jobs=counts.get(name, 0),
                              shared=o.get("printer-is-shared", "false") == "true")
            p.extra["info"] = o.get("printer-info", "")
            p.extra["state"] = state
            p.extra["state_reasons"] = reasons
            if reasons and reasons != "none":
                p.extra["reason"] = reasons
            out.append(p)
        return sorted(out, key=lambda x: x.name.lower())

    def _options(self, name: str) -> dict:
        r = _run(["lpoptions", "-p", name])
        out: dict[str, str] = {}
        if not r.ok:
            return out
        try:
            tokens = shlex.split(r.stdout.strip())
        except ValueError:
            tokens = r.stdout.split()
        for t in tokens:
            if "=" in t:
                k, v = t.split("=", 1)
                out[k] = v
        return out

    def get_printer(self, name: str) -> SystemPrinter | None:
        for p in self.list_printers():
            if p.name.lower() == name.lower():
                return p
        return None

    def list_drivers(self) -> list[SystemDriver]:
        r = _run(["lpinfo", "-m"], timeout=90)
        out: list[SystemDriver] = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(" ", 1)
            ref = parts[0]
            desc = parts[1] if len(parts) > 1 else ref
            if ref in ("everywhere", "raw"):
                # IPP Everywhere requiere ipp://; las colas raw ya no se admiten en macOS 13+
                continue
            out.append(SystemDriver(name=desc, ref=ref, manufacturer=desc.split()[0] if desc else "", kind="model"))
        builtin = builtin_escpos_ppd()
        if builtin.exists():
            out.insert(0, SystemDriver(name=BUILTIN_PPD_NAME, ref=str(builtin), manufacturer="eREDES",
                                       kind="ppd_file"))
        return out

    # ------------------------------------------------------------ alta/baja/cambios
    def _driver_args(self, driver: DriverRef) -> list[str]:
        if driver.kind == "ppd_file":
            if not Path(driver.ref).exists():
                raise FileNotFoundError(f"No existe el PPD {driver.ref}")
            return ["-P", driver.ref]
        if driver.kind == "raw":
            return ["-m", "raw"]
        return ["-m", driver.ref]

    def _lpadmin(self, args: list[str], admin_prompt: str) -> tuple[bool, str]:
        r = _run(["lpadmin"] + args, timeout=60)
        if r.ok:
            return True, (r.stderr or r.stdout).strip()
        err = r.short_error()
        if "forbidden" in err.lower() or "not authorized" in err.lower() or "permission" in err.lower():
            r2 = run_admin_macos(["/usr/sbin/lpadmin"] + args, prompt=admin_prompt)
            if r2.ok:
                return True, r2.stdout.strip()
            return False, r2.short_error()
        return False, err

    def create_printer(self, name: str, driver: DriverRef, ip: str, port: int) -> OpResult:
        uri = f"socket://{ip}:{port}"
        try:
            dargs = self._driver_args(driver)
        except FileNotFoundError as e:
            return OpResult(False, str(e))
        args = ["-p", name, "-E", "-v", uri, *dargs, "-D", name, "-o", "printer-is-shared=false"]
        ok, msg = self._lpadmin(args, f"eREDES necesita permisos para crear la impresora {name}.")
        if not ok:
            return OpResult(False, "CUPS rechazó la creación de la impresora.", msg)
        # Confirmación real: la cola debe aparecer en lpstat con la URI correcta
        p = self.get_printer(name)
        if not p or p.uri != uri:
            return OpResult(False, "La impresora no aparece en CUPS tras crearla.", msg)
        warn = f" Aviso de CUPS: {msg}" if msg else ""
        return OpResult(True, f"Impresora {name} registrada en CUPS ({uri}).{warn}", msg,
                        data={"uri": uri})

    def remove_printer(self, name: str) -> OpResult:
        ok, msg = self._lpadmin(["-x", name], f"eREDES necesita permisos para eliminar {name}.")
        if not ok:
            return OpResult(False, f"No se pudo eliminar {name}.", msg)
        if self.get_printer(name):
            return OpResult(False, f"{name} sigue presente en CUPS.", msg)
        return OpResult(True, f"Impresora {name} eliminada del sistema.")

    def set_address(self, name: str, ip: str, port: int) -> OpResult:
        uri = f"socket://{ip}:{port}"
        ok, msg = self._lpadmin(["-p", name, "-v", uri], f"Cambiar dirección de {name}")
        if not ok:
            return OpResult(False, "CUPS rechazó el cambio de dirección.", msg)
        p = self.get_printer(name)
        if not p or p.uri != uri:
            return OpResult(False, "El cambio no se reflejó en CUPS.", msg)
        return OpResult(True, f"{name} ahora apunta a {uri}.")

    def set_driver(self, name: str, driver: DriverRef) -> OpResult:
        try:
            dargs = self._driver_args(driver)
        except FileNotFoundError as e:
            return OpResult(False, str(e))
        ok, msg = self._lpadmin(["-p", name, *dargs], f"Cambiar controlador de {name}")
        if not ok:
            return OpResult(False, "CUPS rechazó el cambio de controlador.", msg)
        return OpResult(True, f"Controlador de {name} cambiado a {driver.label or driver.ref}.", msg)

    def rename_printer(self, old: str, new: str) -> OpResult:
        p = self.get_printer(old)
        if not p:
            return OpResult(False, f"No existe {old}.")
        ppd = Path(f"/etc/cups/ppd/{old}.ppd")
        if p.driver == BUILTIN_PPD_NAME:
            dref = DriverRef("ppd_file", str(builtin_escpos_ppd()))
        elif ppd.exists() and os.access(ppd, os.R_OK):
            tmp = Path(tempfile.mkdtemp()) / f"{new}.ppd"
            tmp.write_bytes(ppd.read_bytes())
            dref = DriverRef("ppd_file", str(tmp))
        else:
            return OpResult(False, "CUPS no permite renombrar colas directamente y no se puede leer el PPD "
                                   "actual. Elimine y vuelva a crear la impresora con el nuevo nombre.")
        ip, port = parse_uri(p.uri)
        res = self.create_printer(new, dref, ip, port or 9100)
        if not res.ok:
            return res
        rm = self.remove_printer(old)
        if not rm.ok:
            return OpResult(False, f"Se creó {new} pero no se pudo eliminar {old}.", rm.detail)
        return OpResult(True, f"Impresora renombrada: {old} → {new}.")

    # ------------------------------------------------------------ trabajos
    def list_jobs(self, name: str | None = None) -> list[PrintJob]:
        args = ["lpstat", "-o"] + ([name] if name else [])
        r = _run(args)
        jobs = []
        for line in r.stdout.splitlines():
            m = re.match(r"^(\S+)-(\d+)\s+(\S+)\s+(\d+)\s+(.+)$", line.strip())
            if m:
                jobs.append(PrintJob(job_id=f"{m.group(1)}-{m.group(2)}", printer=m.group(1),
                                     size=m.group(4), submitted=m.group(5).strip(), status="pendiente"))
        return jobs

    def clear_queue(self, name: str) -> OpResult:
        r = _run(["cancel", "-a", name])
        if not r.ok:
            return OpResult(False, f"No se pudo vaciar la cola de {name}.", r.short_error())
        left = self.list_jobs(name)
        if left:
            return OpResult(False, f"Quedan {len(left)} trabajos en la cola.")
        return OpResult(True, f"Cola de {name} vaciada.")

    def restart_queue(self, name: str) -> OpResult:
        steps = []
        for cmd in (["cupsdisable", name], ["cancel", "-a", name], ["cupsenable", name], ["cupsaccept", name]):
            r = _run(cmd)
            steps.append(f"{' '.join(cmd)} → {'OK' if r.ok else r.short_error()}")
            if not r.ok and cmd[0] in ("cupsenable", "cupsaccept"):
                r2 = run_admin_macos(["/usr/sbin/" + cmd[0], name])
                if not r2.ok:
                    return OpResult(False, f"No se pudo reactivar la cola {name}.", "\n".join(steps))
        p = self.get_printer(name)
        if p and p.enabled:
            return OpResult(True, f"Cola {name} reiniciada y habilitada.", "\n".join(steps))
        return OpResult(False, f"La cola {name} no quedó habilitada.", "\n".join(steps))

    def _submit(self, name: str, args: list[str]) -> OpResult:
        r = _run(args, timeout=60)
        if not r.ok:
            return OpResult(False, "La cola rechazó el trabajo.", r.short_error())
        m = re.search(rf"({re.escape(name)}-\d+)", r.stdout)
        if not m:
            return OpResult(False, "CUPS no devolvió un identificador de trabajo.", r.output)
        return OpResult(True, f"Trabajo {m.group(1)} aceptado por la cola.", data={"job_id": m.group(1)})

    def print_test_page(self, name: str) -> OpResult:
        text = (
            "eREDES - PRINTER & NETWORK MANAGER\n"
            "PAGINA DE PRUEBA DEL SISTEMA (macOS / CUPS)\n\n"
            f"Impresora: {name}\nFecha: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ\n0123456789\n\n\n"
        )
        f = Path(tempfile.mkstemp(suffix=".txt")[1])
        f.write_text(text, encoding="utf-8")
        return self._submit(name, ["lp", "-d", name, "-t", "eREDES prueba", str(f)])

    def print_raw(self, name: str, data: bytes, title: str = "eREDES") -> OpResult:
        f = Path(tempfile.mkstemp(suffix=".bin")[1])
        f.write_bytes(data)
        return self._submit(name, ["lp", "-d", name, "-o", "raw", "-t", title, str(f)])

    def wait_job(self, name: str, job_id: str, timeout: float = 30) -> OpResult:
        deadline = time.time() + timeout
        while time.time() < deadline:
            pending = _ids(_run(["lpstat", "-W", "not-completed", "-o", name]).stdout)
            if job_id not in pending:
                done = _ids(_run(["lpstat", "-W", "completed", "-o", name]).stdout)
                if job_id in done:
                    return OpResult(True, f"CUPS confirmó el trabajo {job_id} como completado "
                                          "(datos entregados a la impresora).")
                return OpResult(False, f"El trabajo {job_id} desapareció de la cola sin estado 'completado' "
                                       "(cancelado o abortado).")
            time.sleep(1)
        p = self.get_printer(name)
        reason = (p.extra.get("reason") if p else "") or "sin mensaje"
        return OpResult(False, f"El trabajo {job_id} sigue en cola tras {int(timeout)} s.", f"Estado CUPS: {reason}")

    # ------------------------------------------------------------ servicio
    def spooler_status(self) -> OpResult:
        r = _run(["lpstat", "-e"])
        running = r.ok
        return OpResult(running, "Servicio CUPS en ejecución" if running else "CUPS no responde",
                        r.short_error() if not running else "")

    def restart_spooler(self) -> OpResult:
        r = run_admin_macos(["/bin/launchctl", "kickstart", "-k", "system/org.cups.cupsd"],
                            prompt="eREDES necesita permisos para reiniciar el servicio de impresión (CUPS).")
        if not r.ok:
            return OpResult(False, "No se pudo reiniciar CUPS.", r.short_error())
        for _ in range(10):
            time.sleep(0.5)
            if self.spooler_status().ok:
                return OpResult(True, "Servicio de impresión CUPS reiniciado.")
        return OpResult(False, "CUPS no respondió tras el reinicio.")

    # ------------------------------------------------------------ drivers
    def _ppd_snapshot(self) -> set[str]:
        files = set()
        for pattern in ("/Library/Printers/PPDs/Contents/Resources/**/*",):
            files.update(glob.glob(pattern, recursive=True))
        return files

    def install_driver_package(self, path: str, pkg_type: str, driver_names: list[str]) -> OpResult:
        if pkg_type == "PPD":
            return OpResult(True, "Los PPD no requieren instalación: se usan directamente al crear la cola.",
                            data={"driver_ref": DriverRef("ppd_file", path)})
        if pkg_type == "PKG":
            before = self._ppd_snapshot()
            r = run_admin_macos(["/usr/sbin/installer", "-pkg", path, "-target", "/"], timeout=900,
                                prompt="eREDES necesita permisos para instalar el controlador.")
            if not r.ok:
                return OpResult(False, "El instalador de macOS devolvió un error.", r.short_error())
            new = sorted(self._ppd_snapshot() - before)
            return OpResult(True, "Paquete instalado correctamente (confirmado por installer).", r.output,
                            data={"installed": new})
        if pkg_type == "DMG":
            r = _run(["hdiutil", "attach", "-nobrowse", "-readonly", "-plist", path], timeout=120)
            if not r.ok:
                return OpResult(False, "No se pudo montar el DMG.", r.short_error())
            mounts = re.findall(r"<string>(/Volumes/[^<]+)</string>", r.stdout)
            if not mounts:
                return OpResult(False, "El DMG no expuso un volumen.")
            mount = mounts[0]
            try:
                pkgs = sorted(glob.glob(os.path.join(glob.escape(mount), "**", "*.pkg"), recursive=True))
                if not pkgs:
                    return OpResult(False, "El DMG no contiene paquetes .pkg instalables.")
                return self.install_driver_package(pkgs[0], "PKG", driver_names)
            finally:
                _run(["hdiutil", "detach", mount, "-quiet"], timeout=60)
        return OpResult(False, f"Los paquetes {pkg_type} no se pueden instalar en macOS "
                               "(son controladores de Windows).")
