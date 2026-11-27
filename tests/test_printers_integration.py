"""Prueba de integración REAL con el sistema de impresión (CUPS en macOS).

Crea una impresora de verdad apuntando al emulador ESC/POS local, imprime, comprueba
los bytes recibidos, cambia el puerto, diagnostica y la elimina.
Se omite en Windows (requiere administrador) y si CUPS no está disponible.
"""
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Integración CUPS (macOS)")


def _lan_ip():
    from app.network.mdns import _default_local_ip
    ip = _default_local_ip()
    if not ip or ip.startswith("127."):
        pytest.skip("Sin IP de red local")
    return ip


def test_full_printer_lifecycle(ctx, tmp_path):
    from escpos_emulator import EscPosEmulator

    from app.database.db import Database, set_db
    from app.diagnostics.printer_diag import diagnose
    from app.platform.macos.printers import builtin_escpos_ppd
    from app.platform.base import DriverRef
    from app.printers.manager import PrinterManager

    set_db(Database(tmp_path / "p.db"))
    host = _lan_ip()
    emu = EscPosEmulator(host=host, port=0).start()
    emu2 = EscPosEmulator(host=host, port=0).start()
    name = f"EREDES_PYTEST_{int(time.time())}"
    m = PrinterManager()
    try:
        drv = DriverRef("ppd_file", str(builtin_escpos_ppd()), "eREDES ESC/POS")
        r = m.create(ctx, name, drv, host, emu.port)
        assert r.ok, [(s.name, s.status, s.message) for s in r.steps]
        assert any(p.name == name for p in m.system_printers())
        # nombre duplicado rechazado
        assert not m.create(ctx, name, drv, host, emu.port).ok
        # prueba por la cola: CUPS debe confirmar y el emulador recibir el ticket
        t = m.test_print_os(name, escpos_ticket=True, wait=60)
        assert t.ok, t.message + t.detail
        assert b"PRUEBA DE IMPRESION" in bytes(emu.received)
        # cambio de puerto real
        c = m.change_address(name, host, emu2.port)
        assert c.ok, c.message
        assert m.backend.get_printer(name).port == emu2.port
        d = diagnose(ctx, host, emu2.port, name, True, False)
        assert d["overall"] == "OK", [(c.name, c.status, c.detail) for c in d["checks"]]
        hist = m.db.query("SELECT action,status FROM operations")
        assert ("Crear impresora", "OK") in [(h["action"], h["status"]) for h in hist]
    finally:
        rm = m.remove(name)
        emu.stop()
        emu2.stop()
    assert rm.ok
    assert m.backend.get_printer(name) is None


def test_create_fails_without_connection(ctx, tmp_path):
    from app.database.db import Database, set_db
    from app.platform.macos.printers import builtin_escpos_ppd
    from app.platform.base import DriverRef
    from app.printers.manager import PrinterManager
    set_db(Database(tmp_path / "q.db"))
    m = PrinterManager()
    r = m.create(ctx, "EREDES_NOCONN", DriverRef("ppd_file", str(builtin_escpos_ppd())), _lan_ip(), 1)
    assert not r.ok and r.steps[1].status == "error"
    assert m.backend.get_printer("EREDES_NOCONN") is None
