"""Festplatte im Browser einrichten (nur Admins)."""
import os

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from .auth import admin_required, helper
from .dashboard import _cmd, block_devices
from .helper import HelperError

bp = Blueprint("disk", __name__, url_prefix="/laufwerk")


def system_disk() -> str | None:
    source = _cmd("findmnt", "-no", "SOURCE", "/")
    return _cmd("lsblk", "-no", "PKNAME", source) if source else None


def data_source() -> str | None:
    data_dir = str(current_app.config["DATA_DIR"])
    return _cmd("findmnt", "-no", "SOURCE", data_dir) if os.path.ismount(data_dir) else None


def candidates() -> list[dict]:
    sysdisk = system_disk()
    return [d for d in block_devices() if d["name"] != sysdisk and not d["name"].startswith("mmcblk")]


@bp.get("/")
@admin_required
def index():
    return render_template("disk.html", disks=candidates(), source=data_source(),
                           data_dir=current_app.config["DATA_DIR"])


@bp.post("/verwenden")
@admin_required
def use():
    device, mode = request.form.get("device", ""), request.form.get("mode", "")
    disks = candidates()
    names = {d["name"] for d in disks} | {p["name"] for d in disks for p in d["parts"]}
    if device not in names or mode not in ("format", "keep"):
        flash("Ungültige Auswahl.", "error")
        return redirect(url_for("disk.index"))
    if mode == "format" and request.form.get("confirm", "").strip() != device:
        flash(f"Zum Formatieren bitte „{device}“ als Bestätigung eintippen.", "error")
        return redirect(url_for("disk.index"))
    try:
        helper().use_disk(f"/dev/{device}", mode)
        flash(f"/dev/{device} wird jetzt als NAS-Speicher verwendet.", "ok")
    except HelperError as exc:
        flash(f"Einrichtung fehlgeschlagen: {exc}", "error")
    return redirect(url_for("disk.index"))
