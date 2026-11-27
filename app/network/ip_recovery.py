"""Recuperación y configuración de IP de impresoras configuradas en otra subred.

Flujo seguro:
  1. Descubrir sin tocar la red (mDNS/Bonjour: las respuestas multicast cruzan subredes).
  2. Con autorización explícita, añadir una IP TEMPORAL SECUNDARIA en la subred de fábrica
     (no se modifica la IP principal ni el gateway; el equipo conserva su conectividad).
  3. Escanear la subred de fábrica y localizar la impresora.
  4. Cambiar la IP con el perfil del fabricante (web / http_post / raw_tcp documentado).
  5. Verificar la nueva IP desde la red del restaurante.
  6. Quitar la IP temporal y VERIFICAR que la configuración original quedó restaurada.

Las sesiones temporales se registran en la BD (tabla network_changes): si la app se cierra
o falla, al reiniciar se ofrece restaurarlas.
"""
from __future__ import annotations

import atexit
import ipaddress
import json
import logging
import socket
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from app.core import paths
from app.core.validation import ValidationError, validate_ipv4, validate_netmask
from app.database.db import get_db, now
from app.platform import network_config_backend
from app.platform.base import OpResult

from . import probes
from .interfaces import get_interfaces

log = logging.getLogger("eredes.iprecovery")


@dataclass
class TempIpSession:
    db_id: int
    interface: str
    ip: str
    mask: str
    snapshot: dict


_sessions: list[TempIpSession] = []


def active_sessions() -> list[TempIpSession]:
    return list(_sessions)


def pending_from_previous_run() -> list[TempIpSession]:
    """IPs temporales que quedaron sin restaurar (cierre inesperado)."""
    out = []
    for r in get_db().query("SELECT * FROM network_changes WHERE restored=0"):
        if any(s.db_id == r["id"] for s in _sessions):
            continue
        out.append(TempIpSession(r["id"], r["interface"], r["temp_ip"], r["temp_mask"], json.loads(r["snapshot"])))
    return out


def suggest_temp_ip(subnet: str) -> str:
    net = ipaddress.IPv4Network(subnet, strict=False)
    hosts = list(net.hosts())
    if len(hosts) < 2:
        raise ValidationError("Subred demasiado pequeña.")
    # .251 en una /24: lejos de las IP de fábrica típicas (.100, .168, .87) y del gateway (.1/.254)
    return str(hosts[-4] if len(hosts) > 8 else hosts[-1])


def add_temporary_ip(interface: str, ip: str, mask: str) -> tuple[OpResult, TempIpSession | None]:
    ip = validate_ipv4(ip)
    mask = validate_netmask(mask)
    names = {i.name for i in get_interfaces(include_virtual=True)}
    if interface not in names:
        return OpResult(False, f"La interfaz {interface} no existe."), None
    net = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
    for i in get_interfaces(include_virtual=True):
        if i.ipv4 and i.netmask and ipaddress.IPv4Address(i.ipv4) in net:
            return OpResult(False, f"{net} ya es una red local ({i.name} {i.ipv4}); no hace falta IP temporal."), None
    backend = network_config_backend()
    snap = backend.snapshot(interface)
    res = backend.add_secondary_ip(interface, ip, mask)
    if not res.ok:
        get_db().log_operation("red", "IP temporal", f"{interface} {ip}", "ERROR", res.message + " " + res.detail)
        return res, None
    cur = get_db().execute(
        "INSERT INTO network_changes(ts,interface,temp_ip,temp_mask,snapshot) VALUES(?,?,?,?,?)",
        (now(), interface, ip, mask, json.dumps(snap)))
    sess = TempIpSession(cur.lastrowid, interface, ip, mask, snap)
    _sessions.append(sess)
    get_db().log_operation("red", "IP temporal añadida", f"{interface} {ip}/{mask}", "OK", res.message)
    # comprobación de conectividad principal
    gw_ok = _main_connectivity_ok(snap)
    if not gw_ok:
        log.warning("Tras añadir la IP temporal no se alcanza el gateway original; revirtiendo")
        remove_temporary_ip(sess)
        return OpResult(False, "Se perdió la conectividad principal tras añadir la IP temporal: "
                               "se revirtió el cambio automáticamente."), None
    return res, sess


def _main_connectivity_ok(snap: dict) -> bool:
    gw = snap.get("router") or (snap.get("gateways") or [""])[0]
    if not gw:
        return True
    ok, _ = probes.ping(gw, 1500)
    if ok:
        return True
    # algunos routers no responden ICMP: probar TCP 53/80/443
    return any(probes.tcp_probe(gw, p, 1.0)[0] in ("open", "closed") for p in (53, 80, 443))


def remove_temporary_ip(sess: TempIpSession) -> OpResult:
    backend = network_config_backend()
    res = backend.remove_secondary_ip(sess.interface, sess.ip, sess.snapshot)
    if res.ok:
        get_db().execute("UPDATE network_changes SET restored=1, restored_at=? WHERE id=?", (now(), sess.db_id))
        if sess in _sessions:
            _sessions.remove(sess)
    get_db().log_operation("red", "Restaurar configuración", f"{sess.interface} {sess.ip}",
                           "OK" if res.ok else "ERROR", res.message)
    return res


def restore_all() -> list[OpResult]:
    return [remove_temporary_ip(s) for s in list(_sessions)]


@atexit.register
def _restore_on_exit() -> None:  # pragma: no cover - salida del proceso
    for s in list(_sessions):
        try:
            remove_temporary_ip(s)
        except Exception:  # noqa: BLE001
            log.exception("No se pudo restaurar %s al salir", s.ip)


# ---------------------------------------------------------------- perfiles
def load_profiles() -> list[dict]:
    out = []
    dirs = [paths.resource_dir() / "ip_profiles", paths.data_dir() / "ip_profiles"]
    (paths.data_dir() / "ip_profiles").mkdir(exist_ok=True)
    for d in dirs:
        for f in sorted(Path(d).glob("*.json")):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                p["_file"] = str(f)
                p["_user"] = d != dirs[0]
                out.append(p)
            except (ValueError, OSError) as e:
                log.warning("Perfil inválido %s: %s", f, e)
    return out


def profiles_for(manufacturer: str | None) -> list[dict]:
    m = (manufacturer or "").upper()
    return [p for p in load_profiles() if p.get("manufacturer") in ("*", m) or not m]


def _fill(template: str, new_ip: str, mask: str, gw: str) -> str:
    vals = {"new_ip": new_ip, "mask": mask, "gateway": gw}
    for prefix, addr in (("ip", new_ip), ("mask", mask), ("gw", gw or "0.0.0.0")):
        for i, octet in enumerate(addr.split(".")):
            vals[f"{prefix}{i}"] = f"{int(octet):02X}"
    return template.format(**vals)


def apply_profile(profile: dict, current_ip: str, new_ip: str, mask: str, gateway: str) -> OpResult:
    current_ip = validate_ipv4(current_ip)
    new_ip = validate_ipv4(new_ip)
    mask = validate_netmask(mask)
    if gateway:
        gateway = validate_ipv4(gateway)
    method = profile.get("method")
    if profile.get("enabled") is False:
        missing = "; ".join(profile.get("missing", [])) or "datos del fabricante"
        return OpResult(False, f"El perfil '{profile.get('name')}' está incompleto. Falta: {missing}")
    if method == "web":
        url = profile.get("url", "http://{ip}/").format(ip=current_ip)
        webbrowser.open(url)
        return OpResult(True, f"Se abrió {url}. Cambie la IP en la página y después verifique.",
                        data={"manual": True})
    if method == "raw_tcp":
        payload_hex = (profile.get("payload_hex") or "").replace(" ", "")
        if not payload_hex:
            return OpResult(False, "El perfil raw_tcp no tiene secuencia de bytes definida.")
        data = bytes.fromhex(_fill(payload_hex, new_ip, mask, gateway))
        port = int(profile.get("port", 9100))
        try:
            with socket.create_connection((current_ip, port), timeout=4) as s:
                s.sendall(data)
        except OSError as e:
            return OpResult(False, f"No se pudo enviar el comando a {current_ip}:{port}.", str(e))
        get_db().log_operation("red", "Cambio IP (raw_tcp)", f"{current_ip} → {new_ip}", "ENVIADO", profile.get("id", ""))
        return OpResult(True, f"Comando enviado a {current_ip}:{port} ({len(data)} bytes). "
                              "La impresora debe reiniciarse; verifique la nueva IP.")
    if method == "http_post":
        import urllib.parse
        import urllib.request
        url = profile["url"].format(ip=current_ip)
        fields = {k: _fill(str(v), new_ip, mask, gateway) for k, v in profile.get("fields", {}).items()}
        req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                code = r.status
        except Exception as e:  # noqa: BLE001
            return OpResult(False, "La impresora rechazó la petición HTTP.", str(e))
        get_db().log_operation("red", "Cambio IP (http_post)", f"{current_ip} → {new_ip}", f"HTTP {code}", url)
        return OpResult(200 <= code < 400, f"Respuesta HTTP {code}. Verifique la nueva IP.")
    return OpResult(False, f"Método de perfil desconocido: {method}")


def verify_new_ip(ip: str, port: int = 9100) -> OpResult:
    ip = validate_ipv4(ip)
    ok_ping, rtt = probes.ping(ip, 1200)
    st, _ = probes.tcp_probe(ip, port, 2.0)
    details = [f"Ping: {'responde' if ok_ping else 'sin respuesta'}{f' ({rtt:.1f} ms)' if rtt else ''}",
               f"TCP {port}: {'abierto' if st == 'open' else ('cerrado' if st == 'closed' else 'sin respuesta')}"]
    if st == "open":
        get_db().log_operation("red", "Verificar nueva IP", f"{ip}:{port}", "OK", "; ".join(details))
        return OpResult(True, f"La impresora responde en {ip}:{port}.", "\n".join(details))
    return OpResult(False, f"No se confirma la impresora en {ip}:{port}.", "\n".join(details))
