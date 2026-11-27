import socket
import struct
import threading

from app.network import mdns, snmp
from app.network.oui import normalize_mac, vendor
from app.printers import escpos


def test_oui():
    assert normalize_mac("0:11:22:3:44:5") == "00:11:22:03:44:05"
    assert vendor("60:CE:41:D1:A7:6C").lower().startswith("huawei")
    assert "aleatoria" in vendor("DE:AD:6E:7D:EB:ED")


def _fake_snmp_agent(port_holder, values):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port_holder.append(s.getsockname()[1])
    s.settimeout(3)
    try:
        data, addr = s.recvfrom(4096)
    except socket.timeout:
        return
    # respuesta construida con el mismo codificador (GetResponse 0xA2)
    tag, msg, _ = snmp._read_tlv(data, 0)
    i = 0
    _, ver, i = snmp._read_tlv(msg, i)
    _, comm, i = snmp._read_tlv(msg, i)
    _, pdu, _ = snmp._read_tlv(msg, i)
    _, rid, _ = snmp._read_tlv(pdu, 0)
    vbs = b""
    for oid, (t, v) in values.items():
        vbs += snmp._tlv(0x30, snmp._oid(oid) + snmp._tlv(t, v))
    resp_pdu = snmp._tlv(0xA2, snmp._tlv(0x02, rid) + snmp._int(0) + snmp._int(0) + snmp._tlv(0x30, vbs))
    s.sendto(snmp._tlv(0x30, snmp._tlv(0x02, ver) + snmp._tlv(0x04, comm) + resp_pdu), addr)
    s.close()


def test_snmp_roundtrip(monkeypatch):
    values = {snmp.OID_SYS_DESCR: (0x04, b"EPSON Built-in 10Base-T/100Base-TX Print Server"),
              snmp.OID_HR_PRINTER_STATUS: (0x02, b"\x03"),
              snmp.OID_HR_PRINTER_ERROR: (0x04, bytes([0b01001000]))}
    holder = []
    t = threading.Thread(target=_fake_snmp_agent, args=(holder, values))
    t.start()
    while not holder:
        pass
    real = socket.socket.sendto

    def sendto(self, data, addr):
        return real(self, data, ("127.0.0.1", holder[0]) if addr[1] == 161 else addr)
    monkeypatch.setattr(socket.socket, "sendto", sendto)
    v = snmp.snmp_get("127.0.0.1", list(values), timeout=2, retries=0)
    t.join()
    assert "EPSON" in snmp.as_text(v[snmp.OID_SYS_DESCR])
    assert v[snmp.OID_HR_PRINTER_STATUS] == 3
    assert snmp.decode_printer_errors(v[snmp.OID_HR_PRINTER_ERROR]) == ["Sin papel", "Tapa abierta"]


def test_mdns_parse():
    def name(n):
        return mdns._encode_name(n)
    recs = []
    inst = "EPSON TM-T88VI._pdl-datastream._tcp.local"
    recs.append(name("_pdl-datastream._tcp.local") + struct.pack("!HHIH", 12, 1, 120, len(name(inst))) + name(inst))
    srv = struct.pack("!HHH", 0, 0, 9100) + name("tm88.local")
    recs.append(name(inst) + struct.pack("!HHIH", 33, 1, 120, len(srv)) + srv)
    txt = b"".join(bytes([len(x)]) + x for x in (b"ty=EPSON TM-T88VI", b"product=(TM-T88VI)"))
    recs.append(name(inst) + struct.pack("!HHIH", 16, 1, 120, len(txt)) + txt)
    recs.append(name("tm88.local") + struct.pack("!HHIH", 1, 1, 120, 4) + socket.inet_aton("192.168.192.168"))
    pkt = struct.pack("!HHHHHH", 0, 0x8400, 0, len(recs), 0, 0) + b"".join(recs)
    parsed = mdns.parse_packet(pkt)
    types = {r[1] for r in parsed}
    assert types == {12, 33, 16, 1}
    a = [r for r in parsed if r[1] == 1][0]
    assert a[3] == "192.168.192.168"
    srvr = [r for r in parsed if r[1] == 33][0]
    assert srvr[3] == "tm88.local:9100"


def test_escpos_status_with_emulator():
    from escpos_emulator import EscPosEmulator
    emu = EscPosEmulator(port=0, paper_out=True, cover_open=True, cutter_error=True).start()
    try:
        st = escpos.query_status("127.0.0.1", emu.port)
        assert st.compatible
        assert st.paper_out and st.cover_open and st.cutter_error and st.online is False
        emu.paper_out = emu.cover_open = emu.cutter_error = False
        st = escpos.query_status("127.0.0.1", emu.port)
        assert st.online and not st.paper_out and not st.cover_open
        ok, _ = escpos.send_raw("127.0.0.1", emu.port, escpos.test_ticket("COCINA", "127.0.0.1", emu.port))
        assert ok
    finally:
        emu.stop()


def test_escpos_incompatible():
    # servidor que responde basura -> no compatible
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        c, _ = srv.accept()
        for _ in range(4):
            if c.recv(3):
                c.sendall(b"\xff")
        c.close()
    threading.Thread(target=serve, daemon=True).start()
    st = escpos.query_status("127.0.0.1", port)
    srv.close()
    assert st.responded and not st.compatible


def test_ip_profile_fill_and_template_blocked():
    from app.network import ip_recovery
    assert ip_recovery._fill("1F{ip0}{ip1}{ip2}{ip3}", "192.168.1.240", "255.255.255.0", "") == "1FC0A801F0"
    cbx = [p for p in ip_recovery.load_profiles() if p["id"] == "cbx_template"][0]
    r = ip_recovery.apply_profile(cbx, "192.168.0.100", "192.168.1.240", "255.255.255.0", "192.168.1.1")
    assert not r.ok and "incompleto" in r.message
    assert ip_recovery.suggest_temp_ip("192.168.0.0/24") == "192.168.0.251"
