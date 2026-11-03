"""Update-Center: neue PiNAS-Versionen von Hand hochladen und installieren. Der Pi lädt bewusst nichts selbst aus dem Internet.

Prüfen und Installieren übernimmt /usr/local/sbin/pinas-update als root (über pinas-helper);
diese Seite legt nur das Paket ab und zeigt den Fortschritt an."""
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from markupsafe import Markup, escape

from . import transfers, users
from .auth import admin_required, helper
from .db import get_db
from .helper import HelperError

bp = Blueprint("updates", __name__, url_prefix="/update")

_MAX_UPLOAD = 50 * 1024 * 1024


def current_version() -> str:
    try:
        return (Path(__file__).parent / "VERSION").read_text().strip()
    except OSError:
        return "0.0.0"


def parse_changelog(text: str) -> list[dict]:
    """Abschnitte aus CHANGELOG.md: „## x.y.z“, darunter wahlweise „### Neu“, „### Verbessert“, „### Behoben“,
    „### Sicherheit“ und Punkte mit „- “. Ältere Abschnitte ohne Gliederung ergeben eine Gruppe ohne Überschrift."""
    sections, section, group = [], None, None
    for line in text.splitlines():
        if m := re.match(r"^##\s+(\d+\.\d+\.\d+)", line):
            section, group = {"version": m.group(1), "groups": []}, None
            sections.append(section)
        elif section is None:
            continue
        elif m := re.match(r"^###\s+(.+)", line):
            group = {"title": m.group(1).strip(), "items": []}
            section["groups"].append(group)
        elif line.startswith("- "):
            if group is None:
                group = {"title": "", "items": []}
                section["groups"].append(group)
            group["items"].append(line[2:].strip())
    return sections


def _release_notes() -> tuple[dict | None, list[dict]]:
    """Neuigkeiten der installierten Version und alle älteren."""
    try:
        text = (Path(__file__).parent / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return None, []
    sections = parse_changelog(text)
    version = current_version()
    for i, section in enumerate(sections):
        if section["version"] == version:
            return section, sections[i + 1:]
    return None, sections


@bp.before_app_request
def announce_update():
    """Einmal pro Administrator: Nach einem Update auf der nächsten Seite auf die Neuigkeiten hinweisen."""
    user = g.get("user")
    if not user or not user["is_admin"] or request.method != "GET" or request.endpoint in (None, "static"):
        return
    # Nur richtige Seitenaufrufe, nicht die Abfragen im Hintergrund
    if request.headers.get("Sec-Fetch-Dest", "document") != "document" or request.path.startswith("/api/"):
        return
    conn = get_db()
    version = current_version()
    row = conn.execute("SELECT value FROM settings WHERE username = ? AND key = 'seen_version'", (user["username"],)).fetchone()
    if row and row["value"] == version:
        return
    conn.execute("INSERT OR REPLACE INTO settings (username, key, value) VALUES (?, 'seen_version', ?)",
                 (user["username"], version))
    conn.commit()
    st = status()
    if st.get("state") == "done" and st.get("version") == version and request.endpoint != "updates.index":
        link = url_for("updates.index") + "#neu"
        flash(Markup('PiNAS läuft jetzt mit Version {}. <a href="{}">Was ist neu?</a>').format(escape(version), link), "ok")


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


def start_reboot_watch(app) -> None:
    """Übergang: Ein Updater bis 1.2.2 startet nach dem Update noch nicht neu. Dann übernimmt das
    die frisch gestartete Weboberfläche – einmal pro Update (Merker in /var/lib/pinas)."""
    import threading
    import time

    def watch():
        deadline = time.time() + 600
        while time.time() < deadline:
            with app.app_context():
                st = _read_json("status.json")
                if st.get("state") == "running":
                    time.sleep(5)
                    continue
                if st.get("state") == "done" and "reboot" not in st and time.time() - st.get("time", 0) < 600:
                    marker = app.config["DB_PATH"].parent / f"update-reboot-{st.get('time')}"
                    if not marker.exists():
                        marker.write_text("")
                        try:
                            helper().power("update-reboot")
                        except HelperError:
                            pass
            return

    threading.Thread(target=watch, name="pinas-update-reboot", daemon=True).start()


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
    from .dashboard import power_state
    pending = power_state()["pending"]
    reboot_at = pending["at"] if pending and pending.get("action") == "update-reboot" else None
    notes, older_notes = _release_notes()
    return render_template("update.html", version=current_version(), staged=staged, st=st, log=log,
                           has_previous=(current_app.config["UPDATE_DIR"] / "previous").is_dir(),
                           reboot_at=reboot_at, notes=notes, older_notes=older_notes)


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


@bp.post("/werkseinstellungen")
@admin_required
def factory_reset():
    """Alle Benutzer und Einstellungen löschen; auf Wunsch auch die Festplatte leeren."""
    f = request.form
    wipe = f.get("wipe") == "1"
    word = "alles löschen" if wipe else "zurücksetzen"
    running = transfers.busy()
    if running:
        flash(transfers.describe(running), "error")
    elif not users.verify(get_db(), g.user["username"], f.get("password", "")):
        flash("Das Passwort ist falsch – PiNAS wurde nicht zurückgesetzt.", "error")
    elif f.get("confirm", "").strip().lower() != word:
        flash(f"Zum Bestätigen bitte genau „{word}“ eintippen.", "error")
    else:
        try:
            helper().factory_reset(wipe)
            g.user = None  # Warteseite ohne Menü – die Anmeldung gilt danach ohnehin nicht mehr
            return render_template("reset.html", wipe=wipe)
        except HelperError as exc:
            flash(str(exc), "error")
    return redirect(url_for("updates.index") + "#werkseinstellungen")


@bp.post("/<action>")
@admin_required
def action(action):
    names = {"installieren": "apply", "zurueck": "rollback", "verwerfen": "discard"}
    if action not in names:
        return redirect(url_for("updates.index"))
    # Installieren und Zurückgehen starten PiNAS neu – das würde laufende Übertragungen abbrechen
    running = transfers.busy() if action != "verwerfen" else []
    if running:
        flash(transfers.describe(running), "error")
        return redirect(url_for("updates.index"))
    try:
        helper().update(names[action])
        if action == "verwerfen":
            flash("Bereitgelegtes Update verworfen.", "ok")
    except HelperError as exc:
        flash(str(exc), "error")
    return redirect(url_for("updates.index"))
