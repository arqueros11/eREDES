"""Validación de datos de entrada (IPs, puertos, nombres, rangos)."""
from __future__ import annotations

import ipaddress
import re

from .platform_info import IS_WINDOWS


class ValidationError(ValueError):
    pass


def validate_ipv4(value: str, *, allow_network: bool = False) -> str:
    value = (value or "").strip()
    try:
        ip = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError):
        raise ValidationError(f"'{value}' no es una dirección IPv4 válida.")
    if not allow_network:
        if ip.is_unspecified:
            raise ValidationError("La dirección 0.0.0.0 no es válida.")
        if ip.is_multicast or str(ip) == "255.255.255.255":
            raise ValidationError(f"{ip} es una dirección multicast/broadcast.")
        if ip.is_loopback:
            raise ValidationError(f"{ip} es una dirección de loopback.")
    return str(ip)


def is_valid_ipv4(value: str) -> bool:
    try:
        validate_ipv4(value)
        return True
    except ValidationError:
        return False


def validate_port(value) -> int:
    try:
        p = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError(f"'{value}' no es un puerto válido.")
    if not 1 <= p <= 65535:
        raise ValidationError(f"El puerto {p} está fuera de rango (1-65535).")
    return p


def parse_port_list(text: str) -> list[int]:
    """'9100, 515,631, 8000-8002' -> [9100, 515, 631, 8000, 8001, 8002]"""
    ports: list[int] = []
    for chunk in re.split(r"[,\s;]+", (text or "").strip()):
        if not chunk:
            continue
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            pa, pb = validate_port(a), validate_port(b)
            if pb < pa or pb - pa > 1024:
                raise ValidationError(f"Rango de puertos inválido: {chunk}")
            ports.extend(range(pa, pb + 1))
        else:
            ports.append(validate_port(chunk))
    seen = set()
    out = []
    for p in ports:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def validate_netmask(value: str) -> str:
    value = (value or "").strip()
    try:
        if value.startswith("/"):
            net = ipaddress.IPv4Network(f"0.0.0.0{value}")
        else:
            net = ipaddress.IPv4Network(f"0.0.0.0/{value}")
    except ValueError:
        raise ValidationError(f"'{value}' no es una máscara de subred válida.")
    return str(net.netmask)


def mask_to_prefix(mask: str) -> int:
    return ipaddress.IPv4Network(f"0.0.0.0/{validate_netmask(mask)}").prefixlen


def parse_target_range(text: str, max_hosts: int = 4096) -> list[str]:
    """Acepta: '192.168.1.0/24', '192.168.1.10-192.168.1.50', '192.168.1.10-50',
    '192.168.1.15' (IP única) o varias separadas por coma."""
    text = (text or "").strip()
    if not text:
        raise ValidationError("Indique una IP, rango o red (CIDR).")
    hosts: list[str] = []
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        if "/" in part:
            try:
                net = ipaddress.IPv4Network(part, strict=False)
            except ValueError:
                raise ValidationError(f"Red inválida: {part}")
            if net.num_addresses > max_hosts + 2:
                raise ValidationError(f"La red {net} tiene demasiadas direcciones (máximo {max_hosts}).")
            if net.prefixlen >= 31:
                hosts.extend(str(h) for h in net)
            else:
                hosts.extend(str(h) for h in net.hosts())
        elif "-" in part:
            a, b = [x.strip() for x in part.split("-", 1)]
            start = ipaddress.IPv4Address(validate_ipv4(a, allow_network=True))
            if "." not in b:
                b = ".".join(a.split(".")[:3] + [b])
            end = ipaddress.IPv4Address(validate_ipv4(b, allow_network=True))
            if end < start:
                raise ValidationError(f"Rango invertido: {part}")
            if int(end) - int(start) + 1 > max_hosts:
                raise ValidationError(f"El rango {part} es demasiado grande (máximo {max_hosts}).")
            hosts.extend(str(ipaddress.IPv4Address(i)) for i in range(int(start), int(end) + 1))
        else:
            hosts.append(validate_ipv4(part))
    seen = set()
    out = []
    for h in hosts:
        if h not in seen:
            seen.add(h)
            out.append(h)
    if len(out) > max_hosts:
        raise ValidationError(f"Demasiadas direcciones ({len(out)}). Máximo {max_hosts}.")
    return out


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def validate_printer_name(name: str) -> str:
    """Valida el nombre de la impresora según las reglas del sistema operativo actual.

    Windows: hasta 220 caracteres, sin '\\' ni ',' ni '!' al inicio.
    CUPS (macOS): hasta 127 caracteres imprimibles, sin espacios, TAB, '/', '#', '\\', '?', comillas.
    """
    name = (name or "").strip()
    if not name:
        raise ValidationError("El nombre de la impresora es obligatorio.")
    if _CONTROL.search(name):
        raise ValidationError("El nombre contiene caracteres de control.")
    if IS_WINDOWS:
        if len(name) > 220:
            raise ValidationError("El nombre supera 220 caracteres.")
        if any(c in name for c in "\\,"):
            raise ValidationError("En Windows el nombre no puede contener '\\' ni ','.")
        if name.startswith("!"):
            raise ValidationError("El nombre no puede empezar con '!'.")
    else:  # macOS / Linux (CUPS)
        if len(name) > 127:
            raise ValidationError("El nombre supera 127 caracteres (límite de CUPS).")
        bad = [c for c in name if c in " \t/#\\?'\"@"]
        if bad:
            suggestion = re.sub(r"[ \t/#\\?'\"@]+", "_", name)
            raise ValidationError(
                f"CUPS (macOS) no permite espacios ni los caracteres / # \\ ? ' \" @ en el nombre. "
                f"Sugerencia: {suggestion}"
            )
    return name


def suggest_cups_name(name: str) -> str:
    return re.sub(r"[ \t/#\\?'\"@]+", "_", (name or "").strip())[:127]


def validate_folder_name(name: str, what: str = "nombre") -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationError(f"El {what} es obligatorio.")
    if name in (".", "..") or name.startswith("."):
        raise ValidationError(f"{what.capitalize()} inválido.")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValidationError(f'El {what} no puede contener < > : " / \\ | ? *')
    if len(name) > 80:
        raise ValidationError(f"El {what} es demasiado largo (máx. 80).")
    return name
