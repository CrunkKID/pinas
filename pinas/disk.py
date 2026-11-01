"""Festplatte im Browser einrichten und Energiesparen einstellen (nur Admins)."""
import json
import os
import re
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

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


@bp.get("/")
@admin_required
def index():
    disks = candidates()
    aliases = {d["name"]: d["alias"] for d in disks}
    return render_template("disk.html", disks=disks, source=data_source(), fstype=data_fstype(),
                           layout=shared_mode(), data_dir=current_app.config["DATA_DIR"],
                           power=_read_json("disk-power.json", POWER_DEFAULTS),
                           power_status=power_status(), idle_choices=IDLE_CHOICES, aliases=aliases)


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
