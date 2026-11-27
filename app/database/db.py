"""Base de datos SQLite de eREDES.

Acceso con conexiones por hilo (las tareas en segundo plano también escriben).
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from app.core import paths

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS manufacturers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    folder TEXT NOT NULL,
    notes TEXT DEFAULT '',
    limitations TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manufacturer_id INTEGER NOT NULL REFERENCES manufacturers(id) ON DELETE CASCADE,
    name TEXT NOT NULL COLLATE NOCASE,
    folder TEXT NOT NULL,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(manufacturer_id, name)
);

CREATE TABLE IF NOT EXISTS driver_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    pkg_type TEXT NOT NULL,
    os TEXT NOT NULL,
    arch TEXT NOT NULL,
    version TEXT DEFAULT '',
    provider TEXT DEFAULT '',
    driver_names TEXT DEFAULT '[]',
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    signature_status TEXT DEFAULT 'no_verificada',
    signature_detail TEXT DEFAULT '',
    requirements TEXT DEFAULT '',
    installed INTEGER DEFAULT 0,
    imported_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS printers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    driver TEXT NOT NULL,
    driver_package_id INTEGER REFERENCES driver_packages(id) ON DELETE SET NULL,
    ip TEXT NOT NULL,
    port INTEGER NOT NULL,
    os TEXT NOT NULL,
    last_status TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT DEFAULT '',
    status TEXT NOT NULL,
    detail TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    target TEXT NOT NULL,
    ip TEXT DEFAULT '',
    port INTEGER,
    overall TEXT NOT NULL,
    results TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    target TEXT NOT NULL,
    ports TEXT NOT NULL,
    hosts_alive INTEGER DEFAULT 0,
    duration REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scan_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    ip TEXT NOT NULL,
    mac TEXT DEFAULT '',
    hostname TEXT DEFAULT '',
    vendor TEXT DEFAULT '',
    ports TEXT DEFAULT '',
    status TEXT DEFAULT '',
    dev_type TEXT DEFAULT '',
    services TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS speed_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    download_mbps REAL,
    upload_mbps REAL,
    ping_ms REAL,
    jitter_ms REAL,
    loss_pct REAL,
    server TEXT,
    duration_s REAL,
    detail TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    title TEXT NOT NULL,
    client TEXT DEFAULT '',
    path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    logger TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS network_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    interface TEXT NOT NULL,
    temp_ip TEXT NOT NULL,
    temp_mask TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    restored INTEGER DEFAULT 0,
    restored_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ops_ts ON operations(ts);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_pkg_model ON driver_packages(model_id);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or paths.db_path())
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init()

    # ---------- conexión ----------
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA journal_mode=WAL")
            self._local.conn = c
        return c

    def _init(self) -> None:
        with self._lock:
            c = self.conn()
            c.executescript(SCHEMA)
            c.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
            c.commit()

    def execute(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn().execute(sql, tuple(params))
            self.conn().commit()
            return cur

    def query(self, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self.conn().execute(sql, tuple(params)).fetchall())

    def one(self, sql: str, params: Iterable = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Iterable = (), default=0):
        r = self.one(sql, params)
        return r[0] if r and r[0] is not None else default

    # ---------- settings ----------
    def get_setting(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM settings WHERE key=?", (key,))
        if not r:
            return default
        try:
            return json.loads(r["value"])
        except (ValueError, TypeError):
            return r["value"]

    def set_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value)))

    # ---------- historial ----------
    def log_operation(self, category: str, action: str, target: str, status: str, detail: str = "") -> int:
        cur = self.execute(
            "INSERT INTO operations(ts,category,action,target,status,detail) VALUES(?,?,?,?,?,?)",
            (now(), category, action, target, status, detail[:4000]),
        )
        return cur.lastrowid

    def recent_operations(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM operations ORDER BY id DESC LIMIT ?", (limit,))

    # ---------- copias de seguridad ----------
    def backup(self, dest_dir: Path | None = None) -> Path:
        dest_dir = Path(dest_dir or paths.backups_dir())
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"eredes_{datetime.now():%Y%m%d_%H%M%S}.db"
        with self._lock:
            dst = sqlite3.connect(target)
            self.conn().backup(dst)
            dst.close()
        return target

    def restore(self, backup_file: Path) -> None:
        backup_file = Path(backup_file)
        src = sqlite3.connect(backup_file)
        try:
            ok = src.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.DatabaseError as e:
            src.close()
            raise ValueError(f"El archivo no es una base de datos de eREDES válida: {e}")
        if not ok:
            src.close()
            raise ValueError("El archivo no es una copia de eREDES (falta schema_version).")
        self.backup()  # copia de seguridad previa
        with self._lock:
            src.backup(self.conn())
        src.close()

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c:
            c.close()
            self._local.conn = None


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def set_db(db: Database) -> None:
    """Permite inyectar una BD (tests)."""
    global _db
    _db = db


def copy_file_safe(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
