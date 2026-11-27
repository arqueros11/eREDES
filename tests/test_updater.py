import io
import json
import zipfile

import pytest

from app.core import updater


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, headers=None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _release(tag, assets):
    return json.dumps({"tag_name": tag, "body": "Notas", "html_url": "https://github.com/x/y/releases/tag/" + tag,
                       "assets": assets}).encode()


def test_version_compare():
    assert updater._parse_version("v1.10.0") > updater._parse_version("1.9.9")
    assert updater._parse_version("1.0.0") == updater._parse_version("ERMA.V1.0.0")


def test_check_picks_asset_for_this_os(monkeypatch):
    suffix = updater.asset_suffix()
    assets = [{"name": "eREDES-Windows.zip", "browser_download_url": "https://e/w.zip", "size": 10},
              {"name": "eREDES-macOS.zip", "browser_download_url": "https://e/m.zip", "size": 20},
              {"name": f"eREDES-{suffix}.zip.sha256", "browser_download_url": "https://e/s", "size": 1}]
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: _Resp(_release("ERMA.V99.0.0", assets)))
    info = updater.check("usuario/eREDES")
    assert info.available and info.latest == "99.0.0"
    assert suffix in info.asset_name and info.sha256_url == "https://e/s"


def test_check_up_to_date(monkeypatch):
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: _Resp(_release("v0.0.1", [])))
    assert not updater.check("usuario/eREDES").available


def test_check_bad_repo():
    with pytest.raises(updater.UpdateError):
        updater.check("no es un repo")


def test_download_verifies_sha(monkeypatch, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("eREDES/eREDES.exe", b"MZ")
    data = buf.getvalue()
    info = updater.UpdateInfo("1.0.0", "1.0.1", True, asset_name="eREDES-X.zip", asset_url="https://e/z",
                              asset_size=len(data), sha256_url="https://e/s")
    calls = {"n": 0}

    def fake(req, *a, **k):
        calls["n"] += 1
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/s"):
            return _Resp(b"0" * 64 + b"  eREDES-X.zip")   # hash incorrecto
        return _Resp(data, {"Content-Length": str(len(data))})
    monkeypatch.setattr(updater.urllib.request, "urlopen", fake)
    with pytest.raises(updater.UpdateError, match="SHA-256"):
        updater.download(info)
    import hashlib
    good = hashlib.sha256(data).hexdigest().encode()
    monkeypatch.setattr(updater.urllib.request, "urlopen",
                        lambda req, *a, **k: _Resp(good) if req.full_url.endswith("/s")
                        else _Resp(data, {"Content-Length": str(len(data))}))
    assert updater.download(info).exists()


def test_dev_mode_cannot_self_update():
    assert not updater.can_self_update()   # las pruebas no corren desde el .exe/.app
