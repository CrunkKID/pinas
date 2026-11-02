"""Ereignis-Protokoll (Admin-Option): wer sich wann von welchem Gerät anmeldet und was er mit
welchen Dateien macht – in der Weboberfläche und, falls aktiviert, über das Netzlaufwerk (Samba).

Die Einträge liegen in einer eigenen kleinen Datenbank (events.db). Sie ist in der Größe begrenzt:
höchstens MAX_ROWS Einträge, nichts älter als MAX_DAYS Tage – Älteres wird automatisch gelöscht.
"""
import json
import os
import re
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from flask import current_app, g, has_request_context, request

MAX_ROWS = 10_000
MAX_DAYS = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY,
    ts       REAL NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    action   TEXT NOT NULL,
    detail   TEXT NOT NULL DEFAULT '',
    size     INTEGER,
    ip       TEXT NOT NULL DEFAULT '',
    host     TEXT,
    device   TEXT NOT NULL DEFAULT '',
    via      TEXT NOT NULL DEFAULT 'web'
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
"""

# Aktion → (Text, Symbol, Farbe für den Punkt: ok / warn / bad / off)
ACTIONS = {
    "login":       ("Angemeldet", "🔑", "ok"),
    "login_fail":  ("Anmeldung fehlgeschlagen", "⚠", "bad"),
    "login_block": ("Anmeldung gesperrt (zu viele Fehlversuche)", "⛔", "bad"),
    "logout":      ("Abgemeldet", "🚪", "off"),
    "connect":     ("Netzlaufwerk verbunden", "🔌", "ok"),
    "upload":      ("Hochgeladen", "⬆", "ok"),
    "download":    ("Heruntergeladen", "⬇", "ok"),
    "open":        ("Geöffnet", "👁", "off"),
    "mkdir":       ("Ordner angelegt", "📁", "off"),
    "rename":      ("Umbenannt", "✎", "off"),
    "delete":      ("In den Papierkorb", "🗑", "warn"),
    "restore":     ("Wiederhergestellt", "↩", "off"),
    "purge":       ("Endgültig gelöscht", "✖", "bad"),
    "empty":       ("Papierkorb geleert", "✖", "bad"),
    "write":       ("Gespeichert / geändert", "💾", "ok"),
    "read":        ("Gelesen", "👁", "off"),
    "setting":     ("Einstellung geändert", "⚙", "warn"),
    "timer_off":   ("Abschalt-Timer: heruntergefahren", "⏻", "warn"),
    "timer_on":    ("Abschalt-Timer: wieder eingeschaltet", "⏻", "ok"),
    "timer_wait":  ("Abschalt-Timer: verschoben", "⏳", "warn"),
    "timer_cancel": ("Abschalt-Timer: abgebrochen", "⏻", "off"),
}

_lock = threading.Lock()
_host_cache: dict[str, str] = {}


# ---------------------------------------------------------------- Ein/Aus und Speicher

def _db_path(cfg) -> Path:
    return cfg["DB_PATH"].parent / "events.db"


def _connect(cfg) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(cfg), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def enabled(cfg=None) -> bool:
    """Steht im Fenster „⚙ Optionen“ des Dashboards (systemweite Admin-Option)."""
    cfg = cfg or current_app.config
    try:
        conn = sqlite3.connect(cfg["DB_PATH"])
        try:
            row = conn.execute("SELECT value FROM settings WHERE username = '' AND key = 'options'").fetchone()
        finally:
            conn.close()
        return bool(json.loads(row[0]).get("log_events")) if row else False
    except (sqlite3.Error, ValueError):
        return False


def _prune(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM events WHERE ts < ?", (time.time() - MAX_DAYS * 86400,))
    conn.execute("DELETE FROM events WHERE id <= (SELECT id FROM events ORDER BY id DESC LIMIT 1 OFFSET ?)", (MAX_ROWS,))


def _insert(cfg, rows: list[tuple]) -> None:
    with _lock:
        conn = _connect(cfg)
        try:
            conn.executemany("INSERT INTO events (ts, username, action, detail, size, ip, host, device, via) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            _prune(conn)
            conn.commit()
        finally:
            conn.close()


def clear(cfg) -> None:
    with _lock:
        conn = _connect(cfg)
        try:
            conn.execute("DELETE FROM events")
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------- Gerät erkennen

def device_name(ua: str) -> str:
    """Betriebssystem und Browser aus der Browser-Kennung (User-Agent), z. B. „Chrome auf Windows“."""
    if not ua:
        return "unbekanntes Gerät"
    if "PiNAS-App/" in ua:
        browser = "PiNAS-App"
    elif "Edg/" in ua:
        browser = "Edge"
    elif "OPR/" in ua:
        browser = "Opera"
    elif "SamsungBrowser/" in ua:
        browser = "Samsung Internet"
    elif "Firefox/" in ua or "FxiOS/" in ua:
        browser = "Firefox"
    elif "Chrome/" in ua or "CriOS/" in ua:
        browser = "Chrome"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = ua.split("/")[0][:30] or "Browser"
    if m := re.search(r"Android [\d.]+;\s*([^;)]+?)(?: Build/[^;)]*)?\)", ua):
        model = m.group(1).strip()
        system = f"Android ({model})" if model and model not in ("K", "wv") else "Android"
    elif "iPhone" in ua:
        system = "iPhone"
    elif "iPad" in ua:
        system = "iPad"
    elif "Windows" in ua:
        system = "Windows"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        system = "Mac"
    elif "CrOS" in ua:
        system = "ChromeOS"
    elif "Linux" in ua:
        system = "Linux"
    else:
        system = ""
    return f"{browser} auf {system}" if system else browser


def _resolve_later(cfg, ip: str) -> None:
    """Rechnernamen per Reverse-DNS nachschlagen (die Fritz!Box kennt z. B. „PC-Wohnzimmer.fritz.box“).
    Läuft im Hintergrund, weil die Antwort ein paar Sekunden dauern kann."""
    if not ip or ip in _host_cache:
        return
    _host_cache[ip] = ""

    def work():
        try:
            name = socket.gethostbyaddr(ip)[0]
        except (OSError, UnicodeError):
            name = ""
        _host_cache[ip] = name if name and name != ip else ""
        try:
            with _lock:
                conn = _connect(cfg)
                try:
                    conn.execute("UPDATE events SET host = ? WHERE ip = ? AND host IS NULL", (_host_cache[ip], ip))
                    conn.commit()
                finally:
                    conn.close()
        except sqlite3.Error:
            pass

    threading.Thread(target=work, name="pinas-rdns", daemon=True).start()


# ---------------------------------------------------------------- Protokollieren

def log(action: str, detail: str = "", size: int | None = None, username: str | None = None) -> None:
    """Ein Ereignis aus der Weboberfläche festhalten (nur wenn die Option eingeschaltet ist)."""
    if not has_request_context():
        return
    try:
        cfg = current_app.config
        if not enabled(cfg):
            return
        if username is None:
            username = g.user["username"] if g.get("user") else ""
        ip = request.remote_addr or ""
        host = _host_cache.get(ip)
        _insert(cfg, [(time.time(), username, action, detail, size, ip,
                       host if host is not None else None, device_name(request.headers.get("User-Agent", "")), "web")])
        if host is None:
            _resolve_later(cfg, ip)
    except Exception:  # noqa: BLE001 – das Protokoll darf nie eine Aktion des Benutzers verhindern
        current_app.logger.exception("Ereignis konnte nicht protokolliert werden")


def log_system(cfg, action: str, detail: str = "") -> None:
    """Ein Ereignis, das PiNAS selbst auslöst (kein Benutzer, kein Gerät)."""
    _insert(cfg, [(time.time(), "System", action, detail, None, "", "", "PiNAS", "system")])


def size_of(path: Path) -> int | None:
    """Größe einer Datei oder eines ganzen Ordners in Byte."""
    try:
        if not path.is_dir() or path.is_symlink():
            return path.lstat().st_size
        total = 0
        for dirpath, _, names in os.walk(path):
            for n in names:
                try:
                    total += (Path(dirpath) / n).lstat().st_size
                except OSError:
                    pass
        return total
    except OSError:
        return None


# ---------------------------------------------------------------- Netzlaufwerk (Samba full_audit)

# Samba schreibt mit dem Modul full_audit pro Zugriff eine Zeile ins Systemprotokoll, z. B.
#   anna|192.168.178.23|LAPTOP-ANNA|Gemeinsam|openat|ok|w|Urlaub/IMG_0001.jpg
# (Aufbau siehe deploy/smb-pinas.conf). Die Weboberfläche holt diese Zeilen regelmäßig ab.
_SAMBA_OPS = {"connect": "connect", "mkdirat": "mkdir", "renameat": "rename", "unlinkat": "delete"}
# Dateien, die Windows/macOS ständig im Hintergrund anfassen – das wäre nur Rauschen
_NOISE = re.compile(r"(^|/)(desktop\.ini|thumbs\.db|\.ds_store|\._.*|~\$.*|.*\.tmp|\.pinas-upload-.*)$", re.I)
_REPEAT = 300  # gleicher Zugriff auf dieselbe Datei innerhalb von 5 Minuten zählt nur einmal

_cursor_lock = threading.Lock()
_recent: dict[tuple, float] = {}


def _cursor_file(cfg) -> Path:
    return cfg["DB_PATH"].parent / "events-samba.cursor"


def reset_samba_cursor(cfg) -> None:
    """Beim Einschalten nur Zugriffe ab jetzt übernehmen, keine alten aus dem Systemprotokoll."""
    _cursor_file(cfg).unlink(missing_ok=True)


def _share_dir(cfg, share: str, user: str) -> Path | None:
    from .config import storage
    paths = storage(cfg)
    if share.lower() == "privat":
        return paths["users"] / user
    if share.lower() == "gemeinsam":
        return paths["shared"]
    return None


def _parse_samba(cfg, msg: str, ts: float) -> tuple | None:
    parts = msg.split("|")
    if len(parts) < 6:
        return None
    user, ip, machine, share, op, result, *args = parts
    if result != "ok":
        return None
    base = _share_dir(cfg, share, user)
    # Neuere Samba-Versionen schreiben den vollen Pfad – relativ zur Freigabe ist er lesbarer
    if base:
        prefix = str(base).rstrip("/") + "/"
        args = [a[len(prefix):] if a.startswith(prefix) else ("" if a == prefix[:-1] else a) for a in args]
    if op == "openat":
        # args: Modus (r/w) und Pfad. Nur Dateien, keine Ordner
        if len(args) < 2:
            return None
        mode, path = args[0], args[-1]
        action = "write" if mode == "w" else "read"
    elif op in _SAMBA_OPS:
        action = _SAMBA_OPS[op]
        path = " → ".join(args) if op == "renameat" else (args[-1] if args else share)
    else:
        return None
    if action != "connect":
        if path in ("", ".") or _NOISE.search(path) or ".papierkorb" in path.split("/")[0]:
            return None
        if op == "renameat" and len(args) >= 2 and args[1].startswith(".papierkorb"):
            return None
    size = None
    if base and action in ("read", "write"):
        target = base / path
        if target.is_dir():
            return None  # Ordner werden beim Durchblättern ständig geöffnet
        size = size_of(target)
    elif base and action == "delete" and (base / ".papierkorb" / path).exists():
        size = size_of(base / ".papierkorb" / path)  # Samba-Papierkorb behält den Ordnerpfad
    key = (user, ip, action, share, path)
    if action != "connect" and ts - _recent.get(key, 0) < _REPEAT:
        return None
    _recent[key] = ts
    detail = share if action == "connect" else f"{share}/{path}"
    device = f"Netzlaufwerk ({machine})" if machine and machine != ip else "Netzlaufwerk"
    return (ts, user, action, detail, size, ip, None, device, "smb")


def import_samba(cfg) -> int:
    """Neue Netzlaufwerk-Zugriffe aus dem Systemprotokoll (journald) übernehmen."""
    if not enabled(cfg):
        return 0
    if not _cursor_lock.acquire(blocking=False):
        return 0
    try:
        cursor_file = _cursor_file(cfg)
        try:
            cursor = cursor_file.read_text().strip()
        except OSError:
            cursor = ""
        cmd = ["journalctl", "-t", "smbd_audit", "-o", "json", "--no-pager", "-q"]
        # Gemerkt ist die Stelle im Systemprotokoll („Cursor“) oder, solange es noch keinen Eintrag gab,
        # der Zeitpunkt des letzten Abholens als „@Sekunden“
        if cursor.startswith("@"):
            cmd.append(f"--since={cursor}")
        else:
            cmd += [f"--after-cursor={cursor}"] if cursor else ["--since=-10min"]
        started = int(time.time())
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return 0
        rows, last = [], cursor
        for line in res.stdout.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            last = entry.get("__CURSOR", last)
            msg = entry.get("MESSAGE")
            if not isinstance(msg, str):
                continue
            ts = int(entry.get("__REALTIME_TIMESTAMP", time.time() * 1e6)) / 1e6
            row = _parse_samba(cfg, msg.strip(), ts)
            if row:
                rows.append(row)
        if rows:
            _insert(cfg, rows)
            for ip in {r[5] for r in rows}:
                _resolve_later(cfg, ip)
        if last and last != cursor:
            cursor_file.write_text(last)
        elif not cursor:
            cursor_file.write_text(f"@{started}")
        # Merkliste für Wiederholungen klein halten
        cutoff = time.time() - _REPEAT
        for k in [k for k, t in _recent.items() if t < cutoff]:
            _recent.pop(k, None)
        return len(rows)
    finally:
        _cursor_lock.release()


def start_importer(app, interval: int = 60) -> None:
    """Holt jede Minute die Netzlaufwerk-Zugriffe ab, auch wenn gerade niemand das Dashboard ansieht."""
    def loop():
        while True:
            try:
                import_samba(app.config)
            except Exception:  # noqa: BLE001 – der Hintergrund-Thread darf nie abbrechen
                app.logger.exception("Netzlaufwerk-Ereignisse konnten nicht gelesen werden")
            time.sleep(interval)

    threading.Thread(target=loop, name="pinas-events", daemon=True).start()


# ---------------------------------------------------------------- Anzeigen

def recent(cfg, limit: int = 30, offset: int = 0, username: str = "", kind: str = "") -> list[dict]:
    where, args = [], []
    if username:
        where.append("username = ?")
        args.append(username)
    if kind == "login":
        where.append("action IN ('login', 'login_fail', 'login_block', 'logout', 'connect')")
    elif kind == "files":
        where.append("action NOT IN ('login', 'login_fail', 'login_block', 'logout', 'connect', 'setting') "
                     "AND action NOT LIKE 'timer%'")
    sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"
    try:
        conn = _connect(cfg)
        try:
            rows = conn.execute(sql, (*args, limit, offset)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        text, icon, color = ACTIONS.get(r["action"], (r["action"], "•", "off"))
        device = r["device"]
        # „Netzlaufwerk (PC-WOHNZIMMER)“ – der Rechnername steht schon als Gerätename darüber
        m = re.fullmatch(r"Netzlaufwerk \((.+)\)", device)
        if m and r["host"] and r["host"].split(".")[0].lower() == m.group(1).lower():
            device = "Netzlaufwerk"
        out.append({**dict(r), "device": device, "text": text, "icon": icon, "color": color,
                    "time": time.strftime("%d.%m.%Y %H:%M:%S", time.localtime(r["ts"]))})
    return out


def usernames(cfg) -> list[str]:
    try:
        conn = _connect(cfg)
        try:
            return [r[0] for r in conn.execute("SELECT DISTINCT username FROM events WHERE username != '' ORDER BY 1")]
        finally:
            conn.close()
    except sqlite3.Error:
        return []
