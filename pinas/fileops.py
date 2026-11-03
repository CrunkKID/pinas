"""Bearbeiten wie im Explorer: Kopieren und Verschieben (auch zwischen den Bereichen) und Textdateien ändern.

Kopieren läuft in einem eigenen Thread, damit der Server frei bleibt; der Browser fragt den Fortschritt
über /api/uebertragung/<id> ab und kann über dieselbe Stelle abbrechen (siehe transfers.py).
Die Rechte prüft allein der Server: Jeder Pfad geht durch files.resolve, Symlinks werden nie mitkopiert.
"""
import os
import shutil
import threading
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, jsonify, render_template, request

from . import crashlog, events, quota, shares, transfers
from .auth import login_required
from .config import TRASH_NAME
from .files import (TEXT_EXTS, TEXT_MAX, all_roots, can_write, forget_sizes, hidden_names, is_hidden, quota_owner, rel,
                    resolve, root_label, trash_place, unique_path)
from .trash import move_to_trash

bp = Blueprint("fileops", __name__)

_BLOCK = 1024 * 1024
_SYNC_EVERY = 64 * 1024 * 1024
_MAX_ITEMS = 2000
_CONFLICTS = {"keep", "replace", "skip"}
_reserve = threading.Lock()


class _Cancelled(Exception):
    pass


def err(text: str, status: int = 400):
    resp = jsonify(error=text)
    resp.status_code = status
    return resp


def _skip_name(name: str) -> bool:
    # Der Papierkorb bleibt in jeder Tiefe draußen, angefangene Uploads auch
    return name == TRASH_NAME or name.startswith(".pinas-upload-")


def _measure(sources: list[Path], entry: dict) -> None:
    """Größe zusammenzählen, ohne Symlinks zu verfolgen. Bricht auf Wunsch schon hier ab."""
    pending = list(sources)
    while pending:
        if entry["cancel"]:
            raise _Cancelled
        path = pending.pop()
        try:
            if path.is_symlink():
                continue
            if path.is_dir():
                with os.scandir(path) as it:
                    pending += [Path(e.path) for e in it if not _skip_name(e.name)]
            else:
                entry["size"] += path.stat().st_size
        except OSError:
            pass  # beim Kopieren wird es dann als nicht lesbar gemeldet


class _Job:
    def __init__(self, entry: dict, conflict: str, trash_root: str, trash_base: Path):
        self.entry, self.conflict = entry, conflict
        self.trash_root, self.trash_base = trash_root, trash_base
        self.copied = self.skipped = self.unsynced = 0
        self.failed: list[str] = []

    def check(self) -> None:
        transfers.touch(self.entry)
        if self.entry["cancel"]:
            raise _Cancelled

    def clear_way(self, dest: Path) -> bool:
        """Am Ziel liegt schon etwas: False = überspringen. Beim Ersetzen geht das Alte in den Papierkorb."""
        if not (dest.exists() or dest.is_symlink()):
            return True
        if self.conflict == "skip":
            return False
        move_to_trash(self.trash_root, self.trash_base, dest)
        return True

    def copy_file(self, src: Path, dest: Path) -> None:
        size = src.stat().st_size
        if not self.clear_way(dest):
            self.skipped += 1
            self.entry["done"] += size
            return
        tmp = dest.parent / f".pinas-upload-{uuid.uuid4().hex}"
        start = self.entry["done"]
        try:
            with open(src, "rb") as source, open(tmp, "wb") as target:
                while block := source.read(_BLOCK):
                    self.check()
                    target.write(block)
                    self.entry["done"] += len(block)
                    self.unsynced += len(block)
                    if self.unsynced >= _SYNC_EVERY:
                        target.flush()
                        os.fsync(target.fileno())
                        self.unsynced = 0
                target.flush()
                os.fsync(target.fileno())
            try:
                shutil.copystat(src, tmp)
            except OSError:
                pass  # NTFS/exFAT können nicht jede Angabe speichern
            os.replace(tmp, dest)
            self.copied += 1
        except BaseException:
            self.entry["done"] = start + size if not self.entry["cancel"] else self.entry["done"]
            tmp.unlink(missing_ok=True)
            raise

    def copy_tree(self, src: Path, dest: Path) -> None:
        self.check()
        if src.is_symlink():
            self.skipped += 1
            return
        try:
            if not src.is_dir():
                self.copy_file(src, dest)
                return
            if (dest.exists() or dest.is_symlink()) and (not dest.is_dir() or dest.is_symlink()):
                if not self.clear_way(dest):
                    self.skipped += 1
                    return
            dest.mkdir(exist_ok=True)
            with os.scandir(src) as it:
                children = sorted(e.name for e in it if not _skip_name(e.name))
        except OSError as exc:
            self.failed.append(f"{src.name}: {exc.strerror or exc}")
            return
        for name in children:
            self.copy_tree(src / name, dest / name)
        try:
            shutil.copystat(src, dest)
        except OSError:
            pass

    def move_tree(self, src: Path, dest: Path) -> None:
        """Innerhalb einer Platte nur ein Umbenennen. Bestehende Zielordner werden zusammengeführt."""
        self.check()
        try:
            size = events.size_of(src) or 0
            merge = dest.is_dir() and not dest.is_symlink() and src.is_dir() and not src.is_symlink()
            if merge:
                with os.scandir(src) as it:
                    children = sorted(e.name for e in it)
                for name in children:
                    if _skip_name(name):
                        continue
                    self.move_tree(src / name, dest / name)
                try:
                    src.rmdir()
                except OSError:
                    pass  # z. B. weil etwas übersprungen wurde: dann bleibt der Rest liegen
                return
            if not self.clear_way(dest):
                self.skipped += 1
                self.entry["done"] += size
                return
            try:
                os.rename(src, dest)
                self.entry["done"] += size
                self.copied += 1
            except OSError as exc:
                if exc.errno != 18:  # EXDEV: anderes Laufwerk, also richtig kopieren
                    raise
                self.copy_tree(src, dest)
                if not self.failed:
                    shutil.rmtree(src) if src.is_dir() else src.unlink()
        except OSError as exc:
            self.failed.append(f"{src.name}: {exc.strerror or exc}")


def _copy_name(dest_dir: Path, src: Path) -> Path:
    """Kopie im selben Ordner: „Bericht - Kopie.txt“ wie im Explorer."""
    if src.is_dir():
        return unique_path(dest_dir / f"{src.name} - Kopie")
    return unique_path(dest_dir / f"{src.stem} - Kopie{src.suffix}")


def _targets(action: str, sources: list[Path], dest_dir: Path, conflict: str):
    """(Quelle, Ziel) je Eintrag. Beim „Beide behalten“ bekommt das Ziel einen freien Namen."""
    pairs = []
    for src in sources:
        if src.parent == dest_dir and action == "move":
            continue  # liegt schon hier
        if src.parent == dest_dir:
            pairs.append((src, _copy_name(dest_dir, src)))
        else:
            dest = dest_dir / src.name
            pairs.append((src, unique_path(dest) if conflict == "keep" else dest))
    return pairs


def _parse() -> tuple:
    data = request.get_json(silent=True) or {}
    action, paths = data.get("action"), data.get("paths")
    src_root, dest_root = str(data.get("src_root", "")), str(data.get("dest_root", ""))
    if action not in ("copy", "move") or not isinstance(paths, list) or not 0 < len(paths) <= _MAX_ITEMS:
        abort(err("Ungültige Auswahl."))
    roots = all_roots()
    if src_root not in roots or dest_root not in roots:
        abort(404)
    if not can_write(dest_root):
        abort(err("In diesem Ordner darfst du nichts ändern, er ist für dich nur zum Ansehen freigegeben.", 403))
    if action == "move" and not can_write(src_root):
        abort(err("Hier darfst du nur ansehen und kopieren, nicht verschieben.", 403))
    src_base = None
    sources = []
    for p in paths:
        src_base, src = resolve(src_root, str(p))
        if src == src_base or not (src.exists() or src.is_symlink()):
            abort(err(f"„{p}“ gibt es nicht mehr."))
        sources.append(src)
    dest_base, dest_dir = resolve(dest_root, str(data.get("dest_dir", "")))
    if not dest_dir.is_dir():
        abort(err("Der Zielordner existiert nicht."))
    for src in sources:
        if dest_dir == src or src in dest_dir.parents:
            abort(err(f"„{src.name}“ lässt sich nicht in sich selbst kopieren oder verschieben."))
        if dest_dir / src.name in src.parents:
            abort(err(f"Im Ziel heißt ein übergeordneter Ordner von „{src.name}“ genauso. Bitte zuerst umbenennen."))
        if dest_dir == dest_base and is_hidden(src.name, hidden_names(dest_root)):
            abort(err(f"Der Name „{src.name}“ ist im Ziel reserviert."))
    return action, src_root, src_base, sources, dest_root, dest_base, dest_dir, data


@bp.post("/api/kopieren/pruefen")
@login_required
def check():
    """Vor dem Start: Was läge am Ziel schon? Der Browser fragt dann wie unter Windows nach."""
    action, _, _, sources, _, _, dest_dir, _ = _parse()
    names = [s.name for s in sources if s.parent != dest_dir and ((dest_dir / s.name).exists() or (dest_dir / s.name).is_symlink())]
    return jsonify(conflicts=names, action=action)


@bp.post("/api/kopieren")
@login_required
def start():
    action, src_root, src_base, sources, dest_root, dest_base, dest_dir, data = _parse()
    conflict = data.get("conflict", "keep")
    if conflict not in _CONFLICTS:
        conflict = "keep"
    pairs = _targets(action, sources, dest_dir, conflict)
    title = sources[0].name if len(sources) == 1 else f"{len(sources)} Einträge"
    source_owner, owner = quota_owner(src_root), quota_owner(dest_root)
    same_owner = action == "move" and source_owner == owner
    entry = transfers.begin("copy" if action == "copy" else "move", title, 0, tid=str(data.get("t", "")),
                            quota_owner=None if same_owner else owner, result=None)
    log_done = events.later("copy" if action == "copy" else "move",
                            f"{root_label(src_root)}/{rel(src_base, sources[0].parent)} → "
                            f"{root_label(dest_root)}/{rel(dest_base, dest_dir)}: {title}")
    trash_root, trash_base = trash_place(dest_root, dest_base)
    app = current_app._get_current_object()
    threading.Thread(target=_run, args=(app, entry, action, pairs, dest_dir, owner, same_owner, conflict,
                                        trash_root, trash_base, log_done, source_owner),
                     name="pinas-copy", daemon=True).start()
    return jsonify(id=entry["id"])


def _run(app, entry, action, pairs, dest_dir, owner, same_owner, conflict, trash_root, trash_base, log_done, source_owner):
    with app.app_context():
        job = _Job(entry, conflict, trash_root, trash_base)
        try:
            _measure([s for s, _ in pairs], entry)
            with _reserve:
                _check_space(entry, dest_dir, owner, same_owner)
            for src, dest in pairs:
                if action == "copy":
                    job.copy_tree(src, dest)
                else:
                    job.move_tree(src, dest)
                    if not (src.exists() or src.is_symlink()):
                        shares.moved(src, dest)
            entry["size"] = max(entry["size"], entry["done"])
            entry["result"] = {"ok": not job.failed, "copied": job.copied, "skipped": job.skipped, "failed": job.failed[:20]}
            log_done(entry["done"])
        except _Cancelled:
            entry["result"] = {"ok": False, "cancelled": True, "copied": job.copied, "skipped": job.skipped, "failed": []}
        except _NoSpace as exc:
            entry["result"] = {"ok": False, "error": str(exc)}
        except OSError as exc:
            entry["result"] = {"ok": False, "error": exc.strerror or str(exc)}
        except Exception as exc:  # noqa: BLE001 – der Browser muss in jedem Fall ein Ende erfahren
            crashlog.record_exception(exc)
            entry["result"] = {"ok": False, "error": "PiNAS hat einen Fehler aufgeschrieben. Bitte versuch es noch einmal."}
        finally:
            for name in {owner, source_owner}:
                if name:
                    quota.forget(name)
            forget_sizes()
            transfers.end(entry, remember=True)


class _NoSpace(Exception):
    pass


def _check_space(entry, dest_dir, owner, same_owner) -> None:
    """Prüft Platte und Speicherlimit vor dem ersten Byte; was gerade noch läuft, ist schon mitgezählt."""
    filesize = current_app.jinja_env.filters["filesize"]
    pending = sum(max(0, e["size"] - e["done"]) for e in transfers.busy() if e is not entry and e["kind"] in ("copy", "move"))
    free = shutil.disk_usage(dest_dir).free - pending
    rename_only = entry["kind"] == "move"
    if not rename_only and entry["size"] > free:
        raise _NoSpace(f"Auf der Festplatte ist nicht genug Platz: Nötig sind {filesize(entry['size'])}, "
                       f"frei sind noch {filesize(max(0, free))}.")
    if owner and not same_owner:
        limit = quota.quotas().get(owner)
        if limit:
            reserved = sum(max(0, e["size"] - e["done"]) for e in transfers.busy()
                           if e is not entry and e.get("quota_owner") == owner)
            left = limit - quota.used(owner, fresh=True) - reserved
            if entry["size"] > left:
                who = "Dein Speicherplatz" if owner == entry["user"] else f"Der Speicherplatz von {owner}"
                raise _NoSpace(f"{who} reicht dafür nicht: Nötig sind {filesize(entry['size'])}, "
                               f"frei sind noch {filesize(max(0, left))}.")


# ---------------------------------------------------------------- Mehrere auf einmal löschen

@bp.post("/api/loeschen/<root>")
@login_required
def delete_many(root):
    if root not in all_roots():
        abort(404)
    if not can_write(root):
        return err("Dieser Ordner ist für dich nur zum Ansehen freigegeben.", 403)
    paths = (request.get_json(silent=True) or {}).get("paths")
    if not isinstance(paths, list) or not 0 < len(paths) <= _MAX_ITEMS:
        return err("Es ist nichts ausgewählt.")
    deleted, failed = 0, []
    for p in paths:
        base, target = resolve(root, str(p))
        if target == base or not (target.exists() or target.is_symlink()):
            continue  # liegt in einem ebenfalls gewählten Ordner oder ist schon weg
        try:
            size = events.size_of(target)
            move_to_trash(*trash_place(root, base), target)
            shares.moved(target, None)
            events.log("delete", f"{root_label(root)}/{rel(base, target)}", size)
            deleted += 1
        except OSError as exc:
            failed.append(f"{target.name}: {exc.strerror or exc}")
    forget_sizes()
    if quota_owner(root):
        quota.forget(quota_owner(root))
    return jsonify(deleted=deleted, failed=failed)


# ---------------------------------------------------------------- Zielordner wählen

@bp.get("/api/ordner")
@login_required
def folders():
    """Für das Fenster „Kopieren nach“ und „Verschieben nach“."""
    root = request.args.get("root", "")
    roots = all_roots()
    listing = [{"root": k, "label": v, "writable": can_write(k)} for k, v in roots.items()]
    if not root:
        return jsonify(roots=listing)
    if root not in roots:
        abort(404)
    base, target = resolve(root, request.args.get("dir", ""))
    if not target.is_dir():
        abort(err("Diesen Ordner gibt es nicht mehr.", 404))
    hidden = hidden_names(root) if target == base else frozenset()
    names = []
    try:
        with os.scandir(target) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False) and not is_hidden(e.name, hidden) and not _skip_name(e.name):
                    names.append(e.name)
    except OSError as exc:
        abort(err(exc.strerror or str(exc), 500))
    names.sort(key=str.casefold)
    return jsonify(roots=listing, root=root, dir=rel(base, target), writable=can_write(root), folders=names)


# ---------------------------------------------------------------- Textdateien bearbeiten

def editable(name: str) -> bool:
    return Path(name).suffix[1:].lower() in TEXT_EXTS


def _read_text(path: Path) -> tuple[str, str, str]:
    """(Inhalt, Kodierung, Zeilenende). Unbekannte Kodierung: abbrechen, statt die Datei zu beschädigen."""
    raw = path.read_bytes()
    if b"\0" in raw:
        raise ValueError("Das sieht nicht nach einer Textdatei aus.")
    try:
        encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        encoding = "cp1252"
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError as exc:
            raise ValueError("Die Zeichenkodierung dieser Datei ist unbekannt.") from exc
    return text, encoding, "\r\n" if b"\r\n" in raw else "\n"


def _text_target(root: str, sub: str) -> tuple[Path, Path]:
    if root not in all_roots():
        abort(404)
    base, target = resolve(root, sub)
    if target == base or not target.is_file():
        abort(404)
    if not editable(target.name):
        abort(415, "Dieser Dateityp lässt sich hier nicht bearbeiten.")
    if target.stat().st_size > TEXT_MAX:
        abort(413, "Die Datei ist zu groß zum Bearbeiten (höchstens 2 MB).")
    return base, target


@bp.get("/text/<root>/<path:sub>")
@login_required
def edit(root, sub):
    base, target = _text_target(root, sub)
    problem, text = "", ""
    try:
        text = _read_text(target)[0]
    except (ValueError, OSError) as exc:
        problem = str(exc)
    return render_template("editor.html", root=root, sub=rel(base, target), name=target.name, text=text, problem=problem,
                           version=target.stat().st_mtime_ns, writable=can_write(root), place=root_label(root),
                           folder=rel(base, target.parent))


@bp.post("/text/<root>/<path:sub>")
@login_required
def save(root, sub):
    if not can_write(root):
        return err("Diese Datei ist für dich nur zum Ansehen freigegeben.", 403)
    base, target = _text_target(root, sub)
    if request.content_length is None or request.content_length > TEXT_MAX:
        return err("Der Text ist zu lang (höchstens 2 MB).", 413)
    current = target.stat()
    if request.headers.get("X-Force") != "1" and request.headers.get("X-Version") != str(current.st_mtime_ns):
        return err("Die Datei wurde inzwischen von jemand anderem geändert.", 409)
    try:
        old_encoding, newline = _read_text(target)[1:]
    except ValueError as exc:
        return err(str(exc))
    text = request.get_data().decode("utf-8", "replace").replace("\r\n", "\n")
    if newline != "\n":
        text = text.replace("\n", newline)
    try:
        data = text.encode(old_encoding)
    except UnicodeEncodeError:
        data = text.encode("utf-8")  # neue Zeichen passen nicht in die alte Kodierung
    owner = quota_owner(root)
    limit = quota.quotas().get(owner) if owner else None
    if limit and quota.used(owner, fresh=True) + len(data) - current.st_size > limit:
        return err("Dafür reicht der Speicherplatz nicht mehr.", 413)
    tmp = target.parent / f".pinas-upload-{uuid.uuid4().hex}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            shutil.copymode(target, tmp)
        except OSError:
            pass
        os.replace(tmp, target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return err(exc.strerror or str(exc), 500)
    if owner:
        quota.forget(owner)
    forget_sizes()
    events.log("write", f"{root_label(root)}/{rel(base, target)}", len(data))
    # Als Text: So große Zahlen würde JavaScript runden
    return jsonify(ok=True, version=str(target.stat().st_mtime_ns))
