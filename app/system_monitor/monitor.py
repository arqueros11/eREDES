"""Monitor del sistema: CPU, RAM, disco, procesos, SO, servicios, SMART, temperatura."""
from __future__ import annotations

import json
import platform
import re
import socket
import time
from dataclasses import dataclass, field

import psutil

from app.core.platform_info import IS_MACOS, IS_WINDOWS, arch_key, os_label
from app.core.shell import run, run_powershell


@dataclass
class Snapshot:
    cpu_percent: float
    per_core: list[float]
    cores_logical: int
    cores_physical: int | None
    freq_mhz: float | None
    mem_total: int
    mem_used: int
    mem_available: int
    mem_percent: float
    swap_percent: float
    disks: list[dict]
    temperature: str
    top_cpu: list[dict] = field(default_factory=list)
    top_mem: list[dict] = field(default_factory=list)
    process_count: int = 0
    boot_time: float = 0.0


_primed = False


def _prime() -> None:
    global _primed
    if not _primed:
        psutil.cpu_percent(None)
        for p in psutil.process_iter():
            try:
                p.cpu_percent(None)
            except (psutil.Error, OSError):
                pass
        _primed = True
        time.sleep(0.3)


def disks() -> list[dict]:
    out = []
    seen = set()
    for part in psutil.disk_partitions(all=False):
        if IS_MACOS and (not (part.mountpoint == "/" or part.mountpoint.startswith("/Volumes/"))
                         or part.mountpoint.startswith("/Volumes/Recovery")):
            continue
        if part.mountpoint in seen or "cdrom" in part.opts:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        seen.add(part.mountpoint)
        out.append({"device": part.device, "mount": part.mountpoint, "fs": part.fstype, "total": u.total,
                    "used": u.used, "free": u.free, "percent": u.percent})
    return out


def temperature() -> str:
    if hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()
            vals = [t.current for arr in temps.values() for t in arr if t.current]
            if vals:
                return f"{max(vals):.0f} °C"
        except Exception:  # noqa: BLE001
            pass
    return "No disponible en este sistema"


def snapshot(top: int = 8) -> Snapshot:
    _prime()
    cpu = psutil.cpu_percent(None)
    per = psutil.cpu_percent(None, percpu=True)
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    freq = None
    try:
        f = psutil.cpu_freq()
        freq = f.current if f else None
    except Exception:  # noqa: BLE001
        pass
    procs = []
    ncpu = psutil.cpu_count() or 1
    for p in psutil.process_iter(["pid", "name", "memory_info", "username"]):
        try:
            c = p.cpu_percent(None) / ncpu
            mi = p.info.get("memory_info")
            procs.append({"pid": p.info["pid"], "name": p.info.get("name") or "?", "cpu": c,
                          "mem": mi.rss if mi else 0})
        except (psutil.Error, OSError):
            continue
    top_cpu = sorted(procs, key=lambda x: x["cpu"], reverse=True)[:top]
    top_mem = sorted(procs, key=lambda x: x["mem"], reverse=True)[:top]
    for lst in (top_cpu, top_mem):
        for d in lst:
            d["mem_pct"] = 100.0 * d["mem"] / vm.total if vm.total else 0
    return Snapshot(cpu, per, psutil.cpu_count(), psutil.cpu_count(logical=False), freq, vm.total, vm.used,
                    vm.available, vm.percent, sw.percent, disks(), temperature(), top_cpu, top_mem, len(procs),
                    psutil.boot_time())


def system_info() -> dict:
    """Información estática del equipo (lenta: llamar en segundo plano)."""
    info = {"Equipo": socket.gethostname(), "Sistema operativo": os_label(), "Arquitectura": arch_key(),
            "Python": platform.python_version(), "Procesador": platform.processor() or platform.machine()}
    if IS_MACOS:
        r = run(["sysctl", "-n", "machdep.cpu.brand_string"])
        if r.ok and r.stdout.strip():
            info["Procesador"] = r.stdout.strip()
        r = run(["system_profiler", "SPHardwareDataType", "-json"], timeout=30)
        try:
            hw = json.loads(r.stdout)["SPHardwareDataType"][0]
            info["Modelo"] = f"{hw.get('machine_name', '')} ({hw.get('machine_model', '')})"
            info["Chip"] = hw.get("chip_type", info["Procesador"])
            info["Memoria"] = hw.get("physical_memory", "")
            info["Número de serie"] = hw.get("serial_number", "")
        except (ValueError, KeyError, IndexError):
            pass
        info["Versión de compilación"] = run(["sw_vers", "-buildVersion"]).stdout.strip()
    elif IS_WINDOWS:
        r = run_powershell(
            "$cs=Get-CimInstance Win32_ComputerSystem;$os=Get-CimInstance Win32_OperatingSystem;"
            "$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1;$bios=Get-CimInstance Win32_BIOS;"
            "[pscustomobject]@{Fab=$cs.Manufacturer;Mod=$cs.Model;OS=$os.Caption;Ver=$os.Version;"
            "Build=$os.BuildNumber;CPU=$cpu.Name;Serie=$bios.SerialNumber;Dom=$cs.Domain}|ConvertTo-Json -Compress",
            timeout=40)
        try:
            d = json.loads(r.stdout.strip().splitlines()[-1])
            info.update({"Fabricante": d.get("Fab"), "Modelo": d.get("Mod"), "Sistema operativo":
                         f"{d.get('OS')} ({d.get('Ver')})", "Procesador": d.get("CPU"),
                         "Número de serie": d.get("Serie"), "Dominio/Grupo": d.get("Dom")})
        except (ValueError, IndexError):
            pass
    info["Memoria total"] = f"{psutil.virtual_memory().total / 1024 ** 3:.1f} GB"
    info["Encendido desde"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(psutil.boot_time()))
    return info


def smart_status() -> list[dict]:
    out = []
    if IS_MACOS:
        r = run(["diskutil", "list", "-plist", "physical"], timeout=20)
        disks_ = re.findall(r"<string>(disk\d+)</string>", r.stdout)
        for d in sorted(set(disks_)):
            ri = run(["diskutil", "info", d], timeout=20)
            m = re.search(r"SMART Status:\s*(.+)", ri.stdout)
            name = re.search(r"Device / Media Name:\s*(.+)", ri.stdout)
            if m:
                st = m.group(1).strip()
                out.append({"disk": d, "name": name.group(1).strip() if name else d, "status": st,
                            "ok": st.lower() == "verified" if st.lower() != "not supported" else None})
    elif IS_WINDOWS:
        r = run_powershell("$l=@(Get-PhysicalDisk | Select-Object FriendlyName,MediaType,"
                           "@{n='H';e={[string]$_.HealthStatus}},@{n='S';e={[string]$_.OperationalStatus}},Size);"
                           "ConvertTo-Json -InputObject $l -Compress", timeout=40)
        try:
            data = json.loads(r.stdout.strip() or "[]")
            for d in data if isinstance(data, list) else [data]:
                out.append({"disk": d.get("FriendlyName"), "name": f"{d.get('MediaType', '')}",
                            "status": f"{d.get('H')} / {d.get('S')}", "ok": d.get("H") == "Healthy"})
        except ValueError:
            pass
    return out


def services_summary() -> dict:
    """Servicios activos y estado del servicio de impresión."""
    out = {}
    if IS_MACOS:
        r = run(["launchctl", "list"], timeout=15)
        lines = [ln for ln in r.stdout.splitlines()[1:] if ln.strip()]
        running = [ln for ln in lines if ln.split("\t")[0] not in ("-", "")]
        out["Servicios (launchd del usuario)"] = f"{len(running)} en ejecución de {len(lines)} cargados"
    elif IS_WINDOWS:
        r = run_powershell("$s=Get-Service;\"$(@($s|?{$_.Status -eq 'Running'}).Count)|$($s.Count)\"", timeout=30)
        parts = r.stdout.strip().split("|")
        if len(parts) == 2:
            out["Servicios de Windows"] = f"{parts[0]} en ejecución de {parts[1]}"
    from app.platform import printer_backend
    sp = printer_backend().spooler_status()
    out["Servicio de impresión"] = sp.message
    return out


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"
