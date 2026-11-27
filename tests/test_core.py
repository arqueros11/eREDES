import pytest

from app.core.validation import (ValidationError, mask_to_prefix, parse_port_list, parse_target_range,
                                 validate_ipv4, validate_netmask, validate_port, validate_printer_name)


def test_ipv4():
    assert validate_ipv4(" 192.168.1.240 ") == "192.168.1.240"
    for bad in ("192.168.1.256", "abc", "", "0.0.0.0", "255.255.255.255", "127.0.0.1", "224.0.0.1"):
        with pytest.raises(ValidationError):
            validate_ipv4(bad)


def test_ports():
    assert validate_port("9100") == 9100
    for bad in ("0", "70000", "x"):
        with pytest.raises(ValidationError):
            validate_port(bad)
    assert parse_port_list("9100, 515 631,8000-8002, 9100") == [9100, 515, 631, 8000, 8001, 8002]


def test_ranges():
    assert len(parse_target_range("192.168.1.0/24")) == 254
    assert parse_target_range("192.168.1.10-12") == ["192.168.1.10", "192.168.1.11", "192.168.1.12"]
    assert parse_target_range("10.0.0.5") == ["10.0.0.5"]
    with pytest.raises(ValidationError):
        parse_target_range("10.0.0.0/8")
    with pytest.raises(ValidationError):
        parse_target_range("192.168.1.50-10")


def test_mask():
    assert validate_netmask("/24") == "255.255.255.0"
    assert mask_to_prefix("255.255.255.0") == 24
    with pytest.raises(ValidationError):
        validate_netmask("255.0.255.0")


def test_printer_name_cups():
    import sys
    if sys.platform.startswith("win"):
        assert validate_printer_name("COCINA 1") == "COCINA 1"
    else:
        assert validate_printer_name("COCINA") == "COCINA"
        with pytest.raises(ValidationError):
            validate_printer_name("COCINA 1")
    with pytest.raises(ValidationError):
        validate_printer_name("")


def test_db_backup_restore(tmp_path):
    from app.database.db import Database
    db = Database(tmp_path / "a.db")
    db.log_operation("x", "y", "z", "OK")
    bk = db.backup(tmp_path)
    db.log_operation("x", "y2", "z", "OK")
    assert db.scalar("SELECT COUNT(*) FROM operations") == 2
    db.restore(bk)
    assert db.scalar("SELECT COUNT(*) FROM operations") == 1
    with pytest.raises(ValueError):
        (tmp_path / "bad.db").write_text("no")
        db.restore(tmp_path / "bad.db")
