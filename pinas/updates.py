"""Update-Center: neue PiNAS-Versionen hochladen oder von GitHub holen und installieren.

Prüfen und Installieren übernimmt /usr/local/sbin/pinas-update als root (über pinas-helper);
diese Seite legt nur das Paket ab und zeigt den Fortschritt an."""
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for

from . import transfers, users
from .auth import admin_required, helper
from .db import get_db
from .helper import HelperError

bp = Blueprint("updates", __name__, url_prefix="/update")

_MAX_UPLOAD = 50 * 1024 * 1024
_TIMEOUT = 20


class _DownloadRefused(Exception):
    """Das heruntergeladene Paket wird aus Sicherheitsgründen nicht weiterverwendet."""


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
    release = _last_check()
    if release.get("version"):  # Die letzte Suche kann von vor dem Update stammen
        release["newer"] = _vt(release["version"]) > _vt(current_version())
    return render_template("update.html", version=current_version(), staged=staged, st=st, log=log,
                           has_previous=(current_app.config["UPDATE_DIR"] / "previous").is_dir(),
                           release=release, reboot_at=reboot_at)


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


def _github_problem(exc: Exception) -> str:
    """Verständliche Meldung, wenn die Anfrage an GitHub nicht geklappt hat."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (403, 429):
            return "GitHub erlaubt ohne Anmeldung nur 60 Anfragen pro Stunde. Bitte später noch einmal suchen."
        if exc.code == 404:
            return "Das PiNAS-Verzeichnis auf GitHub wurde nicht gefunden."
        return f"GitHub antwortet gerade nicht wie erwartet (Fehler {exc.code}). Bitte später noch einmal suchen."
    if isinstance(getattr(exc, "reason", exc), ssl.SSLCertVerificationError):
        return "Die sichere Verbindung zu GitHub ließ sich nicht prüfen. Stimmt die Uhrzeit des PiNAS?"
    if isinstance(exc, ValueError):
        return "GitHub hat eine unerwartete Antwort geschickt. Bitte später noch einmal suchen."
    return "GitHub ist nicht erreichbar. Hat PiNAS gerade Internet?"


def _newest_package(releases: list) -> tuple[dict, dict, str] | None:
    """Höchste Version mit Update-Paket. Andere Releases (Android-App, SD-Karten-Image) zählen nicht."""
    best = None
    for rel in releases:
        if rel.get("draft") or rel.get("prerelease"):
            continue
        for asset in rel.get("assets", []):
            m = re.fullmatch(r"pinas-update-(\d+\.\d+\.\d+)\.tar\.gz", asset.get("name", ""))
            if m and (best is None or _vt(m.group(1)) > _vt(best[2])):
                best = (rel, asset, m.group(1))
    return best


@bp.post("/suchen")
@admin_required
def check_online():
    """Fragt die GitHub-Releases ab (nur lesend, nichts wird installiert)."""
    repo = current_app.config["UPDATE_REPO"]
    result = {"checked": datetime.now().strftime("%d.%m.%Y, %H:%M Uhr")}
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases?per_page=30",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "PiNAS"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            found = _newest_package(json.load(resp))
        if not found:
            result["error"] = "Auf GitHub ist noch kein Update veröffentlicht."
        else:
            rel, asset, version = found
            notes = [line[2:].strip() for line in (rel.get("body") or "").splitlines() if line.startswith("- ")]
            result.update(version=version, url=asset["browser_download_url"], digest=asset.get("digest") or "",
                          notes=notes[:30])
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as exc:
        result["error"] = _github_problem(exc if isinstance(exc, OSError) else ValueError())
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
            # GitHub leitet zum eigentlichen Speicherort um – auch der muss verschlüsselt sein
            if not resp.geturl().startswith("https://"):
                raise _DownloadRefused("Das Paket wäre unverschlüsselt angekommen.")
            total, sha = 0, hashlib.sha256()
            while chunk := resp.read(1 << 16):
                total += len(chunk)
                if total > _MAX_UPLOAD:
                    raise _DownloadRefused("Das Paket ist zu groß für ein PiNAS-Update.")
                sha.update(chunk)
                out.write(chunk)
        digest = rel.get("digest", "")
        if digest.startswith("sha256:") and digest != f"sha256:{sha.hexdigest()}":
            raise _DownloadRefused("Die Prüfsumme stimmt nicht mit GitHub überein.")
    except _DownloadRefused as exc:
        dest.unlink(missing_ok=True)
        flash(f"Download abgelehnt: {exc}", "error")
        return redirect(url_for("updates.index"))
    except (OSError, ValueError) as exc:
        dest.unlink(missing_ok=True)
        flash(f"Download fehlgeschlagen: {_github_problem(exc)}", "error")
        return redirect(url_for("updates.index"))
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
