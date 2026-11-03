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

from . import events, private, quota, shares, transfers
from .auth import login_required
from .config import TRASH_NAME, disk_is_off, storage

bp = Blueprint("files", __name__)

ROOTS = {"privat": "Privat", "gemeinsam": "Gemeinsam"}
_FORBIDDEN_CHARS = set('/\\\0')
_DOWNLOAD_BLOCK = 256 * 1024
_SYNC_EVERY = 64 * 1024 * 1024
_SEARCH_MAX_HITS = 500
_SEARCH_MAX_SECONDS = 30
_SEARCH_MIN_CHARS = 2
_SIZE_WAIT = 2  # so lange wartet eine Anfrage auf das Ergebnis, danach antwortet sie mit dem Zwischenstand
_SIZE_WALKS = 2  # höchstens so viele Ordner gleichzeitig durchzählen, die Platte hat auch anderes zu tun
_SIZE_IDLE = 15  # niemand fragt mehr nach (Seite verlassen): aufhören
_SIZE_KEEP = 300  # Ergebnis merken, wenn das Zählen merklich gedauert hat
_SIZE_SLOW = 1
_SELECTION_KEEP = 3600
_SELECTION_MAX = 50

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
# Lassen sich im Browser ändern (fileops.py)
TEXT_EXTS = {ext for ext, kind in _INLINE_TYPES.items() if kind == _TEXT} | {"xml", "html", "htm", "tsv"}
TEXT_MAX = 2 * 1024 * 1024
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


def all_roots() -> dict:
    """Dazu die Ordner, die andere für diesen Benutzer freigegeben haben (Reiter im Bereich Dateien)."""
    return {**user_roots(), **shares.for_me(g.user["username"])}


def root_label(root: str) -> str:
    return ROOTS.get(root) or (shares.access(root) or {}).get("label", root)


def can_write(root: str) -> bool:
    if root in ROOTS:
        return True
    info = shares.access(root)
    return bool(info and info["can_write"])


def quota_owner(root: str) -> str | None:
    """Auf wessen Speicherlimit das hier zählt; „Gemeinsam“ hat keins."""
    if root == "privat":
        return g.user["username"]
    info = shares.access(root)
    return info["owner"] if info else None


def trash_place(root: str, base: Path) -> tuple[str, Path]:
    """Papierkorb für diesen Bereich. In einer Freigabe ist das der des Besitzers: Er kann alles zurückholen."""
    info = shares.access(root)
    if info:
        return "privat", (storage(current_app.config)["users"] / info["owner"]).resolve()
    return root, base


@bp.before_request
def _read_only_shares():
    # An einer Stelle für alle Änderungen: Wer eine Freigabe nur ansehen darf, kommt mit POST nicht weiter
    root = (request.view_args or {}).get("root")
    if (request.method == "POST" and request.endpoint not in ("files.selection", "files.single_files")
            and shares.is_share_root(root) and not can_write(root)):
        abort(make_response(jsonify(error="Dieser Ordner ist für dich nur zum Ansehen freigegeben."), 403))


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
    # Festplatte wird geprüft und ist dafür ausgehängt: ebenso nichts auf die SD-Karte darunter schreiben
    if Path("/run/pinas-disk-check").exists():
        abort(503, "Die Festplatte wird gerade geprüft. Danach sind Dateien und Netzlaufwerk von selbst wieder da. Den Stand siehst du als Administrator unter „Laufwerk“.")
    # Festplatte im Dashboard ausgeschaltet: nichts auf die SD-Karte darunter schreiben
    if disk_is_off(current_app.config):
        abort(503, "Die Festplatte ist ausgeschaltet. Im Dashboard oben rechts mit „Festplatte einschalten“ wieder aktivieren.")
    paths = storage(current_app.config)
    if root == "privat":
        if not private.enabled(g.user["username"]):
            abort(404, "Du hast noch kein privates Laufwerk. Unter „Konto“ kannst du es einrichten.")
        base = paths["users"] / g.user["username"]
    elif root == "gemeinsam":
        base = paths["shared"]
    else:
        info = shares.access(root)  # prüft auch, ob dieser Benutzer die Freigabe sehen darf
        if not info:
            abort(404)
        return info["folder"].resolve()
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
        "editable": not is_dir and st.st_size <= TEXT_MAX and Path(e.name).suffix[1:].lower() in TEXT_EXTS,
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


# Ordnergrößen: Die Dateiliste wartet nicht darauf, jeder Ordner zählt in einem eigenen Faden und der Browser fragt
# nach dem Stand. Das zählt, was auch ein ZIP dieses Ordners enthielte. Fertige Ergebnisse großer Ordner bleiben
# kurz gemerkt; was über das Netzlaufwerk dazukommt, sieht man bei ihnen also erst nach höchstens _SIZE_KEEP Sekunden.
_sizes: dict[str, dict] = {}
_sizes_lock = threading.Lock()


def _count_size(key: str, job: dict, folder: Path) -> None:
    started = time.monotonic()
    pending = [folder]
    try:
        while pending:
            if time.monotonic() - job["asked"] > _SIZE_IDLE:
                with _sizes_lock:
                    if _sizes.get(key) is job:
                        del _sizes[key]
                return
            try:
                with os.scandir(pending.pop()) as it:
                    for e in it:
                        try:
                            if e.name.startswith(".pinas-upload-") or e.is_symlink():
                                continue
                            if e.is_dir(follow_symlinks=False):
                                pending.append(Path(e.path))
                            elif e.is_file(follow_symlinks=False):
                                job["size"] += e.stat(follow_symlinks=False).st_size
                        except OSError:
                            job["partial"] = True
            except OSError:
                job["partial"] = True  # Ordner nicht lesbar: Die Größe ist dann nur eine Untergrenze
    finally:
        took = time.monotonic() - started
        job["until"] = time.monotonic() + (_SIZE_KEEP if took >= _SIZE_SLOW else 0)
        job["finished"].set()


def _folder_size(key: str, folder: Path) -> dict:
    now = time.monotonic()
    with _sizes_lock:
        for k, j in list(_sizes.items()):
            if j["finished"].is_set() and j["until"] <= now and k != key:
                del _sizes[k]
        job = _sizes.get(key)
        if job and job["finished"].is_set() and job["until"] <= now:
            job = None
        if not job:
            if sum(not j["finished"].is_set() for j in _sizes.values()) >= _SIZE_WALKS:
                return {"size": 0, "partial": False, "finished": threading.Event()}  # gleich noch einmal fragen
            job = {"size": 0, "partial": False, "asked": now, "until": 0, "finished": threading.Event()}
            _sizes[key] = job
            threading.Thread(target=_count_size, args=(key, job, folder), daemon=True).start()
        job["asked"] = now
    return job


def forget_sizes() -> None:
    """Nach einer Änderung: Gemerkte Größen stimmen vielleicht nicht mehr. Laufendes Zählen läuft zu Ende."""
    with _sizes_lock:
        for k, j in list(_sizes.items()):
            if j["finished"].is_set():
                del _sizes[k]


@bp.get("/api/ordnergroesse/<root>/<path:sub>")
@login_required
def folder_size(root, sub):
    _, target = resolve(root, sub)
    if not target.is_dir():
        abort(404)
    job = _folder_size(str(target), target)
    done = job["finished"].wait(_SIZE_WAIT)
    # Erst nach dem Warten lesen, dann gehört auch das Ende des Zählens noch dazu
    resp = jsonify(size=job["size"], text=current_app.jinja_env.filters["filesize"](job["size"]),
                   done=done, partial=job["partial"])
    resp.headers["Cache-Control"] = "no-store"
    return resp


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
                if not _in_batch(root, rel(base, target)):
                    events.log("download", f"{root_label(root)}/{rel(base, target)}", events.size_of(target))
            elif request.headers.get("Sec-Fetch-Dest", "document") == "document":
                events.log("open", f"{root_label(root)}/{rel(base, target)}", events.size_of(target))
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
    owner = quota_owner(root)
    space = quota.info(owner) if owner else None
    if space and space["limit"]:
        # Mit Speicherlimit zeigt „Privat“ den persönlichen Platz statt der ganzen Platte
        usage = SimpleNamespace(total=space["limit"], used=space["used"],
                                free=min(space["free"], usage.free))
    shared = {s["path"] for s in shares.mine(g.user["username"])} if root == "privat" else set()
    resp = make_response(render_template("files.html", root=root, roots=all_roots(), sub=rel(base, target),
                                         entries=entries, crumbs=crumbs, usage=usage, search=search,
                                         quota_free=usage.free if space and space["limit"] else None,
                                         writable=can_write(root), shared_paths=shared, text_max=TEXT_MAX))
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
    name = target.name if target != base else root_label(root)
    entry = transfers.begin("download", name, 0, tid=request.args.get("t", ""))
    try:
        hidden = hidden_names(root) if target == base else frozenset()
        items, skipped = _zip_contents(target, name, hidden, entry)
    except Exception:
        transfers.end(entry, remember=True)
        raise
    events.log("download", f"{root_label(root)}/{rel(base, target)} (Ordner als ZIP)".replace("/ (", " ("), entry["size"])
    return _zip_response(name, items, skipped, entry)


def _zip_response(name: str, items: list, skipped: list, entry: dict):
    resp = current_app.response_class(_zip_stream(items, skipped, entry), mimetype="application/zip")
    resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name + '.zip')}"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-PiNAS-Size"] = str(entry["size"])  # für den Fortschritt, die genaue ZIP-Größe gibt es noch nicht
    resp.call_on_close(lambda: transfers.end(entry, remember=True))
    return resp


# Mehrere Einträge als ein ZIP. Der Browser meldet die Auswahl zuerst an und holt das ZIP dann über eine normale
# Adresse: Die Auswahl kann für eine Adresse zu lang sein, und Browser und Android-App laden nur Adressen selbst herunter.
_selections: dict[str, dict] = {}
_selections_lock = threading.Lock()


def _arc_name(name: str, used: set) -> str:
    """Gleich heißende Einträge aus verschiedenen Ordnern (Suchtreffer) dürfen sich im ZIP nicht überschreiben.
    Windows unterscheidet nicht nach Groß/Klein."""
    stem, suffix = (Path(name).stem, Path(name).suffix)
    arc, i = name, 1
    while arc.casefold() in used:
        i += 1
        arc = f"{stem} ({i}){suffix}"
    used.add(arc.casefold())
    return arc


def _selection_contents(root: str, paths: list, entry: dict) -> tuple[list, list]:
    """Löst die Pfade neu auf: Die Prüfung gilt für jetzt, nicht für den Moment der Anmeldung. Liegt ein gewählter
    Eintrag in einem ebenfalls gewählten Ordner, ist er über den Ordner schon dabei."""
    base = root_dir(root)
    chosen = {resolve(root, p)[1] for p in paths} - {base}
    items, skipped, used = [], [], set()
    for path in sorted(chosen, key=lambda p: p.name.casefold()):
        if any(parent in chosen for parent in path.parents):
            continue
        arc = _arc_name(path.name, used)
        try:
            if path.is_dir():
                more, missed = _zip_contents(path, arc, frozenset(), entry)
                items += more
                skipped += missed
            elif path.is_file():
                entry["size"] += path.stat().st_size
                items.append((path, arc, False))
            else:
                skipped.append(arc)  # inzwischen weg
        except OSError:
            skipped.append(arc)
    return items, skipped


# Unter /api, nicht unter /dateien/<root>/: Dort wäre ein Ordner „auswahl“ nicht mehr zu öffnen
@bp.post("/api/auswahl/<root>")
@login_required
def selection(root):
    data = request.get_json(silent=True) or {}
    paths = data.get("paths")
    if not (isinstance(paths, list) and paths and all(isinstance(p, str) for p in paths)):
        return jsonify(error="Es ist nichts ausgewählt."), 400
    for p in paths:
        resolve(root, p)  # Gesperrtes gleich hier ablehnen
    try:
        name = clean_name(str(data.get("name", "")))
    except ValueError:
        name = "Auswahl"
    ticket = _remember_selection(root, paths, name)
    return jsonify(href=url_for("files.selection_download", root=root, ticket=ticket))


def _remember_selection(root: str, paths: list, name: str, files: set | None = None) -> str:
    ticket = uuid.uuid4().hex
    now = time.monotonic()
    with _selections_lock:
        for key, sel in list(_selections.items()):
            if now - sel["at"] > _SELECTION_KEEP:
                del _selections[key]
        while len(_selections) >= _SELECTION_MAX:
            del _selections[next(iter(_selections))]
        _selections[ticket] = {"root": root, "paths": paths, "name": name, "user": g.user["username"], "at": now, "files": files}
    return ticket


def _shown_names(paths: list) -> str:
    names = [p.rsplit("/", 1)[-1] for p in paths]
    return ", ".join(names[:3]) + (f" und {len(names) - 3} weitere" if len(names) > 3 else "")


def _in_batch(root: str, path: str) -> bool:
    """Gehört der Download zu einer angemeldeten Liste? Dann steht er schon zusammengefasst im Protokoll. Nur genau die
    Dateien der Liste: Andere Downloads lassen sich so nicht am Protokoll vorbeischleusen."""
    with _selections_lock:
        sel = _selections.get(request.args.get("sammel", ""))
        if not sel or not sel["files"] or sel["root"] != root or sel["user"] != g.user["username"] or path not in sel["files"]:
            return False
        sel["at"] = time.monotonic()  # eine lange Liste läuft nicht mittendrin ab
    return True


# Mehrere Einträge einzeln statt als ZIP: Der Server nennt nur, was dazugehört, und zwar nach denselben Regeln wie
# beim ZIP (Rechte, Freigaben, keine Symlinks). Jede Datei holt der Browser danach über ihre normale Adresse.
@bp.post("/api/einzeln/<root>")
@login_required
def single_files(root):
    paths = (request.get_json(silent=True) or {}).get("paths")
    if not (isinstance(paths, list) and paths and all(isinstance(p, str) for p in paths)):
        return jsonify(error="Es ist nichts ausgewählt."), 400
    entry = {"size": 0}
    items, skipped = _selection_contents(root, paths, entry)
    base = root_dir(root)
    files, dirs = [], []
    for path, arc, is_dir in items:
        try:
            arc.encode()  # kaputt kodierter Name: ließe sich nicht als Adresse schicken
            if is_dir:
                dirs.append(arc)
            else:
                files.append({"path": rel(base, path), "arc": arc, "size": path.stat().st_size})
        except (OSError, UnicodeEncodeError):
            skipped.append(arc)
    size = sum(f["size"] for f in files)
    ticket = _remember_selection(root, paths, "", {f["path"] for f in files})
    events.log("download", f"{root_label(root)}: {_shown_names(paths)} (einzeln, {len(files)} Dateien)", size)
    resp = jsonify(base=url_for("files.browse", root=root), ticket=ticket, files=files, dirs=dirs, size=size,
                   skipped=[s.encode("utf-8", "replace").decode() for s in skipped])
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.get("/api/auswahl/<root>/<ticket>")
@login_required
def selection_download(root, ticket):
    with _selections_lock:
        sel = _selections.get(ticket)
    if not sel or sel["root"] != root or sel["user"] != g.user["username"]:
        abort(404, "Diese Auswahl ist abgelaufen. Bitte noch einmal auswählen und herunterladen.")
    entry = transfers.begin("download", f"{sel['name']}.zip", 0, tid=request.args.get("t", ""))
    try:
        items, skipped = _selection_contents(root, sel["paths"], entry)
    except Exception:
        transfers.end(entry, remember=True)
        raise
    events.log("download", f"{root_label(root)}: {_shown_names(sel['paths'])} (Auswahl als ZIP)", entry["size"])
    return _zip_response(sel["name"], items, skipped, entry)


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
    # Dateinamen anderer Benutzer bleiben verborgen, ohne Text zeigt die Seite einen allgemeinen Hinweis
    shown = own or [e for e in running if g.user["is_admin"] or e["user"] == g.user["username"]]
    return jsonify(busy=bool(running), mine=bool(own), text=transfers.describe(shown))


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
    owner = quota_owner(root)
    if owner:
        space = quota.info(owner)
        if space["limit"] and quota.used(owner, fresh=True) + size > space["limit"]:
            who = "Dein Speicherplatz" if owner == g.user["username"] else f"Der Speicherplatz von {owner}"
            return jsonify(error=f"{who} reicht dafür nicht – frei sind noch {filesize(space['free'])}. "
                                 "Lösche Dateien (auch im Papierkorb) oder bitte den Administrator um mehr Platz."), 413
        quota.forget(owner)
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
    if quota_owner(root):
        quota.forget(quota_owner(root))
    forget_sizes()
    events.log("upload", f"{root_label(root)}/{rel(entry['base'], dest)}", events.size_of(dest))
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
        events.log("mkdir", f"{root_label(root)}/{rel(base, new_dir)}")
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
        forget_sizes()
        shares.moved(src, dest)
        events.log("rename", f"{root_label(root)}/{rel(base, src)} → {dest.name}", events.size_of(dest))
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
        move_to_trash(*trash_place(root, base), target)
        forget_sizes()
        shares.moved(target, None)
        events.log("delete", f"{root_label(root)}/{rel(base, target)}", size)
        flash(f"„{target.name}“ in den Papierkorb verschoben.", "ok")
    except OSError as exc:
        flash(f"Löschen fehlgeschlagen: {exc}", "error")
    return back_to(root, sub)
