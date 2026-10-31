"""Benutzerverwaltung: App-Datenbank, Samba-Konto und privater Ordner bleiben synchron."""
import re
import sqlite3
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from .helper import HelperError, SystemHelper

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
RESERVED = {
    "root", "pinas", "daemon", "bin", "sys", "sync", "games", "man", "lp",
    "mail", "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "nobody",
    "systemd-network", "systemd-resolve", "messagebus", "sshd", "avahi", "nasusers",
}
MIN_PASSWORD = 8


class UserError(Exception):
    pass


def validate_username(name: str) -> str:
    name = (name or "").strip().lower()
    if not USERNAME_RE.match(name):
        raise UserError("Benutzername: 2–32 Zeichen, nur a–z, 0–9, - und _, beginnend mit einem Buchstaben.")
    if name in RESERVED:
        raise UserError(f"Der Name „{name}“ ist reserviert.")
    return name


def validate_password(pw: str) -> None:
    if len(pw or "") < MIN_PASSWORD:
        raise UserError(f"Das Passwort muss mindestens {MIN_PASSWORD} Zeichen lang sein.")
    if "\n" in pw or "\r" in pw:
        raise UserError("Das Passwort darf keine Zeilenumbrüche enthalten.")


def list_users(conn: sqlite3.Connection):
    return conn.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY username").fetchall()


def get_user(conn: sqlite3.Connection, username: str):
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def count_admins(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]


def verify(conn: sqlite3.Connection, username: str, password: str):
    row = get_user(conn, (username or "").strip().lower())
    if row and check_password_hash(row["pw_hash"], password or ""):
        return row
    return None


def create_user(conn, helper: SystemHelper, users_dir: Path, username: str, password: str, is_admin: bool):
    username = validate_username(username)
    validate_password(password)
    if get_user(conn, username):
        raise UserError("Diesen Benutzer gibt es bereits.")
    try:
        helper.add_user(username, password)
    except HelperError as exc:
        raise UserError(f"Systembenutzer konnte nicht angelegt werden: {exc}") from exc
    try:
        conn.execute(
            "INSERT INTO users (username, pw_hash, is_admin) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), int(is_admin)),
        )
        conn.commit()
    except sqlite3.Error:
        helper.delete_user(username)
        raise
    (users_dir / username).mkdir(parents=True, exist_ok=True)
    return username


def set_password(conn, helper: SystemHelper, username: str, password: str) -> None:
    validate_password(password)
    if not get_user(conn, username):
        raise UserError("Unbekannter Benutzer.")
    try:
        helper.set_password(username, password)
    except HelperError as exc:
        raise UserError(f"Samba-Passwort konnte nicht gesetzt werden: {exc}") from exc
    conn.execute("UPDATE users SET pw_hash = ? WHERE username = ?", (generate_password_hash(password), username))
    conn.commit()


def delete_user(conn, helper: SystemHelper, username: str) -> None:
    """Entfernt das Konto. Der private Ordner bleibt bewusst erhalten (Datensicherheit)."""
    row = get_user(conn, username)
    if not row:
        raise UserError("Unbekannter Benutzer.")
    if row["is_admin"] and count_admins(conn) <= 1:
        raise UserError("Der letzte Administrator kann nicht gelöscht werden.")
    try:
        helper.delete_user(username)
    except HelperError as exc:
        raise UserError(f"Systembenutzer konnte nicht entfernt werden: {exc}") from exc
    conn.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
