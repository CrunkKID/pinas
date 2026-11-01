"""System-Dashboard: Temperatur, Last, RAM, Speicherplatz, Laufwerke, Dienste."""
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, render_template, request

from .auth import login_required
from .db import get_db

bp = Blueprint("dashboard", __name__)


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _cmd(*args: str) -> str | None:
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return res.stdout.strip() if res.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def cpu_temp() -> float | None:
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    return round(int(raw) / 1000, 1) if raw and raw.isdigit() else None


_cpu_prev: tuple | None = None
_cpu_lock = threading.Lock()


def _cpu_times() -> tuple | None:
    """(Zeitpunkt, CPU-Zeit gesamt, davon untätig) aus /proc/stat."""
    raw = _read("/proc/stat")
    if not raw:
        return None
    f = [int(x) for x in raw.splitlines()[0].split()[1:9]]
    return time.monotonic(), sum(f), f[3] + f[4]  # idle + iowait (Warten auf die Platte ist keine Arbeit)


# Klartext zur Auslastung: (bis Prozent, Text, Farbe)
_CPU_LEVELS = [(25, "Wenig zu tun", "ok"), (60, "Normal beschäftigt", "ok"),
               (90, "Viel zu tun", "warn"), (101, "Voll ausgelastet", "bad")]


def cpu() -> dict | None:
    """Prozessor-Auslastung in Prozent seit der letzten Abfrage (das Dashboard fragt alle 5 s)."""
    global _cpu_prev
    now = _cpu_times()
    if not now:
        return None
    with _cpu_lock:
        prev, _cpu_prev = _cpu_prev, now
    if prev is None or now[0] - prev[0] > 30 or now[1] <= prev[1]:
        # Erste oder veraltete Messung: kurz selbst messen
        time.sleep(0.3)
        prev, now = now, _cpu_times()
        with _cpu_lock:
            _cpu_prev = now
    total, idle = now[1] - prev[1], now[2] - prev[2]
    pct = round(max(0.0, min(100.0, 100 * (total - idle) / total))) if total > 0 else 0
    text, color = next((t, c) for limit, t, c in _CPU_LEVELS if pct < limit)
    load = os.getloadavg()[2] if hasattr(os, "getloadavg") else None
    cores = os.cpu_count() or 1
    return {"pct": pct, "text": text, "color": color, "cores": cores,
            # Systemlast der letzten 15 Minuten, umgerechnet auf Prozent aller Kerne
            "avg15": round(min(100.0, load / cores * 100)) if load is not None else None}


def memory() -> dict | None:
    raw = _read("/proc/meminfo")
    if not raw:
        return None
    info = {}
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        info[key] = int(rest.split()[0]) * 1024
    total, avail = info.get("MemTotal", 0), info.get("MemAvailable", 0)
    return {"total": total, "used": total - avail}


def format_duration(secs: float) -> str:
    d, rem = divmod(int(secs), 86400)
    h, rem = divmod(rem, 3600)
    parts = ([f"{d} {'Tag' if d == 1 else 'Tage'}"] if d else []) + [f"{h} Std", f"{rem // 60} Min"]
    return " ".join(parts)


def uptime() -> dict | None:
    raw = _read("/proc/uptime")
    if not raw:
        return None
    secs = int(float(raw.split()[0]))
    since = datetime.now() - timedelta(seconds=secs)
    return {"text": format_duration(secs), "since": since.strftime("%d.%m.%Y, %H:%M Uhr")}


def history() -> dict:
    """Gesamtbetriebszeit über alle Neustarts und höchste CPU-Temperatur (siehe history.py)."""
    hist = current_app.extensions["pinas_history"]
    hist.sample()
    h = hist.snapshot()
    fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%d.%m.%Y, %H:%M Uhr") if ts else None
    return {
        "total_text": format_duration(h["total"]) if h["since"] else None,
        "since_text": fmt(h["since"]),
        "temp_max": h["temp_max"],
        "temp_max_at_text": fmt(h["temp_max_at"]),
    }


def disk_io() -> dict | None:
    """Seit dem Einschalten gelesene/geschriebene Bytes des Laufwerks mit den NAS-Daten."""
    source = _cmd("findmnt", "-no", "SOURCE", "-T", str(current_app.config["DATA_DIR"]))
    raw = _read("/proc/diskstats")
    if not source or not source.startswith("/dev/") or not raw:
        return None
    dev = source.rsplit("/", 1)[-1]
    for line in raw.splitlines():
        f = line.split()
        if len(f) > 9 and f[2] == dev:
            # Felder 6 und 10: gelesene/geschriebene Sektoren (immer 512 Byte)
            return {"device": dev, "read": int(f[5]) * 512, "written": int(f[9]) * 512}
    return None


def net_bytes() -> dict | None:
    """Seit dem Einschalten empfangene/gesendete Bytes aller Netzwerkschnittstellen (ohne lo)."""
    raw = _read("/proc/net/dev")
    if not raw:
        return None
    rx = tx = 0
    for line in raw.splitlines()[2:]:
        name, _, data = line.partition(":")
        f = data.split()
        if name.strip() == "lo" or len(f) < 9:
            continue
        rx += int(f[0])
        tx += int(f[8])
    return {"rx": rx, "tx": tx}


def throttled() -> str | None:
    """Pi-spezifisch: meldet Unterspannung/Drosselung (wichtig bei USB-Platten!)."""
    out = _cmd("vcgencmd", "get_throttled")
    if not out or "=" not in out:
        return None
    flags = int(out.split("=")[1], 16)
    problems = []
    if flags & 0x1:
        problems.append("Unterspannung jetzt")
    elif flags & 0x10000:
        problems.append("Unterspannung seit Start aufgetreten")
    if flags & 0x4:
        problems.append("CPU gedrosselt")
    if flags & 0x8:
        problems.append("Temperaturlimit erreicht")
    return ", ".join(problems) or "OK"


def _names_file() -> Path:
    return current_app.config["DB_PATH"].parent / "disk-names.json"


def disk_names() -> dict:
    """Vom Nutzer vergebene Namen, Schlüssel ist die stabile Kennung der Platte (disk_key)."""
    try:
        return json.loads(_names_file().read_text())
    except (OSError, ValueError):
        return {}


def save_disk_names(names: dict) -> None:
    path = _names_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(names, ensure_ascii=False))
    os.replace(tmp, path)


def disk_key(disk: dict) -> str:
    """Bleibt gleich, auch wenn die Platte an einem anderen USB-Port als sdb statt sda auftaucht."""
    serial = (disk.get("serial") or "").strip()
    model = (disk.get("model") or "").strip()
    return f"serial:{serial}" if serial else f"model:{model}:{disk.get('size')}"


def block_devices() -> list[dict]:
    out = _cmd("lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,TRAN,LABEL,SERIAL")
    if not out:
        return []
    names = disk_names()
    devices = []
    for disk in json.loads(out).get("blockdevices", []):
        if disk.get("type") != "disk" or disk["name"].startswith(("loop", "zram", "ram")):
            continue
        key = disk_key(disk)
        devices.append({
            "name": disk["name"],
            "key": key,
            "alias": names.get(key, ""),
            "model": (disk.get("model") or "").strip() or "–",
            "tran": disk.get("tran") or "–",
            "size": int(disk.get("size") or 0),
            "parts": [
                {"name": p["name"], "size": int(p.get("size") or 0), "fstype": p.get("fstype") or "–",
                 "mount": p.get("mountpoint") or "", "label": p.get("label") or ""}
                for p in disk.get("children", [])
            ],
        })
    return devices


# Systemdienste mit verständlichem Namen; "optional": fehlt der Dienst, ist das kein Fehler
SERVICES = [
    {"unit": "smbd", "label": "Netzlaufwerk",
     "desc": "Stellt „Privat“ und „Gemeinsam“ als Laufwerk für Windows, Mac und Handy bereit", "optional": False},
    {"unit": "avahi-daemon", "label": "Adresse pinas.local",
     "desc": "Damit das NAS unter seinem Namen statt über die IP-Adresse erreichbar ist", "optional": False},
    {"unit": "wsdd", "label": "Windows-Netzwerkumgebung",
     "desc": "Zeigt das NAS im Windows-Explorer unter „Netzwerk“ an", "optional": True},
    {"unit": "nmbd", "label": "Gerätename für ältere Geräte",
     "desc": "Findet das NAS mit älteren Windows-Versionen und Geräten (NetBIOS)", "optional": True},
    {"unit": "pinas-diskpower.timer", "label": "Energiesparen",
     "desc": "Prüft jede Minute, ob die Festplatte schlafen gelegt werden kann", "optional": True},
]

_STATE_TEXT = {"active": "läuft", "activating": "startet …", "reloading": "startet neu …",
               "deactivating": "wird beendet …", "inactive": "gestoppt", "failed": "Fehler"}


def service_info(unit: str) -> dict:
    """Zustand eines systemd-Dienstes als Text und Farbe (ok / bad / off)."""
    try:
        res = subprocess.run(["systemctl", "show", "-p", "LoadState", "-p", "ActiveState", unit],
                             capture_output=True, text=True, timeout=5)
        props = dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)
    except (OSError, subprocess.TimeoutExpired):
        return {"state": "unbekannt", "color": "off"}
    if props.get("LoadState") == "not-found":
        return {"state": "nicht installiert", "color": "off"}
    active = props.get("ActiveState", "")
    color = "ok" if active == "active" else ("off" if active.endswith("ing") else "bad")
    return {"state": _STATE_TEXT.get(active, active or "unbekannt"), "color": color}


def stats() -> dict:
    data_dir = current_app.config["DATA_DIR"]
    du = shutil.disk_usage(data_dir)
    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {
        "temp": cpu_temp(),
        "load": [round(x, 2) for x in load] if load else None,
        "cpus": os.cpu_count(),
        "cpu": cpu(),
        "memory": memory(),
        "uptime": uptime(),
        "history": history(),
        "io": disk_io(),
        "net": net_bytes(),
        "throttled": throttled(),
        "disk": {"total": du.total, "used": du.used, "free": du.free, "path": str(data_dir),
                 "is_mount": os.path.ismount(data_dir)},
    }


# Kacheln und Bereiche des Dashboards in der Standard-Reihenfolge
TILES = ["disk", "uptime", "io", "net", "temp", "cpu", "mem", "services", "devices"]


def layout() -> dict:
    """Persönliche Anordnung des angemeldeten Benutzers: Reihenfolge und ausgeblendete Kacheln."""
    row = get_db().execute("SELECT value FROM settings WHERE username = ? AND key = 'dashboard'",
                           (g.user["username"],)).fetchone()
    try:
        saved = json.loads(row["value"]) if row else {}
    except ValueError:
        saved = {}
    order = [t for t in saved.get("order", []) if t in TILES]
    order = list(dict.fromkeys(order)) + [t for t in TILES if t not in order]  # neue Kacheln hinten anhängen
    hidden = [t for t in saved.get("hidden", []) if t in TILES]
    return {"order": order, "hidden": hidden, "pos": {t: i for i, t in enumerate(order)}}


@bp.get("/dashboard")
@login_required
def index():
    services = [{**svc, **service_info(svc["unit"])} for svc in SERVICES]
    devices = block_devices()
    data_dir = str(current_app.config["DATA_DIR"])
    data_alias = next((d["alias"] for d in devices if any(p["mount"] == data_dir for p in d["parts"])), "")
    return render_template("dashboard.html", s=stats(), devices=devices, services=services,
                           data_alias=data_alias, layout=layout())


@bp.post("/api/dashboard-layout")
@login_required
def save_layout():
    data = request.get_json(silent=True) or {}
    order = [t for t in data.get("order", []) if t in TILES]
    hidden = [t for t in data.get("hidden", []) if t in TILES]
    conn = get_db()
    if data.get("reset"):
        conn.execute("DELETE FROM settings WHERE username = ? AND key = 'dashboard'", (g.user["username"],))
    else:
        conn.execute("INSERT OR REPLACE INTO settings (username, key, value) VALUES (?, 'dashboard', ?)",
                     (g.user["username"], json.dumps({"order": order, "hidden": hidden})))
    conn.commit()
    return jsonify(ok=True)


@bp.get("/api/stats")
@login_required
def api_stats():
    return jsonify(stats())


@bp.get("/api/netz")
@login_required
def api_net():
    """Leichtgewichtig, wird alle 2 Sekunden für die Live-Geschwindigkeit abgefragt."""
    return jsonify(net_bytes() or {})
