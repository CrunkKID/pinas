"""Privates Laufwerk: Von Anfang an gibt es nur „Gemeinsam“. „Privat“ richtet jeder Benutzer
bei Bedarf selbst ein (Konto-Seite) – und kann es dort auch wieder ausschalten.

Gespeichert in der Tabelle settings (Schlüssel "private"). Fürs Netzlaufwerk setzt pinas-helper
die Mitgliedschaft in der Gruppe "pinasprivat"; nur deren Mitglieder sehen die Freigabe „Privat“.
"""
from flask import current_app, g

from .config import storage
from .db import get_db


def _user_dir(username: str):
    return storage(current_app.config)["users"] / username


def enabled(username: str) -> bool:
    cache = g.setdefault("_private", {})
    if username not in cache:
        row = get_db().execute("SELECT value FROM settings WHERE username = ? AND key = 'private'",
                               (username,)).fetchone()
        if row:
            cache[username] = row["value"] == "1"
        else:
            # Konten von vor dieser Einstellung: aktiv, wenn im privaten Ordner schon Dateien liegen
            try:
                cache[username] = any(_user_dir(username).iterdir())
            except OSError:
                cache[username] = False
    return cache[username]


def set_enabled(username: str, on: bool, helper) -> None:
    """Schaltet „Privat“ ein oder aus. Beim Ausschalten bleiben die Dateien erhalten, nur verborgen."""
    helper.set_private(username, on)
    if on:
        _user_dir(username).mkdir(parents=True, exist_ok=True)
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (username, key, value) VALUES (?, 'private', ?)",
                 (username, "1" if on else "0"))
    conn.commit()
    g.setdefault("_private", {})[username] = on
