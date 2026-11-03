"""System-Dashboard: Temperatur, Last, RAM, Speicherplatz, Laufwerke, Dienste."""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from flask import Blueprint, Response, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from . import crashlog, events, powertimer, transfers
from .auth import admin_required, helper, login_required
from .db import get_db
from .helper import HelperError

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
    return {"text": format_duration(secs), "since": since.strftime("%d.%m.%Y, %H:%M")}


def history() -> dict:
    """Gesamtbetriebszeit über alle Neustarts und höchste CPU-Temperatur (siehe history.py)."""
    hist = current_app.extensions["pinas_history"]
    hist.sample()
    h = hist.snapshot()
    fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%d.%m.%Y, %H:%M") if ts else None
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


# ---------------------------------------------------------------- Energieverbrauch

# Raspberry Pi: Der Stromchip (PMIC) misst Spannung und Strom seiner Versorgungsschienen. Was er nicht
# sieht (Netzwerk-/USB-Chip, Spannungswandler), kommt als pauschaler Grundverbrauch dazu.
PI_BOARD_OVERHEAD = 0.6  # Watt
# USB-Festplatte: Der Pi misst den Strom am USB-Anschluss nicht – typische Werte je Zustand.
DISK_WATTS = {
    "hdd": {"active": 2.5, "idle": 1.6, "standby": 0.3},
    "ssd": {"active": 1.5, "idle": 0.4, "standby": 0.4},
}
PRICE_PER_KWH = 0.35  # Euro, nur für die grobe Monatsrechnung

_disk_prev: dict = {}


def pi_power() -> float | None:
    """Leistung des Raspberry Pi in Watt (Summe der PMIC-Schienen + Grundverbrauch)."""
    out = _cmd("vcgencmd", "pmic_read_adc")
    if not out:
        return None
    amps, volts = {}, {}
    for line in out.splitlines():
        # z. B. "VDD_CORE_A current(7)=2.12345000A" und "VDD_CORE_V volt(15)=0.81234000V"
        name, _, value = line.strip().partition("=")
        rail = name.split(" ")[0]
        try:
            number = float(value.rstrip("AV"))
        except ValueError:
            continue
        if rail.endswith("_A"):
            amps[rail[:-2]] = number
        elif rail.endswith("_V"):
            volts[rail[:-2]] = number
    watts = sum(volts[r] * amps[r] for r in amps if r in volts)
    return round(watts + PI_BOARD_OVERHEAD, 2) if watts > 0 else None


def disk_power(io: dict | None) -> dict | None:
    """Geschätzte Leistung der USB-Festplatte aus Plattentyp und Zustand (aktiv/bereit/Ruhezustand)."""
    if (RUN_DIR / "disk-off").exists():
        return {"watts": 0.0, "state": "ausgeschaltet", "kind": ""}
    if not io or not io["device"].startswith("sd"):
        return None  # keine USB-Platte (Daten auf der SD-Karte)
    disk = io["device"].rstrip("0123456789")
    kind = "hdd" if _read(f"/sys/block/{disk}/queue/rotational") == "1" else "ssd"
    try:
        asleep = json.loads(_read("/run/pinas-diskpower.json") or "{}").get("asleep", False)
    except ValueError:
        asleep = False
    # Aktiv, wenn seit der letzten Messung gelesen oder geschrieben wurde
    now, sectors = time.monotonic(), io["read"] + io["written"]
    prev = _disk_prev.get(disk)
    _disk_prev[disk] = (now, sectors)
    busy = bool(prev) and sectors > prev[1] and now - prev[0] < 120
    state = "standby" if asleep and not busy else ("active" if busy else "idle")
    labels = {"active": "arbeitet", "idle": "bereit", "standby": "Ruhezustand"}
    return {"watts": DISK_WATTS[kind][state], "state": labels[state], "kind": kind.upper()}


def energy(io: dict | None) -> dict | None:
    pi = pi_power()
    disk = disk_power(io)
    if pi is None and disk is None:
        return None
    total = (pi or 0) + (disk["watts"] if disk else 0)
    kwh_month = total * 24 * 30 / 1000
    return {"pi": pi, "disk": disk, "total": round(total, 1),
            "kwh_month": round(kwh_month, 1), "euro_month": round(kwh_month * PRICE_PER_KWH, 2)}


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


def system_disk() -> str | None:
    """Laufwerk, auf dem Raspberry Pi OS und PiNAS selbst laufen (meist die SD-Karte "mmcblk0")."""
    source = _cmd("findmnt", "-no", "SOURCE", "/")
    return _cmd("lsblk", "-no", "PKNAME", source) if source else None


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
    io = disk_io()
    return {
        "temp": cpu_temp(),
        "load": [round(x, 2) for x in load] if load else None,
        "cpus": os.cpu_count(),
        "cpu": cpu(),
        "memory": memory(),
        "uptime": uptime(),
        "history": history(),
        "io": io,
        "energy": energy(io),
        "net": net_bytes(),
        "throttled": throttled(),
        "disk": {"total": du.total, "used": du.used, "free": du.free, "path": str(data_dir),
                 "is_mount": os.path.ismount(data_dir)},
    }


RUN_DIR = Path("/run/pinas")  # Zustand der Ein/Aus-Knöpfe (geschrieben von pinas-helper)


def power_state() -> dict:
    """Ist die Festplatte ausgeschaltet, und ist ein Herunterfahren/Neustart geplant?"""
    state = {"disk_off": (RUN_DIR / "disk-off").exists(), "pending": None}
    try:
        p = json.loads((RUN_DIR / "power.json").read_text())
        if p.get("at", 0) > time.time() - 10:
            p["at_text"] = datetime.fromtimestamp(p["at"]).strftime("%H:%M:%S Uhr")
            state["pending"] = p
    except (OSError, ValueError):
        pass
    return state


_WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def pi_now() -> dict:
    """Datum und Uhrzeit des Pi für die Kopfzeile; ms und tz (Sekunden zu UTC) zum Weiterzählen im Browser."""
    now = time.time()
    t = time.localtime(now)
    return {"ms": int(now * 1000), "tz": t.tm_gmtoff or 0, "time": f"{t.tm_hour:02}:{t.tm_min:02}",
            "date": f"{_WEEKDAYS[t.tm_wday]}, {t.tm_mday:02}.{t.tm_mon:02}.{t.tm_year}"}


@bp.app_context_processor
def inject_power():
    # Für die Kopfzeile (Ein/Aus-Knöpfe, Uhrzeit) und den Herunterfahr-Hinweis auf jeder Seite
    return {"power": power_state(), "pi_now": pi_now()} if g.get("user") else {}


# Knöpfe oben rechts: URL-Teil → (Helper-Aktion, Meldung danach)
POWER_ACTIONS = {
    "herunterfahren": ("shutdown", "PiNAS fährt jetzt herunter. Nach etwa 30 Sekunden kannst du den Strom trennen."),
    "neustart": ("reboot", "PiNAS startet neu – in etwa 1–2 Minuten ist es wieder erreichbar."),
    "sicher-herunterfahren": ("safe-shutdown", "Festplatte ist ausgeschaltet. PiNAS fährt in 2 Minuten herunter."),
    "festplatte-aus": ("disk-off", "Festplatte ist ausgeschaltet. Netzlaufwerk und Dateien sind nicht erreichbar, bis du sie wieder einschaltest."),
    "festplatte-an": ("disk-on", "Festplatte ist wieder eingeschaltet."),
    "abbrechen": ("cancel", "Geplantes Herunterfahren abgebrochen."),
}
# Nicht, solange jemand über die Weboberfläche Dateien hoch- oder herunterlädt
_NEEDS_IDLE = {"shutdown", "reboot", "safe-shutdown", "disk-off"}


@bp.post("/system/<action>")
@admin_required
def power(action):
    if action not in POWER_ACTIONS:
        return redirect(url_for("dashboard.index"))
    cmd, message = POWER_ACTIONS[action]
    running = transfers.busy() if cmd in _NEEDS_IDLE else []
    try:
        if running:
            flash(transfers.describe(running), "error")
        else:
            helper().power(cmd)
            flash(message, "ok")
    except HelperError as exc:
        flash(str(exc), "error")
    # Zurück auf die Seite, von der aus geklickt wurde (die Knöpfe stehen in der Kopfzeile jeder Seite)
    ref = urlparse(request.referrer or "")
    if ref.netloc == request.host and ref.path.startswith("/"):
        return redirect(ref.path + (f"?{ref.query}" if ref.query else ""))
    return redirect(url_for("dashboard.index"))


# Kacheln und Bereiche des Dashboards in der Standard-Reihenfolge
TILES = ["disk", "uptime", "io", "net", "temp", "cpu", "mem", "power", "timer", "events", "errors", "services", "devices"]


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


# Systemweite Optionen, die nur Administratoren ändern (in settings unter dem Benutzernamen "")
OPTION_DEFAULTS = {"show_system_disk": False, "log_events": False}


def options() -> dict:
    row = get_db().execute("SELECT value FROM settings WHERE username = '' AND key = 'options'").fetchone()
    try:
        saved = json.loads(row["value"]) if row else {}
    except ValueError:
        saved = {}
    return {k: bool(saved.get(k, v)) for k, v in OPTION_DEFAULTS.items()}


@bp.post("/dashboard/optionen")
@admin_required
def save_options():
    new = {k: request.form.get(k) == "1" for k in OPTION_DEFAULTS}
    old = options()
    if old["log_events"] and not new["log_events"]:
        events.log("setting", "Ereignis-Protokoll ausgeschaltet")
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (username, key, value) VALUES ('', 'options', ?)", (json.dumps(new),))
    conn.commit()
    if new["log_events"] != old["log_events"]:
        if new["log_events"]:
            events.reset_samba_cursor(current_app.config)  # nur neue Netzlaufwerk-Zugriffe übernehmen
            events.log("setting", "Ereignis-Protokoll eingeschaltet")
        try:
            helper().samba_audit(new["log_events"])
        except HelperError as exc:
            flash(f"Zugriffe übers Netzlaufwerk werden nicht protokolliert: {exc}", "error")
    flash("Optionen gespeichert.", "ok")
    return redirect(url_for("dashboard.index"))


_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@bp.post("/dashboard/abschalttimer")
@admin_required
def timer_save():
    f = request.form
    new = {"enabled": f.get("enabled") == "1", "off": f.get("off", ""), "on": f.get("on", ""),
           "wake": f.get("wake") == "1",
           "off_days": [d for d in range(7) if f"off_{d}" in f], "on_days": [d for d in range(7) if f"on_{d}" in f]}
    status = powertimer.wake_status()["state"]
    if not (_TIME_RE.match(new["off"]) and _TIME_RE.match(new["on"])):
        flash("Bitte gültige Uhrzeiten eintragen.", "error")
    elif new["enabled"] and not new["off_days"]:
        flash("Bitte mindestens einen Tag zum Abschalten wählen.", "error")
    elif new["enabled"] and new["wake"] and not new["on_days"]:
        flash("Bitte mindestens einen Tag zum Einschalten wählen.", "error")
    elif new["enabled"] and new["wake"] and new["off"] == new["on"]:
        flash("Abschalten und Einschalten dürfen nicht zur selben Uhrzeit sein.", "error")
    elif new["enabled"] and new["wake"] and status not in ("ready", "pending"):
        flash("Das Einschalten per Uhr ist noch nicht eingerichtet: Richte es zuerst ein "
              "oder nimm das Häkchen bei „Automatisch wieder einschalten“ weg.", "error")
    else:
        try:
            powertimer.save_config(current_app.config, helper(), new)
            events.log("setting", "Abschalt-Timer " + (f"eingeschaltet: aus {new['off']} Uhr" +
                       (f", an {new['on']} Uhr" if new["wake"] else "") if new["enabled"] else "ausgeschaltet"))
            flash("Abschalt-Timer gespeichert.", "ok")
        except HelperError as exc:
            flash(str(exc), "error")
    return redirect(url_for("dashboard.index") + "#abschalttimer")


@bp.post("/dashboard/abschalttimer/uhr-einrichten")
@admin_required
def timer_setup():
    try:
        helper().rtc_wake_setup()
        events.log("setting", "Einschalten per Uhr eingerichtet (Bootloader)")
        flash("Vorbereitet. Bitte starte PiNAS jetzt einmal neu, erst dann ist es eingerichtet.", "ok")
    except HelperError as exc:
        flash(str(exc), "error")
    return redirect(url_for("dashboard.index") + "#abschalttimer")


@bp.post("/dashboard/abschalttimer/probelauf")
@admin_required
def timer_test():
    """PiNAS jetzt abschalten und nach wenigen Minuten von der Uhr wecken lassen."""
    running = transfers.busy()
    if powertimer.wake_status()["state"] != "ready":
        flash("Für den Probelauf muss das Einschalten per Uhr eingerichtet und PiNAS einmal neu gestartet sein.", "error")
    elif running:
        flash(transfers.describe(running), "error")
    else:
        try:
            secs = powertimer.TEST_SECONDS
            powertimer.shutdown(current_app.config, helper(), secs, time.time() + secs, label="Probelauf. ")
            flash(f"Probelauf: PiNAS fährt herunter und schaltet sich in etwa {secs // 60} Minuten wieder ein.", "ok")
        except HelperError as exc:
            flash(str(exc), "error")
    return redirect(url_for("dashboard.index") + "#abschalttimer")


@bp.get("/dashboard/ereignisse")
@admin_required
def events_page():
    """Alle Ereignisse mit Filter nach Benutzer und Art, 100 pro Seite."""
    if not options()["log_events"]:
        return redirect(url_for("dashboard.index"))
    events.import_samba(current_app.config)
    who, kind = request.args.get("wer", ""), request.args.get("art", "")
    page = max(1, request.args.get("seite", 1, type=int))
    rows = events.recent(current_app.config, 101, (page - 1) * 100, who, kind)
    return render_template("events.html", rows=rows[:100], more=len(rows) > 100, page=page, who=who, kind=kind,
                           names=events.usernames(current_app.config),
                           max_rows=events.MAX_ROWS, max_days=events.MAX_DAYS)


@bp.post("/dashboard/ereignisse/leeren")
@admin_required
def events_clear():
    events.clear(current_app.config)
    flash("Ereignis-Protokoll geleert.", "ok")
    return redirect(url_for("dashboard.events_page"))


@bp.get("/api/ereignisse")
@admin_required
def api_events():
    """Neueste Ereignisse als fertige Tabellenzeilen (die Kachel aktualisiert sich alle 15 Sekunden)."""
    if not options()["log_events"]:
        return jsonify(html="")
    events.import_samba(current_app.config)
    return jsonify(html=render_template("_events_rows.html", rows=events.recent(current_app.config, EVENTS_IN_TILE)))


EVENTS_IN_TILE = 15


# Was der Benutzer selbst tun kann, passend zur Art des Fehlers
_ERROR_HINTS = {
    "power": "Das liegt fast immer am Netzteil. Der Pi 5 mit Festplatte braucht das offizielle 5-V-/5-A-Netzteil. "
             "Dateien können sonst beschädigt werden.",
    "reboot": "Das passiert bei einem Stromausfall, wenn der Stecker gezogen wird oder wenn der Pi abstürzt. "
              "Kommt es öfter vor, schick den Fehlerbericht.",
    "disk": "Die Festplatte oder ihre Verbindung macht Probleme. Prüf das USB-Kabel und sichere wichtige Dateien.",
    "space": "Lösch nicht mehr benötigte Dateien und leere den Papierkorb.",
    "memory": "Der Arbeitsspeicher wurde knapp. Hilft ein Neustart nicht, schick den Fehlerbericht.",
}


def _error_state() -> list[dict]:
    rows = []
    for unit, label in (("nginx", "Verschlüsselter Zugang (nginx)"), ("smbd", "Netzlaufwerk"), ("pinas-rescue", "Notfallhelfer")):
        info = service_info(unit)
        rows.append({"label": label, "color": info["color"], "text": info["state"]})
    power = throttled()
    if power:
        rows.insert(0, {"label": "Stromversorgung und Temperatur", "text": "in Ordnung" if power == "OK" else power,
                        "color": "ok" if power == "OK" else "bad"})
    return rows


@bp.get("/dashboard/fehler")
@admin_required
def errors_page():
    rows = [{**e, "hint": _ERROR_HINTS.get(e.get("kind"), "")} for e in crashlog.read()]
    return render_template("errors.html", rows=rows, state=_error_state())


@bp.get("/dashboard/fehler/bericht")
@admin_required
def errors_report():
    name = "pinas-fehlerbericht-" + datetime.now().strftime("%Y-%m-%d-%H%M") + ".txt"
    return Response(crashlog.report(), mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@bp.post("/dashboard/fehler/test")
@admin_required
def errors_test():
    """Zeigt auf dem echten Pi, ohne Anmeldung am Gerät, dass das Aufschreiben funktioniert."""
    raise RuntimeError("Testfehler: vom Administrator ausgelöst, alles in Ordnung")


@bp.post("/dashboard/fehler/leeren")
@admin_required
def errors_clear():
    crashlog.clear()
    try:
        helper().crash_report_clear()
    except HelperError:
        pass
    flash("Fehlerprotokoll geleert.", "ok")
    return redirect(url_for("dashboard.errors_page"))


@bp.get("/dashboard")
@login_required
def index():
    services = [{**svc, **service_info(svc["unit"])} for svc in SERVICES]
    opts = options()
    sysdisk = system_disk()
    devices = block_devices()
    for d in devices:
        d["system"] = d["name"] == sysdisk
    # Das Laufwerk, auf dem PiNAS selbst läuft, ist verborgen – nur ein Admin kann es per Option einblenden
    if not (g.user["is_admin"] and opts["show_system_disk"]):
        devices = [d for d in devices if not d["system"]]
    data_dir = str(current_app.config["DATA_DIR"])
    data_alias = next((d["alias"] for d in devices if any(p["mount"] == data_dir for p in d["parts"])), "")
    event_rows = None
    if g.user["is_admin"] and opts["log_events"]:
        events.import_samba(current_app.config)
        event_rows = events.recent(current_app.config, EVENTS_IN_TILE)
    return render_template("dashboard.html", s=stats(), devices=devices, services=services,
                           data_alias=data_alias, layout=layout(), power=power_state(), options=opts,
                           event_rows=event_rows,
                           errors=crashlog.summary() if g.user["is_admin"] else None,
                           timer=powertimer.view(current_app.config) if g.user["is_admin"] else None)


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
