"""Update-Center: neue PiNAS-Versionen hochladen oder von GitHub holen und installieren.

Prüfen und Installieren übernimmt /usr/local/sbin/pinas-update als root (über pinas-helper);
diese Seite legt nur das Paket ab und zeigt den Fortschritt an."""
import json
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

from .auth import admin_required, helper
from .helper import HelperError

bp = Blueprint("updates", __name__, url_prefix="/update")

_MAX_UPLOAD = 50 * 1024 * 1024
_TIMEOUT = 20


def current_version() -> str:
    try:
        return (Path(__file__).parent / "VERSION").read_text().strip()
    except OSError:
        return "0.0.0"


def _vt(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def _read_json(name: str) -> dict:
    try:
        return json.loads((current_app.config["UPDATE_DIR"] / name).read_text())
    except (OSError, ValueError):
        return {}


def status() -> dict:
    st = _read_json("status.json")
    if st.get("time"):
        st["time_text"] = datetime.fromtimestamp(st["time"]).strftime("%d.%m.%Y, %H:%M Uhr")
    return st


def _upload_dir() -> Path:
    d = current_app.config["DB_PATH"].parent / "updates"
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def _stage(path: Path) -> None:
    try:
        info = json.loads(helper().update("stage", str(path)) or "{}")
        if info.get("version"):
            flash(f"Paket geprüft: Version {info['version']} ist bereit.", "ok")
    except (HelperError, ValueError) as exc:
        flash(f"Update-Paket abgelehnt: {exc}", "error")
    finally:
        path.unlink(missing_ok=True)


@bp.get("/")
@admin_required
def index():
    st = status()
    staged = _read_json("staged.json") if st.get("state") in ("staged", "running") else {}
    log = ""
    if st.get("state") in ("failed", "done", "running"):
        try:
            log = (current_app.config["UPDATE_DIR"] / "update.log").read_text(errors="replace")[-6000:]
        except OSError:
            pass
    return render_template("update.html", version=current_version(), staged=staged, st=st, log=log,
                           has_previous=(current_app.config["UPDATE_DIR"] / "previous").is_dir(),
                           release=_last_check())


def _last_check() -> dict:
    """Ergebnis der letzten Online-Suche (wird nur für die Anzeige zwischengespeichert)."""
    try:
        return json.loads((_upload_dir() / "release.json").read_text())
    except (OSError, ValueError):
        return {}


@bp.get("/status")
@admin_required
def api_status():
    st = status()
    st["installed"] = current_version()
    return jsonify(st)


@bp.post("/hochladen")
@admin_required
def upload():
    f = request.files.get("package")
    if not f or not f.filename:
        flash("Bitte eine Update-Datei auswählen.", "error")
        return redirect(url_for("updates.index"))
    if not f.filename.lower().endswith((".zip", ".tar.gz", ".tgz")):
        flash("Das ist keine Update-Datei. Erwartet wird die ZIP von GitHub oder eine .tar.gz-Datei.", "error")
        return redirect(url_for("updates.index"))
    dest = _upload_dir() / f"upload-{uuid.uuid4().hex}.pkg"
    f.save(dest)
    if dest.stat().st_size > _MAX_UPLOAD:
        dest.unlink()
        flash("Die Datei ist zu groß für ein PiNAS-Update.", "error")
    else:
        _stage(dest)
    return redirect(url_for("updates.index"))


@bp.post("/suchen")
@admin_required
def check_online():
    """Fragt das neueste GitHub-Release ab (nur lesend, nichts wird installiert)."""
    repo = current_app.config["UPDATE_REPO"]
    result = {"checked": datetime.now().strftime("%d.%m.%Y, %H:%M Uhr")}
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "PiNAS"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            rel = json.load(resp)
        asset = next((a for a in rel.get("assets", [])
                      if re.fullmatch(r"pinas-update-\d+\.\d+\.\d+\.tar\.gz", a.get("name", ""))), None)
        if not asset:
            result["error"] = "Im neuesten Release ist kein Update-Paket enthalten."
        else:
            version = re.search(r"\d+\.\d+\.\d+", asset["name"]).group(0)
            result.update(version=version, url=asset["browser_download_url"], name=rel.get("name") or rel.get("tag_name"),
                          newer=_vt(version) > _vt(current_version()))
    except urllib.error.HTTPError as exc:
        result["error"] = ("Auf GitHub gibt es noch kein veröffentlichtes Release." if exc.code == 404
                           else f"GitHub antwortet nicht wie erwartet ({exc.code}).")
    except (OSError, ValueError) as exc:
        result["error"] = f"Keine Verbindung zu GitHub: {exc}"
    (_upload_dir() / "release.json").write_text(json.dumps(result, ensure_ascii=False))
    return redirect(url_for("updates.index"))


@bp.post("/herunterladen")
@admin_required
def download():
    rel = _last_check()
    url = rel.get("url", "")
    # Nur Download-Links von GitHub zum eigenen Repository akzeptieren
    if not url.startswith(f"https://github.com/{current_app.config['UPDATE_REPO']}/releases/download/"):
        flash("Bitte zuerst nach Updates suchen.", "error")
        return redirect(url_for("updates.index"))
    dest = _upload_dir() / f"download-{uuid.uuid4().hex}.pkg"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PiNAS"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp, open(dest, "wb") as out:
            total = 0
            while chunk := resp.read(1 << 16):
                total += len(chunk)
                if total > _MAX_UPLOAD:
                    raise ValueError("Download zu groß")
                out.write(chunk)
    except (OSError, ValueError) as exc:
        dest.unlink(missing_ok=True)
        flash(f"Download fehlgeschlagen: {exc}", "error")
        return redirect(url_for("updates.index"))
    _stage(dest)
    return redirect(url_for("updates.index"))


@bp.post("/<action>")
@admin_required
def action(action):
    names = {"installieren": "apply", "zurueck": "rollback", "verwerfen": "discard"}
    if action not in names:
        return redirect(url_for("updates.index"))
    try:
        helper().update(names[action])
        if action == "verwerfen":
            flash("Bereitgelegtes Update verworfen.", "ok")
    except HelperError as exc:
        flash(str(exc), "error")
    return redirect(url_for("updates.index"))
