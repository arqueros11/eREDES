"""Configuración temporal de red en macOS.

Se usa `ifconfig <if> alias <ip> <mask>`: añade una IP SECUNDARIA sin tocar la
configuración DHCP/manual existente del servicio de red. Al quitarla con
`ifconfig <if> -alias <ip>` la interfaz queda exactamente como estaba.
Un reinicio del equipo o de la interfaz también elimina el alias (no es persistente).
"""
from __future__ import annotations

import re

import psutil

from app.core.shell import run, run_admin_macos
from app.platform.base import NetworkConfigBackend, OpResult


def _ipv4s(interface: str) -> list[tuple[str, str]]:
    out = []
    for a in psutil.net_if_addrs().get(interface, []):
        if a.family.name == "AF_INET":
            out.append((a.address, a.netmask or ""))
    return out


class MacNetworkConfig(NetworkConfigBackend):
    def snapshot(self, interface: str) -> dict:
        r = run(["ifconfig", interface])
        router = run(["ipconfig", "getoption", interface, "router"]).stdout.strip()
        return {"interface": interface, "ipv4": _ipv4s(interface), "ifconfig": r.stdout, "router": router}

    def add_secondary_ip(self, interface: str, ip: str, mask: str) -> OpResult:
        if not re.fullmatch(r"[a-z]+\d+", interface):
            return OpResult(False, f"Interfaz inválida: {interface}")
        if any(a == ip for a, _ in _ipv4s(interface)):
            return OpResult(False, f"{ip} ya está asignada a {interface}.")
        r = run_admin_macos(["/sbin/ifconfig", interface, "alias", ip, mask],
                            prompt=f"eREDES necesita permisos para añadir la IP temporal {ip} a {interface}.")
        if not r.ok:
            return OpResult(False, "No se pudo añadir la IP temporal.", r.short_error())
        if not any(a == ip for a, _ in _ipv4s(interface)):
            # psutil puede tardar en refrescar: comprobar con ifconfig
            if ip not in run(["ifconfig", interface]).stdout:
                return OpResult(False, "El sistema no muestra la IP temporal tras añadirla.")
        return OpResult(True, f"IP temporal {ip}/{mask} añadida a {interface}.")

    def remove_secondary_ip(self, interface: str, ip: str, snapshot: dict) -> OpResult:
        original = {a for a, _ in snapshot.get("ipv4", [])}
        if ip in original:
            return OpResult(False, f"{ip} formaba parte de la configuración original: no se elimina.")
        if ip not in run(["ifconfig", interface]).stdout:
            return OpResult(True, f"La IP {ip} ya no estaba en {interface}.")
        r = run_admin_macos(["/sbin/ifconfig", interface, "-alias", ip],
                            prompt=f"eREDES necesita permisos para quitar la IP temporal {ip}.")
        if not r.ok:
            return OpResult(False, "No se pudo quitar la IP temporal.", r.short_error())
        return self.verify_restored(interface, snapshot)

    def verify_restored(self, interface: str, snapshot: dict) -> OpResult:
        current = {a for a, _ in _ipv4s(interface)}
        cur_txt = run(["ifconfig", interface]).stdout
        original = {a for a, _ in snapshot.get("ipv4", [])}
        missing = [a for a in original if a not in current and a not in cur_txt]
        extra = [a for a in current if a not in original]
        if missing:
            return OpResult(False, f"Faltan direcciones originales en {interface}: {', '.join(missing)}. "
                                   "Desactive y active la interfaz o renueve DHCP.")
        if extra:
            return OpResult(False, f"Quedan direcciones adicionales en {interface}: {', '.join(extra)}")
        return OpResult(True, f"Configuración original de {interface} restaurada y verificada.")
