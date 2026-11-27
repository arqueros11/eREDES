"""Protocolo ESC/POS: ticket de prueba y consulta de estado en tiempo real (DLE EOT n).

Referencia: Epson ESC/POS Command Reference — DLE EOT (transmisión de estado en tiempo real).
Compatible con Epson TM y con la mayoría de impresoras térmicas que implementan ESC/POS
(Xprinter, CBX, BIENEX, POS-D... según modelo). Si la impresora no responde o la respuesta
no tiene los bits fijos esperados, el estado se informa como "No compatible / no determinado".
"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field

ESC = b"\x1b"
GS = b"\x1d"
INIT = ESC + b"@"
CUT = GS + b"V" + bytes([66, 0])     # corte parcial con avance
ALIGN_C = ESC + b"a\x01"
ALIGN_L = ESC + b"a\x00"
BOLD_ON, BOLD_OFF = ESC + b"E\x01", ESC + b"E\x00"
DOUBLE_ON, DOUBLE_OFF = GS + b"!\x11", GS + b"!\x00"
CP858 = ESC + b"t\x13"               # página de códigos PC858 (acentos y €)


def _t(s: str) -> bytes:
    return s.encode("cp858", errors="replace")


def test_ticket(printer_name: str, ip: str, port: int, width: int = 42, extra_lines: list[str] | None = None) -> bytes:
    line = "-" * width
    stamp = time.strftime("%d/%m/%Y %H:%M:%S")
    parts = [
        INIT, CP858, ALIGN_C, DOUBLE_ON, BOLD_ON, _t("eREDES\n"), DOUBLE_OFF, BOLD_OFF,
        _t("PRINTER & NETWORK MANAGER\n"), _t(line + "\n"), BOLD_ON, _t("PRUEBA DE IMPRESION\n"), BOLD_OFF,
        _t(line + "\n"), ALIGN_L,
        _t(f"Impresora: {printer_name}\n"), _t(f"IP:        {ip}\n"), _t(f"Puerto:    {port}\n"),
        _t(f"Fecha:     {stamp}\n"), _t(line + "\n"),
        _t("ABCDEFGHIJKLMNOPQRSTUVWXYZ\n"), _t("abcdefghijklmnopqrstuvwxyz\n"), _t("0123456789 áéíóú ñ Ñ €\n"),
    ]
    for ln in extra_lines or []:
        parts.append(_t(ln + "\n"))
    parts += [_t(line + "\n"), ALIGN_C, _t("Si lee este ticket, la comunicacion\nTCP/IP es correcta.\n"),
              b"\n\n\n\n", CUT]
    return b"".join(parts)


@dataclass
class EscPosStatus:
    responded: bool = False
    compatible: bool = False
    online: bool | None = None
    cover_open: bool | None = None
    paper_out: bool | None = None
    paper_near_end: bool | None = None
    paper_feed_button: bool | None = None
    error: bool | None = None
    cutter_error: bool | None = None
    unrecoverable_error: bool | None = None
    auto_recoverable_error: bool | None = None   # p.ej. sobrecalentamiento del cabezal
    raw: dict = field(default_factory=dict)
    message: str = ""


def _valid(b: int) -> bool:
    # bits fijos de DLE EOT: bit0=0, bit1=1, bit4=1, bit7=0
    return (b & 0b10010011) == 0b00010010


def query_status(ip: str, port: int = 9100, timeout: float = 2.0) -> EscPosStatus:
    st = EscPosStatus()
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
    except OSError as e:
        st.message = f"No se pudo conectar a {ip}:{port}: {e}"
        return st
    try:
        s.settimeout(timeout)
        for n in (1, 2, 3, 4):
            s.sendall(b"\x10\x04" + bytes([n]))
            try:
                data = s.recv(1)
            except (socket.timeout, OSError):
                data = b""
            if data:
                st.raw[n] = data[0]
    finally:
        s.close()
    if not st.raw:
        st.message = ("La impresora acepta conexión pero no respondió a DLE EOT: no admite estado ESC/POS "
                      "en tiempo real por red (o está en modo solo-recepción).")
        return st
    st.responded = True
    if not all(_valid(b) for b in st.raw.values()):
        st.message = "La respuesta no corresponde al formato de estado ESC/POS: modelo no compatible."
        return st
    st.compatible = True
    if 1 in st.raw:
        b = st.raw[1]
        st.online = not bool(b & 0x08)
    if 2 in st.raw:
        b = st.raw[2]
        st.cover_open = bool(b & 0x04)
        st.paper_feed_button = bool(b & 0x08)
        st.paper_out = bool(b & 0x20)
        st.error = bool(b & 0x40)
    if 3 in st.raw:
        b = st.raw[3]
        st.cutter_error = bool(b & 0x08)
        st.unrecoverable_error = bool(b & 0x20)
        st.auto_recoverable_error = bool(b & 0x40)
    if 4 in st.raw:
        b = st.raw[4]
        st.paper_near_end = (b & 0x0C) == 0x0C
        roll_end = (b & 0x60) == 0x60
        st.paper_out = bool(st.paper_out) or roll_end
    st.message = "Estado ESC/POS leído correctamente."
    return st


def send_raw(ip: str, port: int, data: bytes, timeout: float = 5.0) -> tuple[bool, str]:
    """Envía bytes directamente al puerto TCP (sin pasar por la cola del SO)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(data)
            try:
                s.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        return True, f"{len(data)} bytes entregados a {ip}:{port} (TCP confirmado)."
    except OSError as e:
        return False, f"Error enviando a {ip}:{port}: {e}"
