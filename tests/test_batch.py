"""PC nueva (instalación en lote) y ping continuo."""
import sys
import time

import pytest

from app.platform.base import SystemDriver


def _drivers():
    return [SystemDriver("Microsoft Print to PDF", "Microsoft Print to PDF", kind="installed"),
            SystemDriver("EPSON L3150 Series", "EPSON L3150 Series", kind="installed"),
            SystemDriver("Generic / Text Only", "Generic / Text Only", kind="inbox")]


def test_default_driver_windows(monkeypatch):
    from app.printers import batch
    monkeypatch.setattr(batch, "IS_WINDOWS", True)
    # un driver de inyección de tinta de la misma marca NO sirve para una térmica
    assert batch.default_driver(_drivers(), "Seiko Epson Corporation").name == "Generic / Text Only"
    tm = SystemDriver("EPSON TM-T20II Receipt5", "EPSON TM-T20II Receipt5", kind="installed")
    assert batch.default_driver(_drivers() + [tm], "Seiko Epson Corporation").name == tm.name
    assert batch.default_driver(_drivers(), "").name == "Generic / Text Only"


def test_default_driver_macos(monkeypatch):
    from app.printers import batch
    monkeypatch.setattr(batch, "IS_WINDOWS", False)
    ppd = SystemDriver("eREDES ESC/POS", "/x.ppd", kind="ppd_file")
    assert batch.default_driver([SystemDriver("Epson X", "x", kind="model"), ppd], "Epson").kind == "ppd_file"


def test_check_names(monkeypatch):
    from app.printers import batch
    errs = batch.check_names(["COCINA", "cocina", "BARRA", ""], ["BARRA"])
    assert any("repetido" in e for e in errs)
    assert any("ya existe" in e for e in errs)
    assert any("vacío" in e for e in errs)
    assert batch.check_names(["COCINA", "BARRA"], []) == []


def test_identify_ticket():
    from app.printers import escpos
    t = escpos.identify_ticket("192.168.1.250", 9100)
    assert b"250" in t and b"192.168.1.250" in t and t.endswith(escpos.CUT)
    assert b"COCINA" in escpos.test_ticket("COCINA", "192.168.1.250", 9100)


def test_find_printers_filters(monkeypatch, ctx):
    from app.network import scanner
    from app.network.scanner import HostResult
    from app.printers import batch

    def fake_scan(_ctx, targets, ports, identify):
        hs = [HostResult("192.168.1.250", alive=True, open_ports=[9100], vendor="Seiko Epson", rtt_ms=2.0),
              HostResult("192.168.1.30", alive=True, open_ports=[445, 3389]),        # PC
              HostResult("192.168.1.1", alive=True, open_ports=[80], dev_type="Router / Gateway"),
              HostResult("192.168.1.200", alive=True, open_ports=[515], vendor="Xprinter"),
              HostResult("192.168.1.5", alive=True, open_ports=[9100], is_self=True)]
        return {"hosts": hs, "scanned": len(targets), "cancelled": False}

    class FakeBackend:
        def list_printers(self):
            from app.platform.base import SystemPrinter
            return [SystemPrinter(name="COCINA", ip="192.168.1.250", port=9100)]

    monkeypatch.setattr(scanner, "scan", fake_scan)
    monkeypatch.setattr("app.platform.printer_backend", lambda: FakeBackend())
    res = batch.find_printers(ctx, ["192.168.1.1"])
    ips = [c.ip for c in res["printers"]]
    assert ips == ["192.168.1.200", "192.168.1.250"]          # ordenadas por IP, sin PC/router/este equipo
    by = {c.ip: c for c in res["printers"]}
    assert by["192.168.1.250"].installed_as == "COCINA"
    assert by["192.168.1.200"].port == 515


def test_ping_state_transitions():
    from app.network.live_ping import PingState
    s = PingState("192.168.1.250")
    assert s.update(None, 1.0) is None          # primera ronda: sin aviso
    assert s.update(None, 2.0) is None
    assert s.update(3.0, 3.0) == "up"
    assert s.update(4.0, 4.0) is None
    assert s.update(None, 5.0) == "down"
    assert (s.sent, s.received, s.lost) == (5, 2, 3)
    assert round(s.loss_pct) == 60 and s.avg_rtt == 3.5


def _lan_ip():
    from app.network.mdns import _default_local_ip
    ip = _default_local_ip()
    if not ip or ip.startswith("127."):
        pytest.skip("Sin IP de red local")
    return ip


def test_ping_loop_real(ctx):
    """Ping real a la IP de este equipo durante 2 rondas."""
    from app.network.live_ping import ping_loop
    ip = _lan_ip()
    rounds = []

    class C(type(ctx)):
        @property
        def cancelled(self):
            return len(rounds) >= 2

        def emit(self, payload):
            rounds.append(payload)

    t0 = time.time()
    n = ping_loop(C(), [ip], interval=0.3)
    assert n == 2 and all(ip in r["replies"] for r in rounds)
    assert time.time() - t0 < 10


@pytest.mark.skipif(sys.platform != "darwin", reason="Integración CUPS (macOS)")
def test_identify_and_install_batch_real(ctx, tmp_path):
    """Identificar e instalar 2 impresoras de verdad (CUPS + emuladores ESC/POS en la red local)."""
    from escpos_emulator import EscPosEmulator

    from app.database.db import Database, set_db
    from app.platform import printer_backend
    from app.printers import batch

    set_db(Database(tmp_path / "b.db"))
    host = _lan_ip()
    e1 = EscPosEmulator(host=host, port=0).start()
    e2 = EscPosEmulator(host=host, port=0).start()
    stamp = int(time.time())
    names = [f"EREDES_LOTE_A_{stamp}", f"EREDES_LOTE_B_{stamp}"]
    be = printer_backend()
    drv = batch.driver_ref(batch.default_driver(be.list_drivers()))
    try:
        idr = batch.identify(ctx, [(host, e1.port), (host, e2.port)])
        # los puertos del emulador no son 9100: identify solo permite 9100-9102
        assert all(not ok for _ip, ok, _m in idr)
        jobs = [batch.InstallJob(names[0], drv, host, e1.port), batch.InstallJob(names[1], drv, host, e2.port)]
        out = batch.install_batch(ctx, jobs)
        assert all(o.created and o.printed for o in out), [(o.name, o.message) for o in out]
        assert names[0].encode() in bytes(e1.received) and names[1].encode() in bytes(e2.received)
        # identificación directa por TCP (mismo envío que usa identify en el puerto 9100)
        from app.printers import escpos
        ok, _ = escpos.send_raw(host, e1.port, escpos.identify_ticket(host, e1.port))
        t0 = time.time()
        while b"IDENTIFICAR" not in bytes(e1.received) and time.time() - t0 < 5:
            time.sleep(0.05)
        assert ok and b"IDENTIFICAR" in bytes(e1.received)
    finally:
        from app.printers.manager import PrinterManager
        for n in names:
            PrinterManager().remove(n)
        e1.stop()
        e2.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="Integración Windows (spooler real)")
def test_install_batch_windows_generic(ctx, tmp_path):
    """Windows real: instala 'Generic / Text Only' (incluido en Windows) y 2 impresoras con prueba."""
    from escpos_emulator import EscPosEmulator

    from app.core.platform_info import is_admin
    from app.database.db import Database, set_db
    from app.platform import printer_backend
    from app.printers import batch

    if not is_admin():
        pytest.skip("Requiere administrador")
    set_db(Database(tmp_path / "w.db"))
    host = _lan_ip()
    e1 = EscPosEmulator(host=host, port=0).start()
    e2 = EscPosEmulator(host=host, port=0).start()
    stamp = int(time.time())
    names = [f"EREDES COCINA {stamp}", f"EREDES BARRA {stamp}"]
    be = printer_backend()
    drivers = be.list_drivers()
    d = batch.default_driver(drivers, "Seiko Epson Corporation")
    assert d.name == "Generic / Text Only", [x.name for x in drivers]
    try:
        jobs = [batch.InstallJob(names[0], batch.driver_ref(d), host, e1.port),
                batch.InstallJob(names[1], batch.driver_ref(d), host, e2.port)]
        out = batch.install_batch(ctx, jobs)
        assert all(o.created for o in out), [(o.name, o.message) for o in out]
        t0 = time.time()
        while time.time() - t0 < 30 and not (names[0].encode() in bytes(e1.received)
                                             and names[1].encode() in bytes(e2.received)):
            time.sleep(0.2)
        assert names[0].encode() in bytes(e1.received), [(o.name, o.message) for o in out]
        assert names[1].encode() in bytes(e2.received)
        assert {p.name for p in be.list_printers()} >= set(names)
    finally:
        from app.printers.manager import PrinterManager
        for n in names:
            PrinterManager().remove(n)
        e1.stop()
        e2.stop()
