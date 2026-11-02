"""Abschalt-Timer: fährt das ganze System zu festen Zeiten herunter und per Uhr wieder hoch (nur Admins).

Einschalten: Der Raspberry Pi 5 hat eine Uhr (RTC) mit Weckfunktion. Der Weckruf bleibt ständig auf die
nächste Einschaltzeit gestellt, also weckt sie auch nach einem Herunterfahren von Hand. Er wird als
Abstand in Sekunden gesetzt und stimmt deshalb auch dann, wenn die Uhr selbst eine falsche Zeit zeigt.

Abschalten: 5 Minuten vorher erscheint auf jeder Seite der Hinweis mit Restzeit und „Abbrechen“.
Läuft dann etwas (Übertragung, Update, geöffnete Datei am Netzlaufwerk), wird um 5 Minuten verschoben.

Nach einem Neustart oder Stromausfall innerhalb der Aus-Zeit bleibt PiNAS bis zur nächsten Abschaltzeit an:
Wer den Einschaltknopf drückt, will das NAS benutzen, und ein Stromausfall sieht genauso aus.
"""
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import events, transfers
from .helper import HelperError

DEFAULTS = {"enabled": False, "off": "23:00", "off_days": [0, 1, 2, 3, 4, 5, 6],
            "on": "07:00", "on_days": [0, 1, 2, 3, 4, 5, 6], "wake": True}
DAY_NAMES = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
ACTION = "timer-shutdown"  # Eintrag in power.json (pinas-helper), zeigt den Hinweis auf jeder Seite
WARN = 300  # Sekunden Vorwarnung
POSTPONE = 300  # Sekunden, um die verschoben wird, wenn gerade etwas läuft
SAMBA_WAIT = 3600  # Länger wartet der Timer nicht auf offene Dateien am Netzlaufwerk (die bleiben oft einfach offen)
SKIP_IF_ON_WITHIN = 900  # Verschoben bis kurz vor die Einschaltzeit: Abschalten lohnt nicht mehr
TEST_SECONDS = 180  # Probelauf: so lange bleibt PiNAS aus

RTC = Path("/sys/class/rtc/rtc0")
POWER_JSON = Path("/run/pinas/power.json")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
EEPROM_PENDING = Path("/boot/firmware/recovery.bin")  # liegt nur bereit, solange eine Änderung auf den Neustart wartet

_lock = threading.Lock()


def _cmd(*args: str) -> str | None:
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return res.stdout.strip() if res.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def _load(path: Path, default: dict) -> dict:
    try:
        return {**default, **json.loads(path.read_text())}
    except (OSError, ValueError):
        return dict(default)


def _save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def _config_file(cfg) -> Path:
    return cfg["DB_PATH"].parent / "power-timer.json"


def _state_file(cfg) -> Path:
    return cfg["DB_PATH"].parent / "power-timer-state.json"


def load_config(cfg) -> dict:
    return _load(_config_file(cfg), DEFAULTS)


def save_config(cfg, helper, new: dict) -> None:
    _save(_config_file(cfg), new)
    # Neue Zeiten gelten sofort: eine bereits angekündigte Abschaltung zählt nicht mehr
    with _lock:
        st = _load(_state_file(cfg), {})
        st.pop("skip", None)
        if st.pop("due", None):
            helper.power("cancel")
        _save(_state_file(cfg), st)


# ---------------------------------------------------------------- Zeiten

def _next_time(hhmm: str, days: list[int], now: float) -> float | None:
    """Nächster Zeitpunkt nach `now`, an dem es an einem der Wochentage hhmm Uhr ist."""
    h, m = map(int, hhmm.split(":"))
    start = datetime.fromtimestamp(now).replace(hour=h, minute=m, second=0, microsecond=0)
    for i in range(8):
        day = start + timedelta(days=i)
        if day.weekday() in days and day.timestamp() > now:
            return day.timestamp()
    return None


def next_off(c: dict, now: float) -> float | None:
    return _next_time(c["off"], c["off_days"], now) if c["enabled"] else None


def next_on(c: dict, now: float) -> float | None:
    return _next_time(c["on"], c["on_days"], now) if c["enabled"] and c["wake"] else None


def _fmt(ts: float | None) -> str:
    if not ts:
        return ""
    d = datetime.fromtimestamp(ts)
    return f"{DAY_NAMES[d.weekday()]}, {d.strftime('%d.%m., %H:%M')} Uhr"


def clock_ok(now: float) -> bool:
    """Stimmt die Uhrzeit? Sonst nichts tun: Nach einem Stromausfall ohne Internet könnte sie falsch sein."""
    if _cmd("timedatectl", "show", "-p", "NTPSynchronized", "--value") == "yes":
        return True
    rtc = _read(RTC / "since_epoch")
    return rtc.isdigit() and abs(int(rtc) - now) < 300


# ---------------------------------------------------------------- Uhr und Bootloader

def wake_status() -> dict:
    """Kann die Uhr den Pi wecken? state: ready, pending (wartet auf Neustart), setup, unsupported."""
    model = _read(Path("/proc/device-tree/model")).rstrip("\x00")
    if "Raspberry Pi 5" not in model or not (RTC / "wakealarm").exists():
        return {"state": "unsupported", "text": "Einschalten per Uhr gibt es nur auf dem Raspberry Pi 5."}
    conf = {}
    for line in (_cmd("vcgencmd", "bootloader_config") or "").splitlines():
        key, _, value = line.partition("=")
        conf[key.strip()] = value.strip()
    if conf.get("POWER_OFF_ON_HALT") == "1" and conf.get("WAKE_ON_GPIO", "0") != "1":
        return {"state": "ready", "text": "Einschalten per Uhr ist eingerichtet."}
    if EEPROM_PENDING.exists():
        return {"state": "pending", "text": "Einrichtung vorbereitet – sie wird beim nächsten Neustart übernommen."}
    return {"state": "setup", "text": "Einschalten per Uhr ist noch nicht eingerichtet."}


def _sync_alarm(c: dict, helper, now: float) -> None:
    """Weckruf der Uhr auf die nächste Einschaltzeit stellen. Klingelt er bei laufendem Pi, passiert nichts."""
    if not (RTC / "wakealarm").exists():
        return
    current = _read(RTC / "wakealarm")
    target = next_on(c, now)
    if target is None:
        if current:
            helper.rtc_wake(0)
        return
    rtc_now = _read(RTC / "since_epoch")
    if not rtc_now.isdigit() or target - now < 60:
        return
    want = int(rtc_now) + (target - now)
    if not current.isdigit() or abs(int(current) - want) > 90:
        helper.rtc_wake(int(target - now))


# ---------------------------------------------------------------- Ablauf

def _log(cfg, action: str, detail: str = "") -> None:
    if events.enabled(cfg):
        events.log_system(cfg, action, detail)


def _pending_action() -> str | None:
    try:
        return json.loads(POWER_JSON.read_text()).get("action")
    except (OSError, ValueError):
        return None


def _note_boot(cfg, st: dict, now: float, boot: str) -> None:
    """Erster Lauf nach dem Abschalten: das Einschalten festhalten, auch für die Anzeige im Dashboard."""
    off = st.get("last_off")
    if not off or st.get("off_boot") == boot:
        return
    planned = st.get("planned_on")
    st["last_on"] = {"at": now, "off": off, "planned": planned,
                     "on_time": bool(planned) and abs(now - planned) < 300}
    st.pop("last_off", None)
    st.pop("planned_on", None)
    st.pop("off_boot", None)
    _log(cfg, "timer_on", f"war aus seit {_fmt(off)}" + (f", geplant für {_fmt(planned)}" if planned else ""))


def shutdown(cfg, helper, wake_seconds: int, planned: float | None, force: bool = False, label: str = "") -> None:
    """Festplatte sauber abmelden, Weckruf setzen, herunterfahren. HelperError: es läuft noch etwas."""
    helper.timer_run(wake_seconds, force)
    now = time.time()
    with _lock:
        st = _load(_state_file(cfg), {})
        st.update(last_off=now, planned_on=planned, off_boot=_read(BOOT_ID))
        for key in ("due", "at", "first", "postponed"):
            st.pop(key, None)
        _save(_state_file(cfg), st)
    _log(cfg, "timer_off", label + (f"Einschalten geplant für {_fmt(planned)}" if planned else "Einschalten mit dem Knopf am Pi"))


def tick(app, now: float | None = None) -> None:
    cfg, helper = app.config, app.extensions["pinas_helper"]
    now = time.time() if now is None else now
    if not clock_ok(now):
        return
    c = load_config(cfg)
    boot = _read(BOOT_ID)
    with _lock:
        st = _load(_state_file(cfg), {})
    if st.get("boot") != boot:  # Neu gestartet: eine angekündigte Abschaltung ist hinfällig
        for key in ("due", "at", "first", "postponed"):
            st.pop(key, None)
        st["boot"] = boot
    _note_boot(cfg, st, now, boot)

    pending = _pending_action()
    if pending is None:
        _sync_alarm(c, helper, now)
    if pending and pending != ACTION:
        st.pop("due", None)  # Update, Neustart oder Herunterfahren von Hand haben Vorrang
        _finish(cfg, st)
        return

    due = st.get("due")
    if not c["enabled"]:
        if due:
            helper.power("cancel")
            st.pop("due", None)
        _finish(cfg, st)
        return

    if not due:
        n = next_off(c, now)
        if n and n - now <= WARN and n != st.get("skip") and pending is None:
            at = max(n, now + 60)
            helper.timer_notice(int(at - now))
            st.update(due=n, at=at, first=at, postponed=False)
    elif pending is None:
        # Der Hinweis ist weg: Jemand hat auf „Abbrechen“ gedrückt
        st.update(skip=due)
        st.pop("due", None)
        _log(cfg, "timer_cancel", f"Abschaltung von {_fmt(due)} abgebrochen, der Timer gilt ab dem nächsten Mal wieder")
    elif now >= st["at"] and _run_due(cfg, helper, c, st, now, due):
        return  # shutdown() hat den Zustand schon gespeichert
    _finish(cfg, st)


def _finish(cfg, st: dict) -> None:
    with _lock:
        _save(_state_file(cfg), st)


def _run_due(cfg, helper, c: dict, st: dict, now: float, due: float) -> bool:
    """Die Abschaltzeit ist da. True: PiNAS fährt herunter."""
    on = next_on(c, now)
    if on and on - now < SKIP_IF_ON_WITHIN:
        helper.power("cancel")
        st["skip"] = due
        st.pop("due", None)
        _log(cfg, "timer_cancel", "Abschaltung entfällt: Es lief so lange etwas, dass gleich wieder die Einschaltzeit ist")
        return False
    running = transfers.busy()
    reason = transfers.describe(running) if running else ""
    if not running:
        _finish(cfg, st)  # shutdown() schreibt den Zustand selbst weiter
        try:
            shutdown(cfg, helper, int(on - now) if on else 0, on, force=now - st["first"] >= SAMBA_WAIT)
            return True
        except HelperError as exc:
            reason = str(exc)
    # Noch etwas in Arbeit: später erneut versuchen, der Hinweis zeigt die neue Zeit
    helper.timer_notice(POSTPONE)
    st["at"] = now + POSTPONE
    if not st.get("postponed"):
        st["postponed"] = True
        _log(cfg, "timer_wait", f"Abschaltung verschoben: {reason}")
    return False


def start(app, interval: int = 20) -> None:
    def loop():
        while True:
            try:
                tick(app)
            except Exception:  # noqa: BLE001 – der Hintergrund-Thread darf nie abbrechen
                app.logger.exception("Abschalt-Timer: Fehler")
            time.sleep(interval)

    threading.Thread(target=loop, name="pinas-powertimer", daemon=True).start()


# ---------------------------------------------------------------- Anzeige

def view(cfg) -> dict:
    now = time.time()
    c = load_config(cfg)
    st = _load(_state_file(cfg), {})
    off = next_off(c, now)
    if off and off == st.get("skip"):
        off = next_off(c, off + 1)
    on_after = next_on(c, off) if off else None
    return {**c, "next_off": _fmt(st["at"]) if st.get("due") else _fmt(off), "next_on": _fmt(on_after),
            "wake_status": wake_status(), "clock_ok": clock_ok(now), "day_names": DAY_NAMES,
            "last_on": {**st["last_on"], "text": _fmt(st["last_on"]["at"])} if st.get("last_on") else None,
            "test_minutes": TEST_SECONDS // 60}
