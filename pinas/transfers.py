"""Laufende Hoch- und Herunterladevorgänge der Weboberfläche (nur im Speicher).

Damit weiß der Server, wie weit eine Übertragung ist, und verweigert Abmelden und Herunterfahren,
solange sie läuft – auch wenn der Wunsch von einem anderen Gerät kommt.
"""
import hashlib
import re
import threading
import time
import uuid
from pathlib import Path

from flask import g, session

_STALE = 120  # Sekunden ohne neues Teilstück: der Browser ist weg, das Hochladen gilt als abgebrochen
_KEEP_DONE = 50  # beendete Downloads kurz merken, damit das Fenster das Ende sicher mitbekommt
_ID = re.compile(r"^[0-9a-f]{32}$")

_lock = threading.Lock()
_active: dict[str, dict] = {}
_done: dict[str, dict] = {}


def browser() -> str:
    # Der CSRF-Wert gehört zur Anmeldung in genau diesem Browser
    return hashlib.sha256(session.get("csrf", "").encode()).hexdigest()


def _purge() -> None:
    now = time.monotonic()
    for tid, e in list(_active.items()):
        if e["kind"] == "upload" and now - e["touched"] > _STALE:
            del _active[tid]
            Path(e["path"]).unlink(missing_ok=True)


def begin(kind: str, name: str, size: int, tid: str = "", **extra) -> dict:
    with _lock:
        _purge()
        if not _ID.match(tid) or tid in _active or tid in _done:
            tid = uuid.uuid4().hex
        entry = {"id": tid, "kind": kind, "name": name, "size": size, "done": 0, "cancel": False,
                 "user": g.user["username"], "browser": browser(), "touched": time.monotonic(),
                 "lock": threading.Lock(), **extra}
        _active[tid] = entry
    return entry


def get(tid: str) -> dict | None:
    """Laufende Übertragung des angemeldeten Benutzers."""
    with _lock:
        e = _active.get(tid)
    return e if e and e["user"] == g.user["username"] else None


def status(tid: str) -> dict:
    with _lock:
        e = _active.get(tid) or _done.get(tid)
        finished = tid in _done
    if not e or e["user"] != g.user["username"]:
        return {"known": False}
    return {"known": True, "finished": finished, "done": e["done"], "size": e["size"]}


def touch(entry: dict) -> None:
    entry["touched"] = time.monotonic()


def end(entry: dict, remember: bool = False) -> None:
    with _lock:
        _active.pop(entry["id"], None)
        if remember:
            _done[entry["id"]] = entry
            while len(_done) > _KEEP_DONE:
                _done.pop(next(iter(_done)))


def busy() -> list[dict]:
    with _lock:
        _purge()
        return list(_active.values())


def mine() -> list[dict]:
    """Übertragungen, die in diesem Browser laufen."""
    me = browser()
    return [e for e in busy() if e["browser"] == me]


def describe(entries: list[dict]) -> str:
    if not entries:
        return ""
    e = entries[0]
    what = "hochgeladen" if e["kind"] == "upload" else "heruntergeladen"
    more = f" und {len(entries) - 1} weitere Übertragung(en)" if len(entries) > 1 else ""
    return f"Gerade wird „{e['name']}“ {what} (von {e['user']}){more}. Das geht erst, wenn alles fertig ist."
