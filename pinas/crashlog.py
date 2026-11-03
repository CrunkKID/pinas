"""Absturz- und Fehlerprotokoll: Fehler der Weboberfläche und der Hintergrund-Threads festhalten.

Pro Fehler eine Zeile JSON in <state>/crash/errors.jsonl, höchstens zwei Dateien zu je MAX_BYTES.
Derselbe Fehler wird innerhalb einer Stunde nur einmal geschrieben, das schont die SD-Karte.
Was passiert, während die Weboberfläche gar nicht läuft, trägt der Notfallhelfer deploy/pinas-rescue
in dieselbe Datei ein. Er erzeugt auch den Fehlerbericht zum Herunterladen.

Das Protokoll darf nie selbst einen Fehler verursachen: Keine Funktion hier gibt eine Ausnahme weiter.
Formularwerte und lokale Variablen werden gar nicht erst erfasst, nur Quelltextzeilen und Fehlermeldungen.
"""
import faulthandler
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

MAX_BYTES = 512 * 1024
REPEAT_WINDOW = 3600
MAX_TEXT = 2000
MAX_TRACE = 12000
RESCUE = os.environ.get("PINAS_RESCUE", "/usr/local/sbin/pinas-rescue")

_dir: Path | None = None
_version = "?"
_fault_file = None
_installed = False
_lock = threading.Lock()
_seen: dict[str, list] = {}  # Fingerabdruck → [zuletzt geschrieben, seitdem unterdrückt]

# Was niemals in einer Datei landen darf. Der Notfallhelfer maskiert beim Bericht zusätzlich Dateinamen,
# Benutzernamen und IP-Adressen (gleiche Regeln in deploy/pinas-rescue)
_MASKS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S), "[Schlüssel entfernt]"),
    (re.compile(r"(?i)\b(pass(?:word|wort|wd)?|pw(?:_hash)?|secret(?:_key)?|token|csrf\w*|cookie|session|authorization"
                r"|api[_-]?key)\b(['\"]?\s*[:=]\s*)(?:'[^'\n]*'|\"[^\"\n]*\"|[^\s,;&)]+)"), r"\1\2[entfernt]"),
    (re.compile(r"\b(?:scrypt|pbkdf2)[:$][^\s'\"]+"), "[Hash entfernt]"),
    (re.compile(r"\$[0-9a-z]{1,2}\$[./A-Za-z0-9$]{20,}"), "[Hash entfernt]"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "[Schlüssel entfernt]"),
    (re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"), "[Schlüssel entfernt]"),
]


def mask(text: str) -> str:
    for pattern, repl in _MASKS:
        text = pattern.sub(repl, text)
    return text


def _file() -> Path | None:
    return _dir / "errors.jsonl" if _dir else None


def _append(entry: dict) -> None:
    path = _file()
    _dir.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _lock:
        try:
            if path.stat().st_size + len(line) > MAX_BYTES:
                os.replace(path, path.with_suffix(".jsonl.1"))
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def _request_context() -> dict:
    """Seite und Benutzer des laufenden Aufrufs. Nur die Regel (/dateien/<root>/<path:sub>), nicht der
    echte Pfad, denn der enthält Dateinamen. Formularwerte werden nie erfasst."""
    try:
        from flask import g, has_request_context, request
        if not has_request_context():
            return {}
        user = g.get("user")
        return {"seite": str(request.url_rule or "?"), "funktion": request.endpoint or "?", "methode": request.method,
                "benutzer": user["username"] if user else "", "ist_admin": bool(user and user["is_admin"])}
    except Exception:  # noqa: BLE001
        return {}


def record(kind: str, title: str, text: str = "", trace: str = "", level: str = "error",
           context: dict | None = None, key: str = "") -> str:
    """Einen Fehler festhalten. Gibt seine Nummer zurück (derselbe Fehler behält dieselbe)."""
    try:
        err_id = hashlib.sha256((key or f"{kind}|{title}|{text}").encode("utf-8", "replace")).hexdigest()[:8]
        if not _dir:
            return err_id
        now = time.time()
        with _lock:
            seen = _seen.get(err_id)
            if seen and now - seen[0] < REPEAT_WINDOW:
                seen[1] += 1
                return err_id
            repeats = seen[1] if seen else 0
            if len(_seen) > 500:
                _seen.clear()
            _seen[err_id] = [now, 0]
        _append({"id": err_id, "ts": now, "kind": kind, "level": level, "title": mask(title)[:300],
                 "text": mask(text)[:MAX_TEXT], "trace": mask(trace)[-MAX_TRACE:], "version": _version,
                 "context": {**_request_context(), **(context or {})}, "repeats": repeats})
        return err_id
    except Exception:  # noqa: BLE001 – das Protokoll darf nie etwas anderes stören
        return ""


def record_exception(exc: BaseException, kind: str = "web", title: str = "") -> str:
    """Eine Ausnahme festhalten; dieselbe Ausnahme zählt nur einmal, auch wenn sie mehrfach gemeldet wird."""
    try:
        done = getattr(exc, "_pinas_id", "")
        if done:
            return done
        frames = traceback.extract_tb(exc.__traceback__)[-3:]
        key = type(exc).__name__ + "|" + "|".join(f"{f.filename}:{f.lineno}" for f in frames)
        err_id = record(kind, title or "Fehler in der Weboberfläche", f"{type(exc).__name__}: {exc}",
                        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), key=key)
        try:
            exc._pinas_id = err_id
        except AttributeError:
            pass
        return err_id
    except Exception:  # noqa: BLE001
        return ""


class _Handler(logging.Handler):
    """Alles, was als Fehler ins Log geht: abgefangene Fehler der Hintergrund-Threads, waitress, Flask."""

    def emit(self, rec: logging.LogRecord) -> None:
        # Nur eigene Meldungen übernehmen, die fremden nennen oft den Pfad mit Dateinamen
        own = rec.name.startswith("pinas") and not rec.getMessage().startswith("Exception on ")
        title = rec.getMessage() if own else f"Fehler in {rec.name}"
        exc = rec.exc_info[1] if rec.exc_info else None
        if exc is not None:
            in_request = bool(_request_context())
            record_exception(exc, "web" if in_request else "thread", "" if in_request else title)
        elif own:
            record("log", title, level="warn")


def setup(state_dir: Path) -> None:
    """Beim Start der Weboberfläche: Ablageort festlegen und alle Fehlerquellen anschließen."""
    global _dir, _version, _fault_file, _installed
    try:
        _dir = Path(state_dir) / "crash"
        _dir.mkdir(parents=True, exist_ok=True)
        try:
            _version = (Path(__file__).parent / "VERSION").read_text().strip()
        except OSError:
            pass
        if _installed:
            return
        _installed = True
        logging.getLogger().addHandler(_Handler(level=logging.ERROR))

        prev_sys, prev_thread = sys.excepthook, threading.excepthook

        def sys_hook(etype, value, tb):
            record_exception(value, "start", "PiNAS konnte nicht starten oder ist abgestürzt")
            prev_sys(etype, value, tb)

        def thread_hook(args):
            if args.exc_type is not SystemExit:
                name = args.thread.name if args.thread else "?"
                record_exception(args.exc_value, "thread", f"Hintergrund-Aufgabe {name} ist abgestürzt")
            prev_thread(args)

        sys.excepthook, threading.excepthook = sys_hook, thread_hook

        # Harte Abstürze des Python-Interpreters selbst (Speicherfehler): nur diese Datei überlebt so etwas
        fault = _dir / "faulthandler.log"
        if fault.exists() and fault.stat().st_size > 64 * 1024:
            fault.unlink()
        _fault_file = open(fault, "a", encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- Anzeigen

def read(limit: int = 200) -> list[dict]:
    """Neueste Einträge zuerst. Eine beschädigte Zeile stört nicht."""
    out = []
    path = _file()
    if not path:
        return out
    for p in (path, path.with_suffix(".jsonl.1")):
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                e = json.loads(line)
                e["time"] = time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(e["ts"]))
                out.append(e)
            except (ValueError, KeyError, TypeError, OverflowError, OSError):
                continue
            if len(out) >= limit:
                return out
    return out


def summary(days: int = 7) -> dict:
    cutoff = time.time() - days * 86400
    recent = [e for e in read(500) if e["ts"] >= cutoff]
    errors = [e for e in recent if e.get("level") == "error"]
    return {"errors": len(errors), "warnings": len(recent) - len(errors), "days": days,
            "last": errors[0] if errors else (recent[0] if recent else None)}


def clear() -> None:
    path = _file()
    if not path:
        return
    with _lock:
        _seen.clear()
        for p in (path, path.with_suffix(".jsonl.1")):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def report() -> str:
    """Der ganze Bericht kommt vom Notfallhelfer. Fehlt er, wenigstens die eigenen Einträge."""
    try:
        res = subprocess.run([RESCUE, "report"], capture_output=True, encoding="utf-8", errors="replace", timeout=120)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    parts = [f"PiNAS-Fehlerbericht (Kurzfassung, der Notfallhelfer war nicht verfügbar)\nVersion: {_version}\n"]
    for e in read(100):
        parts.append(f"\n--- {e['time']}  {e.get('kind')}  {e.get('title')}\n{e.get('text', '')}\n"
                     f"{e.get('context', '')}\n{e.get('trace', '')}\n")
    return mask("".join(parts))
