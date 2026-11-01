"""Speicherplatz pro Benutzer: Der Admin legt fest, wie viel jeder in „Privat“ belegen darf.

Die Grenzen stehen in /var/lib/pinas/quotas.json ({"name": Bytes}); kein Eintrag = unbegrenzt.
Dieselbe Datei liest pinas-dfree, damit das Netzlaufwerk die Grenze als Laufwerksgröße meldet.
Gezählt wird alles im privaten Ordner, auch dessen Papierkorb.
"""
import json
import os
import threading
import time
from pathlib import Path

from flask import current_app

from .config import storage

_CACHE_SECONDS = 30
_cache: dict[str, tuple[float, int]] = {}
_lock = threading.Lock()


def _file() -> Path:
    return current_app.config["DB_PATH"].parent / "quotas.json"


def quotas() -> dict:
    try:
        data = json.loads(_file().read_text())
        return {k: int(v) for k, v in data.items() if int(v) > 0}
    except (OSError, ValueError, TypeError):
        return {}


def set_quota(username: str, limit: int | None) -> None:
    data = quotas()
    if limit:
        data[username] = int(limit)
    else:
        data.pop(username, None)
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, _file())


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def used(username: str, fresh: bool = False) -> int:
    """Belegter Platz im privaten Ordner (kurz zwischengespeichert, große Ordner zählen dauert)."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(username)
        if hit and not fresh and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
    size = dir_size(storage(current_app.config)["users"] / username)
    with _lock:
        _cache[username] = (now, size)
    return size


def forget(username: str) -> None:
    with _lock:
        _cache.pop(username, None)


def info(username: str) -> dict:
    """{"limit": Bytes oder None, "used", "free", "pct"} für Anzeige und Prüfung."""
    limit = quotas().get(username)
    size = used(username)
    if not limit:
        return {"limit": None, "used": size, "free": None, "pct": 0}
    return {"limit": limit, "used": size, "free": max(0, limit - size), "pct": min(100, size / limit * 100)}
