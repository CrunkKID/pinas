import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    username   TEXT UNIQUE NOT NULL,
    pw_hash    TEXT NOT NULL,
    is_admin   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Persönliche Einstellungen (z. B. Dashboard-Kacheln), gelten auf allen Geräten des Benutzers
CREATE TABLE IF NOT EXISTS settings (
    username   TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    PRIMARY KEY (username, key)
);
-- Abgemeldete Sitzungen: Die Anmeldung steckt im Browser-Cookie, eine Kopie gälte sonst bis zum Ablauf weiter
CREATE TABLE IF NOT EXISTS revoked_sessions (
    sid        TEXT PRIMARY KEY,
    expires    REAL NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DB_PATH"])
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()
