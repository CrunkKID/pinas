"""Festplatte im Browser einrichten und Energiesparen einstellen (nur Admins)."""
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from . import crashlog, events, transfers
from .auth import admin_required, helper
from .config import shared_mode
from .dashboard import _cmd, block_devices, disk_names, save_disk_names, system_disk
from .helper import HelperError

bp = Blueprint("disk", __name__, url_prefix="/laufwerk")


FS_NAMES = {"ext4": "ext4 (Linux)", "fuseblk": "NTFS (Windows)", "ntfs3": "NTFS (Windows)", "exfat": "exFAT"}


def data_source() -> str | None:
    data_dir = str(current_app.config["DATA_DIR"])
    return _cmd("findmnt", "-no", "SOURCE", data_dir) if os.path.ismount(data_dir) else None


def data_fstype() -> str | None:
    data_dir = str(current_app.config["DATA_DIR"])
    fs = _cmd("findmnt", "-no", "FSTYPE", data_dir) if os.path.ismount(data_dir) else None
    return FS_NAMES.get(fs, fs) if fs else None


def candidates() -> list[dict]:
    sysdisk = system_disk()
    return [d for d in block_devices() if d["name"] != sysdisk and not d["name"].startswith("mmcblk")]


IDLE_CHOICES = (0, 10, 20, 30, 60, 120)
POWER_DEFAULTS = {"idle_minutes": 0, "schedule_enabled": False, "start": "23:00", "end": "07:00"}
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _state_file(name: str):
    return current_app.config["DB_PATH"].parent / name


def _read_json(name: str, default: dict) -> dict:
    try:
        return {**default, **json.loads(_state_file(name).read_text())}
    except (OSError, ValueError):
        return dict(default)


def power_status() -> dict | None:
    """Vom Energiespar-Dienst (pinas-diskpower) jede Minute geschriebener Zustand."""
    st = _read_json("disk-power-status.json", {})
    if not st:
        return None
    since = st.get("asleep_since")
    st["since_text"] = datetime.fromtimestamp(since).strftime("%d.%m., %H:%M Uhr") if since else None
    return st


# ---------------------------------------------------------------- Festplatte prüfen (Dienst pinas-diskcheck)

CHECK_FLAG = Path("/run/pinas-disk-check")  # da = Prüfung läuft, verschwindet auch bei jedem Neustart
CHECK_REPAIRABLE = ("dirty", "errors")
CHECK_PHASES = {"starting": "Prüfung wird gestartet …", "stopping": "Netzlaufwerk wird gestoppt, Festplatte wird ausgehängt …",
                "checking": "Festplatte wird geprüft. Dabei wird nichts verändert.",
                "repairing": "Festplatte wird repariert. Bitte den Strom nicht trennen.",
                "remounting": "Festplatte wird wieder eingehängt, Netzlaufwerk wird gestartet …"}
# Ergebnis → (Art: ok/warn/bad, Überschrift, Erklärung)
CHECK_OUTCOMES = {
    "clean": ("ok", "Alles in Ordnung", "Die Prüfung hat keine Fehler gefunden."),
    "limited": ("ok", "Keine groben Fehler gefunden",
                "Für Windows-Platten (NTFS) gibt es unter Linux nur eine einfache Prüfung. Sie ersetzt kein chkdsk. "
                "Gründlich geht es nur an einem Windows-PC mit „chkdsk /f“."),
    "dirty": ("warn", "Keine Dateifehler, aber die Platte gilt noch als „nicht sauber getrennt“",
              "Die Markierung stammt meist von einem früheren Stromausfall oder Abziehen der Platte. Sie bleibt stehen, bis sie "
              "repariert wird, darum meldet das System die Warnung bei jedem Start erneut, auch wenn du sauber herunterfährst. "
              "„Reparieren“ prüft noch einmal und entfernt die Markierung."),
    "errors": ("bad", "Fehler gefunden", "Auf der Platte stimmt etwas nicht. Es wurde noch nichts verändert. Unten kannst du die Reparatur starten."),
    "repaired": ("ok", "Repariert", "Die Reparatur ist abgeschlossen, die Markierung „nicht sauber getrennt“ ist weg."),
    "repaired_limited": ("warn", "Einfache Reparatur abgeschlossen",
                         "Unter Linux lassen sich auf Windows-Platten (NTFS) nur grundlegende Fehler beheben. "
                         "Das ist keine vollständige Reparatur. Gründlich geht es nur an einem Windows-PC mit „chkdsk /f“."),
    "repair_incomplete": ("bad", "Reparatur nicht vollständig", "Das Prüfprogramm meldet Erfolg, aber die Markierung „nicht sauber getrennt“ ist noch da."),
    "errors_left": ("bad", "Es sind noch Fehler übrig", "Die Reparatur konnte nicht alles beheben. Bitte sichere deine wichtigen Daten auf eine andere Platte."),
    "failed": ("bad", "Prüfung fehlgeschlagen", "Die Prüfung konnte nicht zu Ende geführt werden."),
    "cancelled": ("warn", "Prüfung abgebrochen", "Die Prüfung wurde abgebrochen. Es wurde nichts verändert."),
    "interrupted": ("bad", "Prüfung wurde unterbrochen", "Der Pi wurde währenddessen neu gestartet oder hat den Strom verloren. Bitte noch einmal prüfen."),
}


def check_view(cfg) -> dict | None:
    """Stand der Prüfung für die Anzeige. None: noch nie geprüft."""
    try:
        st = json.loads((cfg["DB_PATH"].parent / "disk-check.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    running = CHECK_FLAG.exists()
    if running and st.get("state") != "running":
        try:  # Dienst startet gerade und hat noch nichts geschrieben
            req = json.loads(CHECK_FLAG.read_text())
        except (OSError, ValueError):
            req = {}
        st = {"state": "running", "mode": req.get("mode", "check"), "phase": "starting", "started": req.get("at")}
    elif not running and st.get("state") == "running":
        st = {**st, "state": "done", "outcome": "interrupted"}
    if not st:
        return None
    st["running"] = running
    if running:
        st["phase_text"] = CHECK_PHASES.get(st.get("phase"), CHECK_PHASES["starting"])
    else:
        st["kind"], st["title"], st["text"] = CHECK_OUTCOMES.get(st.get("outcome"), CHECK_OUTCOMES["failed"])
        st["can_repair"] = st.get("mode") == "check" and st.get("outcome") in CHECK_REPAIRABLE
    for key in ("started", "finished"):
        st[f"{key}_text"] = datetime.fromtimestamp(st[key]).strftime("%d.%m.%Y, %H:%M Uhr") if st.get(key) else ""
    if st.get("started") and st.get("finished"):
        st["minutes"] = max(1, round((st["finished"] - st["started"]) / 60))
    return st


def note_check_result(cfg) -> None:
    """Ein fertiges Ergebnis einmal ins Ereignis-Protokoll und, wenn es nicht glatt lief, ins Fehlerprotokoll schreiben."""
    st = check_view(cfg)
    if not st or st["running"]:
        return
    marker = cfg["DB_PATH"].parent / "disk-check.noted"
    stamp = str(st.get("started"))
    try:
        if marker.read_text() == stamp:
            return
    except OSError:
        pass
    marker.write_text(stamp)
    what = "Reparatur" if st.get("mode") == "repair" else "Prüfung"
    detail = f"{what}: {st['title']}" + (f" ({st.get('fstype')})" if st.get("fstype") else "")
    if st.get("mount_failed"):
        detail += ". Die Platte ließ sich danach nicht wieder einhängen"
    if events.enabled(cfg):
        events.log_system(cfg, {"ok": "disk_ok", "warn": "disk_warn", "bad": "disk_bad"}[st["kind"]], detail)
    if st["kind"] != "ok" or st.get("mount_failed") or st.get("warning_after") and st.get("mode") == "repair":
        # Ohne die Ausgabe des Prüfprogramms: Sie kann Dateinamen nennen
        facts = {k: st[k] for k in ("mode", "outcome", "fstype", "exit_code", "dirty_before", "dirty_after", "media_failure",
                                    "warning_after", "remounted", "stats", "message") if k in st}
        crashlog.record("disk", f"Festplatte: {detail}", json.dumps(facts, ensure_ascii=False),
                        level="error" if st["kind"] == "bad" or st.get("mount_failed") else "warn", key=f"diskcheck|{stamp}")


def start_check_watch(app, interval: int = 30) -> None:
    """Schreibt das Ergebnis auch dann auf, wenn gerade niemand die Seite offen hat."""
    def loop():
        while True:
            try:
                note_check_result(app.config)
            except Exception:  # noqa: BLE001 – der Hintergrund-Thread darf nie abbrechen
                app.logger.exception("Festplatten-Prüfung: Ergebnis konnte nicht notiert werden")
            time.sleep(interval)

    threading.Thread(target=loop, name="pinas-diskcheck", daemon=True).start()


def _start_check(action: str, message: str) -> None:
    running = transfers.busy()
    if running:
        flash(transfers.describe(running), "error")
        return
    try:
        helper().disk_check(action)
        events.log("setting", message)
        flash(f"{message}. Du kannst diese Seite schließen, das läuft im Hintergrund weiter.", "ok")
    except HelperError as exc:
        flash(str(exc), "error")


@bp.post("/pruefen")
@admin_required
def check():
    _start_check("check", "Festplatten-Prüfung gestartet")
    return redirect(url_for("disk.index") + "#pruefung")


@bp.post("/reparieren")
@admin_required
def repair():
    st = check_view(current_app.config)
    if not st or not st.get("can_repair"):
        flash("Bitte zuerst prüfen. Repariert wird nur, wenn die letzte Prüfung etwas gefunden hat.", "error")
    elif request.form.get("confirm", "").strip().lower() != "reparieren":
        flash("Zum Reparieren bitte das Wort „reparieren“ eintippen.", "error")
    else:
        _start_check("repair", "Festplatten-Reparatur gestartet")
    return redirect(url_for("disk.index") + "#pruefung")


@bp.post("/pruefung-abbrechen")
@admin_required
def check_cancel():
    try:
        helper().disk_check("cancel")
        flash("Die Prüfung wird abgebrochen.", "ok")
    except HelperError as exc:
        flash(str(exc), "error")
    return redirect(url_for("disk.index") + "#pruefung")


@bp.get("/pruefung-status")
@admin_required
def check_status():
    st = check_view(current_app.config) or {}
    return jsonify(running=st.get("running", False), phase=st.get("phase_text", ""))


@bp.get("/")
@admin_required
def index():
    disks = candidates()
    aliases = {d["name"]: d["alias"] for d in disks}
    data_dir = str(current_app.config["DATA_DIR"])
    return render_template("disk.html", disks=disks, source=data_source(), fstype=data_fstype(),
                           layout=shared_mode(), data_dir=current_app.config["DATA_DIR"],
                           power=_read_json("disk-power.json", POWER_DEFAULTS),
                           power_status=power_status(), idle_choices=IDLE_CHOICES, aliases=aliases,
                           check=check_view(current_app.config),
                           disk_in_fstab=bool(_cmd("findmnt", "--fstab", "-no", "SOURCE", data_dir)))


@bp.post("/aufteilung")
@admin_required
def layout():
    new = request.form.get("layout", "")
    if new not in ("folder", "root"):
        flash("Ungültige Auswahl.", "error")
    else:
        try:
            helper().set_layout(new)
            flash("Ordner-Aufteilung gespeichert.", "ok")
        except HelperError as exc:
            flash(f"Umstellen fehlgeschlagen: {exc}", "error")
    return redirect(url_for("disk.index") + "#aufteilung")


@bp.post("/name")
@admin_required
def rename():
    device = request.form.get("device", "")
    alias = " ".join(request.form.get("alias", "").split())  # Steuerzeichen/Mehrfach-Leerzeichen entfernen
    disk = next((d for d in candidates() if d["name"] == device), None)
    if disk is None:
        flash("Unbekannte Festplatte.", "error")
    elif len(alias) > 40:
        flash("Der Name darf höchstens 40 Zeichen lang sein.", "error")
    else:
        names = disk_names()
        if alias:
            names[disk["key"]] = alias
            flash(f"Festplatte heißt jetzt „{alias}“.", "ok")
        else:
            names.pop(disk["key"], None)
            flash("Name entfernt.", "ok")
        save_disk_names(names)
    return redirect(url_for("disk.index"))


@bp.post("/energie")
@admin_required
def power():
    f = request.form
    try:
        idle = int(f.get("idle_minutes", "0"))
    except ValueError:
        idle = -1
    start, end = f.get("start", ""), f.get("end", "")
    enabled = f.get("schedule_enabled") == "1"
    if idle not in IDLE_CHOICES or not (_TIME_RE.match(start) and _TIME_RE.match(end)):
        flash("Ungültige Einstellung.", "error")
    elif enabled and start == end:
        flash("Beginn und Ende des Zeitplans dürfen nicht gleich sein.", "error")
    else:
        path = _state_file("disk-power.json")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"idle_minutes": idle, "schedule_enabled": enabled, "start": start, "end": end}))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        flash("Energiespar-Einstellungen gespeichert. Sie gelten ab der nächsten Minute.", "ok")
    return redirect(url_for("disk.index") + "#energie")


@bp.post("/schlafen")
@admin_required
def sleep_now():
    try:
        helper().disk_sleep()
        flash("Die Festplatte wurde in den Ruhezustand versetzt. Sie wacht beim nächsten Zugriff auf.", "ok")
    except HelperError as exc:
        flash(f"Ruhezustand fehlgeschlagen: {exc}", "error")
    return redirect(url_for("disk.index") + "#energie")


@bp.post("/verwenden")
@admin_required
def use():
    device, mode = request.form.get("device", ""), request.form.get("mode", "")
    layout = request.form.get("layout", "folder")
    disks = candidates()
    names = {d["name"] for d in disks} | {p["name"] for d in disks for p in d["parts"]}
    if device not in names or mode not in ("format", "keep") or layout not in ("folder", "root"):
        flash("Ungültige Auswahl.", "error")
        return redirect(url_for("disk.index"))
    if mode == "format" and request.form.get("confirm", "").strip().lower() != "formatieren":
        flash("Zum Formatieren bitte das Wort „formatieren“ eintippen.", "error")
        return redirect(url_for("disk.index"))
    try:
        helper().use_disk(f"/dev/{device}", mode, layout)
        flash(f"/dev/{device} wird jetzt als NAS-Speicher verwendet.", "ok")
    except HelperError as exc:
        flash(f"Einrichtung fehlgeschlagen: {exc}", "error")
    return redirect(url_for("disk.index"))
