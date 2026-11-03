"""Dateibrowser: Auflisten, Hoch-/Herunterladen, Ordner anlegen, Umbenennen, Löschen."""
import os
import shutil
import threading
import time
import unicodedata
import uuid
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from flask import (Blueprint, abort, current_app, flash, g, jsonify, make_response, redirect,
                   render_template, request, send_file, url_for)

from . import events, private, quota, transfers
from .auth import login_required
from .config import TRASH_NAME, storage

bp = Blueprint("files", __name__)

ROOTS = {"privat": "Privat", "gemeinsam": "Gemeinsam"}
_FORBIDDEN_CHARS = set('/\\\0')
_DOWNLOAD_BLOCK = 256 * 1024
_SYNC_EVERY = 64 * 1024 * 1024
_SEARCH_MAX_HITS = 500
_SEARCH_MAX_SECONDS = 30
_SEARCH_MIN_CHARS = 2

# Dateiart für Symbolfarbe, Vorschaubild und die Spalte „Typ“ (wie im Windows-Explorer)
_KINDS = {
    "image": ("Bild", {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "avif", "heic", "heif", "tif", "tiff", "raw", "dng"}),
    "video": ("Video", {"mp4", "mkv", "mov", "avi", "wmv", "webm", "m4v", "mpg", "mpeg", "ts"}),
    "audio": ("Audio", {"mp3", "wav", "flac", "aac", "m4a", "ogg", "wma", "opus"}),
    "pdf": ("PDF-Dokument", {"pdf"}),
    "doc": ("Dokument", {"doc", "docx", "odt", "rtf", "txt", "md", "pages"}),
    "sheet": ("Tabelle", {"xls", "xlsx", "ods", "csv", "numbers"}),
    "slides": ("Präsentation", {"ppt", "pptx", "odp", "key"}),
    "archive": ("Archiv", {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "img"}),
    "code": ("Code", {"py", "js", "html", "css", "json", "xml", "sh", "bat", "ps1", "c", "cpp", "java", "yml", "yaml", "ini", "conf"}),
}
_EXT_KIND = {ext: kind for kind, (_, exts) in _KINDS.items() for ext in exts}
# Formate, die der Browser selbst als Vorschaubild anzeigen kann
_THUMB_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "avif"}
# Nur diese Dateien zeigt der Browser direkt an, mit festem Typ. Alles andere wird heruntergeladen: Eine
# hochgeladene Webseite oder SVG-Datei könnte sonst Skripte mit der Anmeldung dessen ausführen, der sie öffnet.
_TEXT = "text/plain"
_INLINE_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp",
    "bmp": "image/bmp", "avif": "image/avif",
    "mp4": "video/mp4", "m4v": "video/mp4", "webm": "video/webm", "mov": "video/quicktime", "mkv": "video/x-matroska",
    "mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac", "ogg": "audio/ogg", "opus": "audio/ogg",
    "aac": "audio/aac", "m4a": "audio/mp4",
    "pdf": "application/pdf",
    # Quelltexte nur als reiner Text, ausgeführt wird davon nichts
    "txt": _TEXT, "log": _TEXT, "md": _TEXT, "csv": _TEXT, "json": _TEXT, "yml": _TEXT, "yaml": _TEXT, "ini": _TEXT,
    "conf": _TEXT, "py": _TEXT, "js": _TEXT, "css": _TEXT, "sh": _TEXT, "bat": _TEXT, "ps1": _TEXT, "c": _TEXT,
    "cpp": _TEXT, "java": _TEXT,
}
# Für alles andere, falls ein Browser es trotzdem öffnet: keine Skripte, kein Zugriff auf PiNAS
_DOWNLOAD_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"


def file_kind(name: str, is_dir: bool) -> dict:
    if is_dir:
        return {"kind": "dir", "ext": "", "type": "Dateiordner", "base": name, "suffix": "", "thumb": False}
    stem, dot, ext = name.rpartition(".")
    if not (dot and stem) or len(ext) > 8:
        return {"kind": "file", "ext": "", "type": "Datei", "base": name, "suffix": "", "thumb": False}
    low = ext.lower()
    kind = _EXT_KIND.get(low, "file")
    label = f"{_KINDS[kind][0]} ({low.upper()})" if kind != "file" else f"{low.upper()}-Datei"
    return {"kind": kind, "ext": low, "type": label, "base": stem, "suffix": "." + ext, "thumb": low in _THUMB_EXTS}


def user_roots() -> dict:
    """Bereiche des angemeldeten Benutzers: „Gemeinsam“ immer, „Privat“ nur wenn eingerichtet."""
    if private.enabled(g.user["username"]):
        return ROOTS
    return {k: v for k, v in ROOTS.items() if k != "privat"}


@bp.app_context_processor
def inject_home():
    # Ziel der Menüpunkte „Dateien“ und „Papierkorb“
    user = g.get("user")
    return {"home_root": "privat" if user and private.enabled(user["username"]) else "gemeinsam"}


def hidden_names(root: str) -> frozenset:
    """Ordner, die in der obersten Ebene verborgen sind: der Papierkorb und im Modus "root"
    zusätzlich PiNAS- und Systemordner in „Gemeinsam“."""
    extra = storage(current_app.config)["hidden"] if root == "gemeinsam" else frozenset()
    return extra | {TRASH_NAME}


def is_hidden(name: str, hidden: frozenset) -> bool:
    """exFAT und Co. unterscheiden nicht nach Groß/Klein und ignorieren Punkte am Ende:
    „.PINAS“ und „.pinas.“ sind dort derselbe Ordner wie „.pinas“."""
    wanted = name.rstrip(". ").casefold()
    return any(wanted == h.rstrip(". ").casefold() for h in hidden)


def root_dir(root: str) -> Path:
    # Festplatte im Dashboard ausgeschaltet: nichts auf die SD-Karte darunter schreiben
    if Path("/run/pinas/disk-off").exists():
        abort(503, "Die Festplatte ist ausgeschaltet. Im Dashboard oben rechts mit „Festplatte einschalten“ wieder aktivieren.")
    paths = storage(current_app.config)
    if root == "privat":
        if not private.enabled(g.user["username"]):
            abort(404, "Du hast noch kein privates Laufwerk. Unter „Konto“ kannst du es einrichten.")
        base = paths["users"] / g.user["username"]
    elif root == "gemeinsam":
        base = paths["shared"]
    else:
        abort(404)
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # z. B. Platte nur lesend eingebunden
    return base.resolve()


def resolve(root: str, sub: str) -> tuple[Path, Path]:
    """Löst einen Pfad relativ zum Wurzelordner auf und verhindert Ausbrüche (../, Symlinks)."""
    base = root_dir(root)
    target = (base / (sub or "").strip("/")).resolve()
    if target != base and base not in target.parents:
        abort(403)
    if target != base and is_hidden(target.relative_to(base).parts[0], hidden_names(root)):
        abort(403)  # PiNAS- und Systemordner sind über „Gemeinsam“ nicht erreichbar
    return base, target


def clean_name(name: str) -> str:
    name = (name or "").strip()
    if not name or name in (".", "..") or any(c in _FORBIDDEN_CHARS for c in name) or len(name) > 255:
        raise ValueError(f"Ungültiger Name: „{name}“")
    return name


def new_name(root: str, base: Path, parent: Path, name: str) -> str:
    """Prüft einen neuen Namen; reservierte Ordnernamen sind ganz oben in „Gemeinsam“ tabu."""
    name = clean_name(name)
    if parent == base and is_hidden(name, hidden_names(root)):
        raise ValueError(f"Der Name „{name}“ ist hier reserviert.")
    return name


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 10_000):
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("Kein freier Dateiname gefunden.")


def rel(base: Path, path: Path) -> str:
    return path.relative_to(base).as_posix() if path != base else ""


def back_to(root: str, sub: str):
    # Umbenannt oder gelöscht aus den Suchtreffern: q und wo führen zurück zur Trefferliste
    return redirect(url_for("files.browse", root=root, sub=sub,
                            q=request.form.get("q") or None, wo=request.form.get("wo") or None))


def _entry(base: Path, e: os.DirEntry) -> dict:
    st = e.stat()
    is_dir = e.is_dir()
    return {
        "name": e.name,
        "path": rel(base, Path(e.path)),
        "is_dir": is_dir,
        "size": None if is_dir else st.st_size,
        "ts": int(st.st_mtime),
        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y %H:%M"),
        **file_kind(e.name, is_dir),
    }


def _fold(text: str) -> str:
    # Ohne Groß/Klein, und „ü“ findet auch die zerlegte Schreibweise, die manche Geräte (z. B. Macs) speichern
    return unicodedata.normalize("NFC", text).casefold()


# Pro Browser läuft nur eine Suche: Eine neue oder „Abbrechen“ beendet die alte, damit sich auf der Platte nichts staut
_searches: dict[str, threading.Event] = {}
_searches_lock = threading.Lock()


def _search(root: str, base: Path, start: Path, query: str) -> dict:
    """Sucht im Namen, alle Wörter müssen vorkommen. Liest nur die Ordnerlisten, Dateien selbst bleiben zu;
    Symlinks werden nicht verfolgt. Oben Liegendes kommt zuerst dran, falls die Suche an eine Grenze stößt."""
    words = _fold(query.replace("*", " ")).split()  # „*.jpg“ findet so, was man erwartet
    result = {"q": query, "scope": "ordner" if start != base else "", "entries": [], "stopped": "",
              "too_short": len("".join(words)) < _SEARCH_MIN_CHARS, "max_hits": _SEARCH_MAX_HITS,
              "min_chars": _SEARCH_MIN_CHARS, "max_seconds": _SEARCH_MAX_SECONDS}
    if result["too_short"]:
        return result
    key, stop = transfers.browser(), threading.Event()
    with _searches_lock:
        if key in _searches:
            _searches[key].set()
        _searches[key] = stop
    deadline = time.monotonic() + _SEARCH_MAX_SECONDS
    # Der Papierkorb bleibt in jeder Tiefe draußen, auch ein tiefer liegender aus einer früheren Ordner-Aufteilung
    below = frozenset({TRASH_NAME})
    pending = deque([(start, hidden_names(root) if start == base else below)])
    entries = result["entries"]
    try:
        while pending and not result["stopped"]:
            if stop.is_set():
                result["stopped"] = "cancel"
            elif time.monotonic() > deadline:
                result["stopped"] = "time"
            if result["stopped"]:
                break
            current, hide = pending.popleft()
            try:
                with os.scandir(current) as it:
                    for e in it:
                        if is_hidden(e.name, hide) or e.name.startswith(".pinas-upload-") or e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            pending.append((Path(e.path), below))
                        folded = _fold(e.name)
                        if all(w in folded for w in words):
                            try:
                                e.path.encode()  # kaputt kodierter Name: ließe sich nicht als Seite ausgeben
                                entries.append({**_entry(base, e), "where": rel(base, Path(e.path).parent)})
                            except (OSError, UnicodeEncodeError):
                                continue
                            if len(entries) >= _SEARCH_MAX_HITS:
                                result["stopped"] = "limit"
                                break
            except OSError:
                continue  # Ordner nicht lesbar: die übrigen trotzdem durchsuchen
    finally:
        with _searches_lock:
            if _searches.get(key) is stop:
                del _searches[key]
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].casefold()))
    return result


@bp.get("/dateien/<root>/")
@bp.get("/dateien/<root>/<path:sub>")
@login_required
def browse(root, sub=""):
    if root == "privat" and not private.enabled(g.user["username"]):
        return redirect(url_for("files.browse", root="gemeinsam"))
    base, target = resolve(root, sub)
    if target.is_file():
        # Vorschaubilder und weitere Teilstücke eines Videos nicht als eigenen Zugriff zählen
        rng = request.headers.get("Range", "")
        if not rng or rng.startswith("bytes=0-"):
            if request.args.get("dl") == "1":
                events.log("download", f"{ROOTS[root]}/{rel(base, target)}", events.size_of(target))
            elif request.headers.get("Sec-Fetch-Dest", "document") == "document":
                events.log("open", f"{ROOTS[root]}/{rel(base, target)}", events.size_of(target))
        want_download = request.args.get("dl") == "1"
        ext = target.suffix[1:].lower()
        inline_type = _INLINE_TYPES.get(ext)
        # SVG behält seinen Typ, sonst gäbe es kein Vorschaubild; als <img> laufen darin keine Skripte
        resp = send_file(target, as_attachment=want_download or not inline_type, conditional=True,
                         mimetype=inline_type or ("image/svg+xml" if ext == "svg" else "application/octet-stream"))
        if not inline_type:
            resp.headers["Content-Security-Policy"] = _DOWNLOAD_CSP
        if want_download and request.method == "GET" and resp.status_code == 200:
            return _tracked_download(resp, target)
        return resp
    if not target.is_dir():
        abort(404)
    if request.args.get("dl") == "1" and request.method == "GET":
        return _zip_download(root, base, target)

    query = request.args.get("q", "").strip()[:100]
    search = None
    if query:
        search = _search(root, base, target if request.args.get("wo") == "ordner" else base, query)
        entries = search["entries"]
    else:
        entries = []
        hidden = hidden_names(root) if target == base else frozenset()
        with os.scandir(target) as it:
            for e in it:
                if is_hidden(e.name, hidden) or e.name.startswith(".pinas-upload-"):
                    continue
                try:
                    entries.append(_entry(base, e))
                except OSError:
                    continue
        entries.sort(key=lambda x: (not x["is_dir"], x["name"].casefold()))

    crumbs, acc = [], []
    for part in Path(rel(base, target)).parts:
        acc.append(part)
        crumbs.append((part, "/".join(acc)))

    usage = shutil.disk_usage(current_app.config["DATA_DIR"])
    space = quota.info(g.user["username"]) if root == "privat" else None
    if space and space["limit"]:
        # Mit Speicherlimit zeigt „Privat“ den persönlichen Platz statt der ganzen Platte
        usage = SimpleNamespace(total=space["limit"], used=space["used"],
                                free=min(space["free"], usage.free))
    resp = make_response(render_template("files.html", root=root, roots=user_roots(), sub=rel(base, target),
                                         entries=entries, crumbs=crumbs, usage=usage, search=search,
                                         quota_free=usage.free if space and space["limit"] else None))
    # Ordnerinhalt nie aus dem Browser-Cache anzeigen (sonst fehlen frisch hochgeladene Dateien)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _tracked_download(resp, path: Path):
    """Liefert die Datei blockweise und zählt mit, wie weit sie ist (für das Übertragungsfenster)."""
    entry = transfers.begin("download", path.name, path.stat().st_size, tid=request.args.get("t", ""))

    def blocks():
        with open(path, "rb") as f:
            while not entry["cancel"] and (block := f.read(_DOWNLOAD_BLOCK)):
                entry["done"] += len(block)
                yield block

    if hasattr(resp.response, "close"):
        resp.response.close()  # die von send_file geöffnete Datei
    resp.direct_passthrough = False
    resp.response = blocks()
    resp.call_on_close(lambda: transfers.end(entry, remember=True))
    return resp


class _ZipOut:
    """Sammelt, was zipfile schreibt, bis der Generator es weiterreicht (nur ein paar hundert KB)."""

    def __init__(self):
        self.data = bytearray()

    def write(self, chunk) -> int:
        self.data += chunk
        return len(chunk)

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        chunk = bytes(self.data)
        self.data.clear()
        return chunk


class _ZipCancelled(Exception):
    """Bricht die Antwort hart ab. Würde sie normal enden, hielte der Browser das halbe ZIP für fertig."""


def _zip_contents(folder: Path, arc_root: str, hidden: frozenset, entry: dict) -> tuple[list, list]:
    """Sucht alles für das ZIP zusammen und zählt die Größe mit. Symlinks bleiben draußen,
    damit nichts von außerhalb des eigenen Bereichs ins ZIP gelangt."""
    items, skipped = [(folder, arc_root, True)], []
    pending = [(folder, arc_root, hidden)]
    while pending:
        current, arc, hide = pending.pop()
        try:
            with os.scandir(current) as it:
                found = sorted(it, key=lambda e: e.name)
        except OSError:
            skipped.append(arc)
            continue
        for e in found:
            if is_hidden(e.name, hide) or e.name.startswith(".pinas-upload-") or e.is_symlink():
                continue
            name = f"{arc}/{e.name}"
            try:
                if e.is_dir(follow_symlinks=False):
                    items.append((Path(e.path), name, True))
                    pending.append((Path(e.path), name, frozenset()))
                elif e.is_file(follow_symlinks=False):
                    entry["size"] += e.stat(follow_symlinks=False).st_size
                    items.append((Path(e.path), name, False))
            except OSError:
                skipped.append(name)
    return items, skipped


def _zip_stream(items: list, skipped: list, entry: dict):
    """Erzeugt das ZIP beim Senden. Ohne Komprimieren: Fotos und Filme werden davon kaum kleiner,
    und der Pi schafft so die volle Geschwindigkeit. entry zählt die Dateiinhalte."""
    out = _ZipOut()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path, arc, is_dir in items:
            try:
                info = zipfile.ZipInfo.from_file(path, arc, strict_timestamps=False)
                if is_dir:
                    archive.writestr(info, b"")
                    yield out.take()
                    continue
                with open(path, "rb") as source, archive.open(info, "w") as dest:
                    while block := source.read(_DOWNLOAD_BLOCK):
                        if entry["cancel"]:
                            raise _ZipCancelled
                        dest.write(block)
                        entry["done"] += len(block)
                        yield out.take()
            except OSError:
                skipped.append(arc)
        if skipped:
            archive.writestr("Nicht enthalten.txt", "Diese Einträge konnten nicht gelesen werden:\n" + "\n".join(skipped) + "\n")
    # Fertig. Die Größe war nur die Summe beim Zusammensuchen, Dateien können sich seither geändert haben.
    entry["size"] = entry["done"]
    yield out.take()


def _zip_download(root: str, base: Path, target: Path):
    name = target.name if target != base else ROOTS[root]
    entry = transfers.begin("download", name, 0, tid=request.args.get("t", ""))
    try:
        hidden = hidden_names(root) if target == base else frozenset()
        items, skipped = _zip_contents(target, name, hidden, entry)
    except Exception:
        transfers.end(entry, remember=True)
        raise
    events.log("download", f"{ROOTS[root]}/{rel(base, target)} (Ordner als ZIP)".replace("/ (", " ("), entry["size"])
    resp = current_app.response_class(_zip_stream(items, skipped, entry), mimetype="application/zip")
    resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name + '.zip')}"
    resp.headers["Cache-Control"] = "no-store"
    resp.call_on_close(lambda: transfers.end(entry, remember=True))
    return resp


@bp.post("/api/suche/abbrechen")
@login_required
def search_cancel():
    with _searches_lock:
        stop = _searches.get(transfers.browser())
    if stop:
        stop.set()
    return jsonify(ok=bool(stop))


@bp.get("/api/uebertragung")
@login_required
def transfer_busy():
    # Sperrt in der Kopfzeile Abmelden und Herunterfahren, solange etwas übertragen wird
    running, own = transfers.busy(), transfers.mine()
    return jsonify(busy=bool(running), mine=bool(own), text=transfers.describe(own or running))


@bp.get("/api/uebertragung/<tid>")
@login_required
def transfer_status(tid):
    return jsonify(transfers.status(tid))


@bp.post("/api/uebertragung/<tid>/abbrechen")
@login_required
def transfer_cancel(tid):
    entry = transfers.get(tid)
    if entry:
        entry["cancel"] = True
    return jsonify(ok=bool(entry))


def _remove_old_uploads(folder: Path) -> None:
    """Reste abgebrochener Uploads (z. B. nach einem Stromausfall) aufräumen."""
    limit = time.time() - 3600
    for leftover in folder.glob(".pinas-upload-*"):
        try:
            if leftover.stat().st_mtime < limit:
                leftover.unlink()
        except OSError:
            pass


# Hochladen in Teilstücken: Jedes Stück landet sofort in der Zieldatei, statt dass der Server die fertig
# empfangene Datei am Ende noch einmal auspacken und umkopieren muss. So ist das Fenster bei 100 %
# wirklich fertig. Ablauf: upload (anmelden) → upload_part (je Stück) → upload_done bzw. upload_cancel.
@bp.post("/dateien/<root>/upload")
@login_required
def upload(root):
    base, target = resolve(root, request.form.get("dir", ""))
    if not target.is_dir():
        return jsonify(error="Zielordner existiert nicht."), 400
    try:
        size = int(request.form.get("size", ""))
        name = new_name(root, base, target, os.path.basename(request.form.get("name", "").replace("\\", "/")))
        if size < 0:
            raise ValueError("Ungültige Dateigröße.")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    filesize = current_app.jinja_env.filters["filesize"]
    if root == "privat":
        space = quota.info(g.user["username"])
        if space["limit"] and quota.used(g.user["username"], fresh=True) + size > space["limit"]:
            return jsonify(error=f"Dein Speicherplatz reicht dafür nicht – frei sind noch {filesize(space['free'])}. "
                                 "Lösche Dateien (auch im Papierkorb) oder bitte den Administrator um mehr Platz."), 413
        quota.forget(g.user["username"])
    free = shutil.disk_usage(target).free
    if size > free:
        return jsonify(error=f"Auf der Festplatte ist nicht genug Platz – frei sind noch {filesize(free)}."), 507
    _remove_old_uploads(target)
    tmp = target / f".pinas-upload-{uuid.uuid4().hex}"
    try:
        tmp.touch()
    except OSError as exc:
        return jsonify(error=str(exc)), 500
    entry = transfers.begin("upload", name, size, path=tmp, root=root, base=base, unsynced=0)
    return jsonify(id=entry["id"])


def _own_upload(root: str, tid: str) -> dict:
    root_dir(root)  # Festplatte ausgeschaltet: hier abbrechen
    entry = transfers.get(tid)
    if not entry or entry["kind"] != "upload" or entry["root"] != root:
        abort(make_response(jsonify(error="Das Hochladen ist abgelaufen – bitte erneut versuchen."), 404))
    return entry


@bp.post("/dateien/<root>/upload/<tid>")
@login_required
def upload_part(root, tid):
    entry = _own_upload(root, tid)
    offset = request.headers.get("X-Offset", type=int)
    with entry["lock"]:
        # Kleiner als bisher empfangen: Der Browser wiederholt ein Stück nach einem Verbindungsfehler
        if offset is None or not 0 <= offset <= entry["done"]:
            return jsonify(error="Teilstück passt nicht.", received=entry["done"]), 409
        # Nie mehr annehmen als angemeldet, sonst ließe sich die Platte (und das Speicherlimit) umgehen
        if request.content_length is None or offset + request.content_length > entry["size"]:
            return jsonify(error="Das Teilstück ist größer als die angemeldete Datei."), 413
        try:
            with open(entry["path"], "r+b") as f:
                f.seek(offset)
                f.truncate()
                written = 0
                while block := request.stream.read(1024 * 1024):
                    f.write(block)
                    written += len(block)
                    transfers.touch(entry)  # langsames Netz: Das Stück läuft noch, also nicht als abgebrochen werten
                entry["done"] = offset + written
                entry["unsynced"] += written
                # Regelmäßig wirklich auf die Platte schreiben, sonst staut es sich und dauert am Ende
                if entry["unsynced"] >= _SYNC_EVERY:
                    f.flush()
                    os.fsync(f.fileno())
                    entry["unsynced"] = 0
        except OSError as exc:
            return jsonify(error=str(exc)), 500
        transfers.touch(entry)
    return jsonify(received=entry["done"])


@bp.post("/dateien/<root>/upload/<tid>/fertig")
@login_required
def upload_done(root, tid):
    entry = _own_upload(root, tid)
    with entry["lock"]:
        if entry["done"] != entry["size"]:
            return jsonify(error="Die Datei ist nicht vollständig angekommen."), 409
        try:
            with open(entry["path"], "rb") as f:
                os.fsync(f.fileno())
            dest = unique_path(entry["path"].with_name(entry["name"]))
            os.replace(entry["path"], dest)
        except (ValueError, OSError) as exc:
            return jsonify(error=str(exc)), 500
        transfers.end(entry)
    if root == "privat":
        quota.forget(g.user["username"])
    events.log("upload", f"{ROOTS[root]}/{rel(entry['base'], dest)}", events.size_of(dest))
    return jsonify(saved=dest.name)


@bp.post("/dateien/<root>/upload/<tid>/abbrechen")
@login_required
def upload_cancel(root, tid):
    entry = _own_upload(root, tid)
    with entry["lock"]:
        transfers.end(entry)
        entry["path"].unlink(missing_ok=True)
    return jsonify(ok=True)


@bp.post("/dateien/<root>/ordner")
@login_required
def mkdir(root):
    sub = request.form.get("dir", "")
    base, target = resolve(root, sub)
    try:
        new_dir = target / new_name(root, base, target, request.form.get("name", ""))
        new_dir.mkdir()
        events.log("mkdir", f"{ROOTS[root]}/{rel(base, new_dir)}")
    except FileExistsError:
        flash("Ein Element mit diesem Namen existiert bereits.", "error")
    except (ValueError, OSError) as exc:
        flash(str(exc), "error")
    return back_to(root, sub)


@bp.post("/dateien/<root>/umbenennen")
@login_required
def rename(root):
    sub = request.form.get("dir", "")
    base, src = resolve(root, request.form.get("path", ""))
    if src == base or not src.exists():
        abort(400)
    try:
        dest = src.with_name(new_name(root, base, src.parent, request.form.get("name", "")))
        if dest.exists():
            raise ValueError("Ein Element mit diesem Namen existiert bereits.")
        src.rename(dest)
        events.log("rename", f"{ROOTS[root]}/{rel(base, src)} → {dest.name}", events.size_of(dest))
    except (ValueError, OSError) as exc:
        flash(str(exc), "error")
    return back_to(root, sub)


@bp.post("/dateien/<root>/loeschen")
@login_required
def delete(root):
    sub = request.form.get("dir", "")
    base, target = resolve(root, request.form.get("path", ""))
    if target == base or not (target.exists() or target.is_symlink()):
        abort(400)
    from .trash import move_to_trash  # hier importiert, sonst Kreis-Import (trash nutzt files)
    try:
        size = events.size_of(target)
        move_to_trash(root, base, target)
        events.log("delete", f"{ROOTS[root]}/{rel(base, target)}", size)
        flash(f"„{target.name}“ in den Papierkorb verschoben.", "ok")
    except OSError as exc:
        flash(f"Löschen fehlgeschlagen: {exc}", "error")
    return back_to(root, sub)
