"""Cliente SNMP v1/v2c mínimo (GET) implementado sobre UDP, sin dependencias.

Se usa para identificar impresoras (sysDescr, hrDeviceDescr) y leer su estado
estándar (Printer-MIB / Host-Resources-MIB: hrPrinterStatus, hrPrinterDetectedErrorState).
"""
from __future__ import annotations

import random
import socket

OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_OBJECTID = "1.3.6.1.2.1.1.2.0"
OID_HR_DEVICE_DESCR = "1.3.6.1.2.1.25.3.2.1.3.1"
OID_HR_PRINTER_STATUS = "1.3.6.1.2.1.25.3.5.1.1.1"
OID_HR_PRINTER_ERROR = "1.3.6.1.2.1.25.3.5.1.2.1"
OID_PRT_SERIAL = "1.3.6.1.2.1.43.5.1.1.17.1"

HR_PRINTER_STATUS = {1: "Otro", 2: "Desconocido", 3: "Inactiva (lista)", 4: "Imprimiendo", 5: "Calentando"}

# RFC 3805: bit 0 = MSB del primer octeto
HR_PRINTER_ERRORS = [
    "Papel bajo", "Sin papel", "Tóner/tinta bajo", "Sin tóner/tinta", "Tapa abierta", "Atasco de papel",
    "Fuera de línea", "Requiere servicio técnico", "Falta bandeja de entrada", "Falta bandeja de salida",
    "Falta consumible", "Bandeja de salida casi llena", "Bandeja de salida llena", "Bandeja de entrada vacía",
    "Mantenimiento preventivo vencido",
]


class SnmpError(Exception):
    pass


# ---------------------------------------------------------------- BER
def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _len(len(value)) + value


def _int(v: int) -> bytes:
    b = v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big", signed=True)
    return _tlv(0x02, b)


def _oid(oid: str) -> bytes:
    parts = [int(x) for x in oid.split(".")]
    out = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        if p == 0:
            out += b"\x00"
            continue
        enc = []
        while p:
            enc.insert(0, (p & 0x7F) | (0x80 if enc else 0))
            p >>= 7
        out += bytes(enc)
    return _tlv(0x06, out)


def build_get(oids: list[str], community: str = "public", version: int = 1, req_id: int | None = None) -> bytes:
    req_id = req_id if req_id is not None else random.randint(1, 0x7FFFFFFF)
    varbinds = b"".join(_tlv(0x30, _oid(o) + b"\x05\x00") for o in oids)
    pdu = _tlv(0xA0, _int(req_id) + _int(0) + _int(0) + _tlv(0x30, varbinds))
    return _tlv(0x30, _int(version) + _tlv(0x04, community.encode()) + pdu)


def _read_tlv(data: bytes, i: int) -> tuple[int, bytes, int]:
    tag = data[i]
    i += 1
    ln = data[i]
    i += 1
    if ln & 0x80:
        nb = ln & 0x7F
        ln = int.from_bytes(data[i:i + nb], "big")
        i += nb
    val = data[i:i + ln]
    if len(val) != ln:
        raise SnmpError("Respuesta SNMP truncada")
    return tag, val, i + ln


def _decode_oid(b: bytes) -> str:
    parts = [b[0] // 40, b[0] % 40]
    n = 0
    for c in b[1:]:
        n = (n << 7) | (c & 0x7F)
        if not c & 0x80:
            parts.append(n)
            n = 0
    return ".".join(map(str, parts))


def _decode_value(tag: int, val: bytes):
    if tag == 0x02:
        return int.from_bytes(val, "big", signed=True)
    if tag == 0x04:
        return val
    if tag == 0x06:
        return _decode_oid(val)
    if tag == 0x40 and len(val) == 4:
        return ".".join(map(str, val))
    if tag in (0x41, 0x42, 0x43, 0x46):
        return int.from_bytes(val, "big")
    if tag in (0x05, 0x80, 0x81, 0x82):
        return None
    return val


def parse_response(data: bytes) -> tuple[int, int, dict[str, object]]:
    """Devuelve (request_id, error_status, {oid: valor})."""
    tag, msg, _ = _read_tlv(data, 0)
    if tag != 0x30:
        raise SnmpError("No es un mensaje SNMP")
    i = 0
    _, _ver, i = _read_tlv(msg, i)
    _, _comm, i = _read_tlv(msg, i)
    ptag, pdu, _ = _read_tlv(msg, i)
    if ptag != 0xA2:
        raise SnmpError(f"PDU inesperada 0x{ptag:02x}")
    j = 0
    _, rid, j = _read_tlv(pdu, j)
    _, est, j = _read_tlv(pdu, j)
    _, _eidx, j = _read_tlv(pdu, j)
    _, vbl, _ = _read_tlv(pdu, j)
    out: dict[str, object] = {}
    k = 0
    while k < len(vbl):
        _, vb, k = _read_tlv(vbl, k)
        _, oidb, m = _read_tlv(vb, 0)
        vtag, vval, _ = _read_tlv(vb, m)
        out[_decode_oid(oidb)] = _decode_value(vtag, vval)
    return int.from_bytes(rid, "big", signed=True), int.from_bytes(est, "big"), out


def snmp_get(ip: str, oids: list[str], community: str = "public", timeout: float = 1.5,
             retries: int = 1) -> dict[str, object]:
    """GET v2c; si falla, reintenta en v1 OID por OID. Lanza SnmpError si no hay respuesta."""
    last_err: Exception | None = None
    for version in (1, 0):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            for _ in range(retries + 1):
                rid = random.randint(1, 0x7FFFFFFF)
                s.sendto(build_get(oids, community, version, rid), (ip, 161))
                try:
                    while True:
                        data, _addr = s.recvfrom(65535)
                        r_id, est, values = parse_response(data)
                        if r_id == rid:
                            break
                except socket.timeout as e:
                    last_err = e
                    continue
                if est == 0:
                    return values
                if version == 0 and len(oids) > 1:
                    merged = {}
                    for o in oids:
                        try:
                            merged.update(snmp_get(ip, [o], community, timeout, 0))
                        except SnmpError:
                            merged[o] = None
                    return merged
                last_err = SnmpError(f"error-status {est}")
                break
        except OSError as e:
            last_err = e
        finally:
            s.close()
    raise SnmpError(f"Sin respuesta SNMP de {ip} ({last_err})")


def as_text(v) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace").strip("\x00 ").strip()
    return "" if v is None else str(v)


def decode_printer_errors(v) -> list[str]:
    if not isinstance(v, bytes) or not v:
        return []
    errs = []
    for byte_i, byte in enumerate(v[:2]):
        for bit in range(8):
            if byte & (0x80 >> bit):
                idx = byte_i * 8 + bit
                if idx < len(HR_PRINTER_ERRORS):
                    errs.append(HR_PRINTER_ERRORS[idx])
    return errs
