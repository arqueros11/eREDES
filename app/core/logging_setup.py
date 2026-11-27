"""Configuración de logs: archivo rotativo + tabla `logs` de SQLite."""
from __future__ import annotations

import logging
import logging.handlers
import sys

from . import paths


class DBLogHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from app.database.db import get_db, now
            msg = self.format(record) if record.exc_info else record.getMessage()
            get_db().execute("INSERT INTO logs(ts,level,logger,message) VALUES(?,?,?,?)",
                             (now(), record.levelname, record.name, msg[:4000]))
        except Exception:  # noqa: BLE001 - nunca romper por logging
            pass


def setup_logging(debug: bool = False) -> None:
    root = logging.getLogger("eredes")
    if root.handlers:
        return
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(paths.logs_dir() / "eredes.log", maxBytes=2_000_000,
                                              backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    sh.setLevel(logging.DEBUG if debug else logging.WARNING)
    root.addHandler(sh)
    root.addHandler(DBLogHandler())
