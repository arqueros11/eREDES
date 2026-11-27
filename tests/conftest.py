import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

# Carpeta de datos aislada para las pruebas (no toca la instalación real)
_TMP = tempfile.mkdtemp(prefix="eredes_test_")
os.environ["EREDES_HOME"] = _TMP


class Ctx:
    cancelled = False

    def progress(self, *_a):
        pass

    def emit(self, *_a):
        pass

    def check(self):
        pass


@pytest.fixture
def ctx():
    return Ctx()


@pytest.fixture
def lib(tmp_path):
    from app.database.db import Database, set_db
    from app.drivers.library import DriverLibrary
    db = Database(tmp_path / "t.db")
    set_db(db)
    root = tmp_path / "DRIVERS"
    for m in ("EPSON", "BIENEX", "POS-D", "XPRINTER", "CBX", "OTRAS_MARCAS"):
        (root / m).mkdir(parents=True)
    return DriverLibrary(root, db)
