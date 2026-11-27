import json
import zipfile

import pytest

from app.core.validation import ValidationError
from app.drivers import analyzers

INF = """; driver de prueba
[Version]
Signature="$Windows NT$"
Class=Printer
ClassGuid={4D36E979-E325-11CE-BFC1-08002BE10318}
Provider=%MFG%
DriverVer=05/10/2023,7.5.0.0
CatalogFile=tm.cat

[Manufacturer]
%MFG%=EPSON,NTamd64,NTx86

[EPSON.NTamd64]
"EPSON TM-T20II Receipt" = TM_T20II, USBPRINT\\EPSONTM-T20II
%TM88%   = TM_T88, USBPRINT\\EPSONTM-T88

[EPSON.NTx86]
"EPSON TM-T20II Receipt" = TM_T20II

[Strings]
MFG="EPSON"
TM88="EPSON TM-T88VI Receipt"
"""

PPD = """*PPD-Adobe: "4.3"
*FileVersion: "2.1"
*Manufacturer: "EPSON"
*ModelName: "TM-T20"
*NickName: "EPSON TM-T20 Receipt"
*cupsFilter: "application/vnd.cups-raster 0 rastertotmtest_inexistente"
"""


def test_inf_parse(tmp_path):
    f = tmp_path / "tm.inf"
    f.write_text(INF, encoding="utf-8")
    info = analyzers.analyze_inf(f)
    assert info.os == "windows"
    assert info.version == "7.5.0.0"
    assert info.provider == "EPSON"
    assert info.arch == "x64,x86"
    assert "EPSON TM-T20II Receipt" in info.driver_names
    assert "EPSON TM-T88VI Receipt" in info.driver_names
    assert info.signature_status == "invalida"   # falta tm.cat
    (tmp_path / "tm.cat").write_bytes(b"x")
    assert analyzers.analyze_inf(f).signature_status == "no_verificada"


def test_inf_utf16(tmp_path):
    f = tmp_path / "u.inf"
    f.write_bytes(b"\xff\xfe" + INF.encode("utf-16-le"))
    assert "EPSON TM-T20II Receipt" in analyzers.analyze_inf(f).driver_names


def test_ppd_and_filters(tmp_path):
    f = tmp_path / "tm.ppd"
    f.write_text(PPD)
    info = analyzers.analyze_ppd(f)
    assert info.driver_names == ["EPSON TM-T20 Receipt"]
    assert info.version == "2.1"
    assert analyzers.missing_cups_filters(info) == ["rastertotmtest_inexistente"]


def test_compat():
    assert analyzers.is_compatible("windows", "x64,x86", "windows", "x64")[0]
    assert not analyzers.is_compatible("windows", "x64", "macos", "arm64")[0]
    assert not analyzers.is_compatible("windows", "x86", "windows", "x64")[0]
    assert not analyzers.is_compatible("windows", "x64", "windows", "arm64")[0]
    assert analyzers.is_compatible("macos", "universal", "macos", "arm64")[0]


def test_pe_arch(tmp_path):
    f = tmp_path / "a.exe"
    data = bytearray(b"MZ" + b"\x00" * 58 + (64).to_bytes(4, "little") + b"PE\x00\x00" + (0x8664).to_bytes(2, "little"))
    f.write_bytes(bytes(data))
    assert analyzers._pe_arch(f) == "x64"


def test_library_sync_and_dynamic_brands(lib, tmp_path):
    (lib.root / "EPSON" / "TM-T20").mkdir()
    (lib.root / "EPSON" / "TM-T20" / "tm.inf").write_text(INF)
    (lib.root / "NUEVA_MARCA" / "M1").mkdir(parents=True)
    (lib.root / "NUEVA_MARCA" / "M1" / "x.ppd").write_text(PPD)
    (lib.root / "CBX" / "suelto.ppd").write_text(PPD)
    rep = lib.sync()
    names = [m["name"] for m in lib.manufacturers()]
    assert "NUEVA_MARCA" in names and "BIENEX" in names and "BIXOLON" not in names
    assert rep.packages == 3 and rep.added == 3
    assert lib.sync().added == 0  # sin cambios
    cbx = [m for m in lib.manufacturers() if m["name"] == "CBX"][0]
    assert [mo["name"] for mo in lib.models(cbx["id"])] == ["_GENERAL"]
    assert len(lib.duplicates()) == 1  # x.ppd y suelto.ppd son iguales
    assert lib.packages(search="TM-T88")


def test_library_crud(lib):
    mid = lib.add_manufacturer("star")
    assert (lib.root / "STAR").is_dir()
    with pytest.raises(ValidationError):
        lib.add_manufacturer("STAR")
    moid = lib.add_model(mid, "TSP100")
    lib.rename_model(moid, "TSP143")
    assert (lib.root / "STAR" / "TSP143").is_dir()
    lib.rename_manufacturer(mid, "STAR_MICRONICS")
    assert (lib.root / "STAR_MICRONICS" / "TSP143").is_dir()
    lib.delete_model(moid)
    lib.delete_manufacturer(mid)
    assert not (lib.root / "STAR_MICRONICS").exists()


def test_delete_blocked_by_printer(lib):
    (lib.root / "EPSON" / "TM").mkdir()
    (lib.root / "EPSON" / "TM" / "a.ppd").write_text(PPD)
    lib.sync()
    pkg = lib.packages()[0]
    lib.db.execute("INSERT INTO printers(name,driver,driver_package_id,ip,port,os,created_at,updated_at)"
                   " VALUES('COCINA','x',?,'1.2.3.4',9100,'macos','t','t')", (pkg["id"],))
    epson = [m for m in lib.manufacturers() if m["name"] == "EPSON"][0]
    with pytest.raises(ValidationError):
        lib.delete_manufacturer(epson["id"])
    with pytest.raises(ValidationError):
        lib.delete_package(pkg["id"])


def test_import_zip_and_zipslip(lib, tmp_path):
    mid = lib.add_manufacturer("ZTEST")
    moid = lib.add_model(mid, "M")
    z = tmp_path / "drv.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("win64/tm.inf", INF)
        zf.writestr("win64/tm.cat", "x")
        zf.writestr("__MACOSX/._junk", "x")
    lib.import_into_model(moid, z)
    lib.sync()
    pk = lib.packages(moid)
    assert len(pk) == 1 and pk[0]["pkg_type"] == "INF" and pk[0]["arch"] == "x64,x86"
    bad = tmp_path / "evil.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../../evil.txt", "x")
    with pytest.raises(ValidationError):
        lib.import_into_model(moid, bad)
    assert not (tmp_path / "evil.txt").exists()
    with pytest.raises(ValidationError):
        (tmp_path / "x.txt").write_text("x")
        lib.import_into_model(moid, tmp_path / "x.txt")


def test_integrity_and_install_block(lib):
    from app.drivers.installers import install_package, precheck
    (lib.root / "EPSON" / "TM").mkdir()
    f = lib.root / "EPSON" / "TM" / "tm.inf"
    f.write_text(INF)
    lib.sync()
    pid = lib.packages()[0]["id"]
    assert lib.verify_integrity(pid)[0][1] == "ok"
    f.write_text(INF + "\n; manipulado")
    assert lib.verify_integrity(pid)[0][1] == "modificado"
    pc = precheck(lib, pid)
    assert not pc.ok and any("SHA-256" in b for b in pc.blocking)
    import sys
    if not sys.platform.startswith("win"):
        assert any("no se instalan" in b for b in pc.blocking)  # INF en macOS
    r = install_package(lib, pid)
    assert not r.ok


def test_metadata_override(lib):
    (lib.root / "BIENEX" / "M1").mkdir()
    f = lib.root / "BIENEX" / "M1" / "setup.msi"
    f.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100)
    lib.sync()
    pid = lib.packages()[0]["id"]
    lib.update_package_metadata(pid, arch="x64", version="1.2", driver_names=["BIENEX 80mm"])
    r = lib.package(pid)
    assert r["arch"] == "x64" and json.loads(r["driver_names"]) == ["BIENEX 80mm"]
    # el override sobrevive a un re-análisis
    lib.db.execute("UPDATE driver_packages SET sha256='x', updated_at='2000' WHERE id=?", (pid,))
    lib.sync()
    assert lib.package(pid)["version"] == "1.2"
