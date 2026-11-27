"""Identificación del fabricante por dirección MAC (base OUI local, sin internet)."""
from __future__ import annotations

import gzip
import logging
import threading

from app.core import paths

log = logging.getLogger("eredes.oui")

_table: dict[int, dict[str, str]] | None = None
_lock = threading.Lock()


def _load() -> dict[int, dict[str, str]]:
    global _table
    with _lock:
        if _table is not None:
            return _table
        t: dict[int, dict[str, str]] = {6: {}, 7: {}, 9: {}}
        user = paths.data_dir() / "oui.tsv.gz"
        src = user if user.exists() else paths.resource_dir() / "oui.tsv.gz"
        try:
            with gzip.open(src, "rt", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    pref, _, name = line.rstrip("\n").partition("\t")
                    t.setdefault(len(pref), {})[pref] = name
        except OSError as e:
            log.warning("No se pudo cargar la base OUI: %s", e)
        _table = t
        return t


def normalize_mac(mac: str) -> str:
    mac = (mac or "").strip().replace("-", ":").lower()
    if not mac or mac in ("(incomplete)", "incomplete"):
        return ""
    parts = mac.split(":")
    if len(parts) == 6:
        try:
            return ":".join(f"{int(p, 16):02X}" for p in parts)
        except ValueError:
            return ""
    clean = mac.replace(":", "").replace(".", "")
    if len(clean) == 12:
        return ":".join(clean[i:i + 2].upper() for i in range(0, 12, 2))
    return ""


def vendor(mac: str) -> str:
    mac = normalize_mac(mac)
    if not mac:
        return ""
    h = mac.replace(":", "")
    if int(h[1], 16) & 0x2:
        return "(MAC aleatoria/local)"
    t = _load()
    for n in (9, 7, 6):
        v = t.get(n, {}).get(h[:n])
        if v:
            return v
    return ""
