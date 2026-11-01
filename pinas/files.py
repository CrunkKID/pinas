"""Dateibrowser: Auflisten, Hoch-/Herunterladen, Ordner anlegen, Umbenennen, Löschen."""
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from flask import (Blueprint, abort, current_app, flash, g, jsonify, make_response, redirect,
                   render_template, request, send_file, url_for)

from . import events, private, quota
from .auth import login_required
from .config import TRASH_NAME, storage

bp = Blueprint("files", __name__)

ROOTS = {"privat": "Privat", "gemeinsam": "Gemeinsam"}
_FORBIDDEN_CHARS = set('/\\\0')

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
    if target != base and target.relative_to(base).parts[0] in hidden_names(root):
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
    if parent == base and name in hidden_names(root):
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
    return redirect(url_for("files.browse", root=root, sub=sub))


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
        return send_file(target, as_attachment=request.args.get("dl") == "1", conditional=True)
    if not target.is_dir():
        abort(404)

    entries = []
    hidden = hidden_names(root) if target == base else frozenset()
    with os.scandir(target) as it:
        for e in it:
            if e.name in hidden or e.name.startswith(".pinas-upload-"):
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            is_dir = e.is_dir()
            entries.append({
                "name": e.name,
                "path": rel(base, Path(e.path)),
                "is_dir": is_dir,
                "size": None if is_dir else st.st_size,
                "ts": int(st.st_mtime),
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y %H:%M"),
                **file_kind(e.name, is_dir),
            })
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
                                         entries=entries, crumbs=crumbs, usage=usage,
                                         quota_free=usage.free if space and space["limit"] else None))
    # Ordnerinhalt nie aus dem Browser-Cache anzeigen (sonst fehlen frisch hochgeladene Dateien)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/dateien/<root>/upload")
@login_required
def upload(root):
    base, target = resolve(root, request.form.get("dir", ""))
    if not target.is_dir():
        return jsonify(error="Zielordner existiert nicht."), 400
    if root == "privat":
        space = quota.info(g.user["username"])
        if space["limit"] and quota.used(g.user["username"], fresh=True) + (request.content_length or 0) > space["limit"]:
            free = current_app.jinja_env.filters["filesize"](space["free"])
            return jsonify(errors=[f"Dein Speicherplatz reicht dafür nicht – frei sind noch {free}. "
                                   "Lösche Dateien (auch im Papierkorb) oder bitte den Administrator um mehr Platz."]), 413
        quota.forget(g.user["username"])
    saved, errors = [], []
    for f in request.files.getlist("files"):
        try:
            name = new_name(root, base, target, os.path.basename((f.filename or "").replace("\\", "/")))
            dest = unique_path(target / name)
            tmp = target / f".pinas-upload-{uuid.uuid4().hex}"
            try:
                f.save(tmp)
                os.replace(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
            saved.append(dest.name)
            events.log("upload", f"{ROOTS[root]}/{rel(base, dest)}", events.size_of(dest))
        except (ValueError, OSError) as exc:
            errors.append(f"{f.filename}: {exc}")
    return jsonify(saved=saved, errors=errors), (200 if not errors else 207)


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
