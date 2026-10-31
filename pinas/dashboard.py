"""System-Dashboard: Temperatur, Last, RAM, Speicherplatz, Laufwerke, Dienste."""
import json
import os
import shutil
import subprocess
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template

from .auth import login_required

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


def uptime() -> str | None:
    raw = _read("/proc/uptime")
    if not raw:
        return None
    secs = int(float(raw.split()[0]))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    return f"{d} T {h} Std {rem // 60} Min" if d else f"{h} Std {rem // 60} Min"


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


def block_devices() -> list[dict]:
    out = _cmd("lsblk", "-J", "-b", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL,TRAN,LABEL")
    if not out:
        return []
    devices = []
    for disk in json.loads(out).get("blockdevices", []):
        if disk.get("type") != "disk" or disk["name"].startswith(("loop", "zram", "ram")):
            continue
        devices.append({
            "name": disk["name"],
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


def service_state(name: str) -> str:
    return _cmd("systemctl", "is-active", name) or "unbekannt"


def stats() -> dict:
    data_dir = current_app.config["DATA_DIR"]
    du = shutil.disk_usage(data_dir)
    load = os.getloadavg() if hasattr(os, "getloadavg") else None
    return {
        "temp": cpu_temp(),
        "load": [round(x, 2) for x in load] if load else None,
        "cpus": os.cpu_count(),
        "memory": memory(),
        "uptime": uptime(),
        "throttled": throttled(),
        "disk": {"total": du.total, "used": du.used, "free": du.free, "path": str(data_dir),
                 "is_mount": os.path.ismount(data_dir)},
    }


@bp.get("/dashboard")
@login_required
def index():
    services = {s: service_state(s) for s in ("smbd", "nmbd", "wsdd", "avahi-daemon")}
    return render_template("dashboard.html", s=stats(), devices=block_devices(), services=services)


@bp.get("/api/stats")
@login_required
def api_stats():
    return jsonify(stats())
