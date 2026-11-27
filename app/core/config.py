"""Configuración persistente (almacenada en la tabla settings)."""
from __future__ import annotations

from app.database.db import get_db

DEFAULTS = {
    "theme": "light",
    # Puertos de impresora y servicios comunes a comprobar en el escáner
    "scan_ports": [9100, 9101, 9102, 515, 631, 80, 443, 8080, 22, 23, 139, 445, 3389, 5900, 161],
    "printer_ports": [9100, 9101, 9102, 515, 631],
    "ping_timeout_ms": 800,
    "tcp_timeout_ms": 600,
    "scan_workers": 96,
    "snmp_community": "public",
    "escpos_status_enabled": True,
    # Subredes de fábrica habituales en impresoras POS (editables en Configuración)
    "factory_subnets": [
        "192.168.192.0/24",   # Epson TM (UB-E0x) por defecto 192.168.192.168
        "192.168.123.0/24",   # Xprinter por defecto 192.168.123.100
        "192.168.1.0/24",
        "192.168.0.0/24",
        "192.168.10.0/24",
        "10.0.0.0/24",
    ],
    "speedtest_duration_s": 8,
    "technician": "",
    # Actualizaciones: repositorio de GitHub con las versiones publicadas (Releases)
    "github_repo": "arqueros11/eREDES",
    "github_token": "",
    "auto_check_updates": True,
    "auto_install_updates": True,   # instalar sola al abrir (con 10 s para elegir "Más tarde")
    "company": "",
}


def get(key: str):
    return get_db().get_setting(key, DEFAULTS.get(key))


def set(key: str, value) -> None:  # noqa: A001
    get_db().set_setting(key, value)
