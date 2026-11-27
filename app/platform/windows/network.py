"""Configuración temporal de red en Windows 10/11.

Estrategia segura:
  * Interfaz con IP estática: se añade una IP secundaria con New-NetIPAddress -SkipAsSource
    (no cambia la IP de origen del equipo ni el gateway).
  * Interfaz con DHCP: Windows no permite mezclar DHCP con IPs estáticas salvo que se active
    `dhcpstaticipcoexistence` (Windows 10 2004+ / Windows 11). Se activa temporalmente y se
    restaura su valor original al quitar la IP. Si no se puede activar, se aborta SIN tocar nada.
"""
from __future__ import annotations

import json

from app.core.platform_info import is_admin
from app.core.shell import ps_quote, run, run_powershell
from app.platform.base import NetworkConfigBackend, OpResult


class WindowsNetworkConfig(NetworkConfigBackend):
    def snapshot(self, interface: str) -> dict:
        script = (
            f"$a={ps_quote(interface)};"
            "$i=Get-NetIPInterface -InterfaceAlias $a -AddressFamily IPv4;"
            "$ips=@(Get-NetIPAddress -InterfaceAlias $a -AddressFamily IPv4 | "
            "Select-Object IPAddress,PrefixLength,PrefixOrigin);"
            "$gw=@(Get-NetRoute -InterfaceAlias $a -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue"
            " | Select-Object -ExpandProperty NextHop);"
            "[pscustomobject]@{Dhcp=[string]$i.Dhcp;Index=$i.ifIndex;IPs=$ips;Gateways=$gw}"
            " | ConvertTo-Json -Depth 4 -Compress"
        )
        r = run_powershell(script)
        snap = {"interface": interface, "raw_ok": r.ok}
        if r.ok:
            try:
                d = json.loads(r.stdout.strip().splitlines()[-1])
                ips = d.get("IPs") or []
                if isinstance(ips, dict):
                    ips = [ips]
                snap["dhcp"] = d.get("Dhcp") == "Enabled"
                snap["index"] = d.get("Index")
                snap["ipv4"] = [(x["IPAddress"], x["PrefixLength"]) for x in ips]
                snap["gateways"] = d.get("Gateways") or []
            except (ValueError, KeyError, IndexError):
                snap["raw_ok"] = False
        nr = run(["netsh", "interface", "ipv4", "show", "interface", interface])
        snap["coexistence_enabled"] = _coexist_line(nr.stdout)
        return snap

    def add_secondary_ip(self, interface: str, ip: str, mask: str) -> OpResult:
        if not is_admin():
            return OpResult(False, "Se requiere ejecutar eREDES como administrador.")
        from app.core.validation import mask_to_prefix
        snap = self.snapshot(interface)
        if not snap.get("raw_ok"):
            return OpResult(False, f"No se pudo leer la configuración de {interface}. No se modificó nada.")
        if any(a == ip for a, _ in snap.get("ipv4", [])):
            return OpResult(False, f"{ip} ya está asignada a {interface}.")
        if snap.get("dhcp") and not snap.get("coexistence_enabled"):
            r = run(["netsh", "interface", "ipv4", "set", "interface", interface, "dhcpstaticipcoexistence=enabled"])
            if not r.ok:
                return OpResult(False, "Esta versión de Windows no permite IP secundaria con DHCP activo "
                                       "(dhcpstaticipcoexistence). No se modificó la red. Alternativa: conecte la "
                                       "impresora directamente por cable y configure IP manual.", r.short_error())
        r = run_powershell(
            f"New-NetIPAddress -InterfaceAlias {ps_quote(interface)} -IPAddress {ps_quote(ip)} "
            f"-PrefixLength {mask_to_prefix(mask)} -SkipAsSource $true -PolicyStore ActiveStore | Out-Null; 'OK'")
        if not r.ok:
            if snap.get("dhcp") and not snap.get("coexistence_enabled"):
                run(["netsh", "interface", "ipv4", "set", "interface", interface, "dhcpstaticipcoexistence=disabled"])
            return OpResult(False, "Windows rechazó la IP temporal.", r.short_error())
        after = self.snapshot(interface)
        if snap.get("dhcp") and not after.get("dhcp"):
            self.remove_secondary_ip(interface, ip, snap)
            return OpResult(False, "Windows desactivó DHCP al añadir la IP: se revirtió el cambio.")
        return OpResult(True, f"IP temporal {ip}/{mask} añadida a {interface} (solo en memoria, no persistente).")

    def remove_secondary_ip(self, interface: str, ip: str, snapshot: dict) -> OpResult:
        if any(a == ip for a, _ in snapshot.get("ipv4", [])):
            return OpResult(False, f"{ip} formaba parte de la configuración original: no se elimina.")
        r = run_powershell(
            f"Remove-NetIPAddress -InterfaceAlias {ps_quote(interface)} -IPAddress {ps_quote(ip)} "
            "-Confirm:$false -ErrorAction SilentlyContinue; 'OK'")
        if snapshot.get("dhcp") and not snapshot.get("coexistence_enabled"):
            run(["netsh", "interface", "ipv4", "set", "interface", interface, "dhcpstaticipcoexistence=disabled"])
        if not r.ok:
            return OpResult(False, "No se pudo quitar la IP temporal.", r.short_error())
        return self.verify_restored(interface, snapshot)

    def verify_restored(self, interface: str, snapshot: dict) -> OpResult:
        now = self.snapshot(interface)
        orig = {a for a, _ in snapshot.get("ipv4", [])}
        cur = {a for a, _ in now.get("ipv4", [])}
        problems = []
        if snapshot.get("dhcp") != now.get("dhcp"):
            problems.append("el estado DHCP cambió")
        extra = cur - orig
        if extra:
            # direcciones APIPA o renovación DHCP distinta no se consideran temporales nuestras
            extra = {a for a in extra if not a.startswith("169.254.")}
        if extra and not snapshot.get("dhcp"):
            problems.append("quedan IPs adicionales: " + ", ".join(sorted(extra)))
        if problems:
            return OpResult(False, f"Revise {interface}: " + "; ".join(problems))
        return OpResult(True, f"Configuración original de {interface} restaurada y verificada.")


def _coexist_line(text: str) -> bool:
    for line in text.splitlines():
        low = line.lower()
        if "coexist" in low:  # "DHCP/Static IP coexistence" / "Coexistencia ..."
            return "enabled" in low or "habilitad" in low
    return False
