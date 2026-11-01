"""Papierkorb: Gelöschtes landet in ".papierkorb" (ganz oben in „Privat“ bzw. „Gemeinsam“)
und wird nach einstellbar vielen Tagen endgültig entfernt.

Aufbau wie beim Samba-Modul "recycle" (keeptree): die Ordnerstruktur bleibt erhalten, der
Löschzeitpunkt steht in der Zugriffszeit (atime) jeder Datei. So landen auch Dateien, die über
das Netzlaufwerk gelöscht werden, im selben Papierkorb.
"""
import json
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, make_response, redirect, render_template,
                   request, url_for)

from .auth import admin_required, login_required
from .config import TRASH_NAME, storage
from .files import ROOTS, rel, root_dir, unique_path, user_roots

bp = Blueprint("trash", __name__, url_prefix="/papierkorb")

RETENTION_CHOICES = (1, 3, 7, 14, 30, 60, 90, 0)  # 0 = nie automatisch leeren
DEFAULT_DAYS = 30


# ---------------------------------------------------------------- Einstellungen

def _settings_path(cfg) -> Path:
    return cfg["DB_PATH"].parent / "trash.json"


def retention_days(cfg) -> int:
    try:
        days = int(json.loads(_settings_path(cfg).read_text()).get("days", DEFAULT_DAYS))
        return days if days in RETENTION_CHOICES else DEFAULT_DAYS
    except (OSError, ValueError):
        return DEFAULT_DAYS


# ---------------------------------------------------------------- Verschieben / Leeren

def _mark_deleted(path: Path, now: float) -> None:
    """Löschzeitpunkt als Zugriffszeit setzen, Änderungszeit bleibt erhalten."""
    items = [path]
    if path.is_dir() and not path.is_symlink():
        items += [Path(d) / n for d, dirs, files in os.walk(path) for n in dirs + files]
    for p in items:
        try:
            os.utime(p, (now, p.lstat().st_mtime), follow_symlinks=False)
        except (OSError, NotImplementedError):
            pass


def move_to_trash(root: str, base: Path, target: Path) -> Path:
    """Verschiebt Datei/Ordner in den Papierkorb und behält dabei den Ordnerpfad bei."""
    dest = base / TRASH_NAME / target.relative_to(base)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        dest = unique_path(dest.with_name(f"{dest.stem} (gelöscht {stamp}){dest.suffix}"))
    shutil.move(str(target), str(dest))
    _mark_deleted(dest, time.time())
    return dest


def _deleted_at(path: Path) -> float:
    return path.lstat().st_atime


def purge_dir(trash: Path, cutoff: float) -> int:
    """Entfernt alles, was vor `cutoff` gelöscht wurde, und danach leere Ordner."""
    removed = 0
    if not trash.is_dir():
        return 0
    for dirpath, dirs, files in os.walk(trash, topdown=False):
        for name in files:
            p = Path(dirpath) / name
            try:
                if _deleted_at(p) < cutoff:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
        for name in dirs:
            p = Path(dirpath) / name
            try:
                if not any(p.iterdir()) and _deleted_at(p) < cutoff:
                    p.rmdir()
            except OSError:
                pass
    return removed


def all_trash_dirs(cfg) -> list[Path]:
    paths = storage(cfg)
    dirs = [paths["shared"] / TRASH_NAME]
    if paths["users"].is_dir():
        dirs += [u / TRASH_NAME for u in paths["users"].iterdir() if u.is_dir()]
    return dirs


def purge_all(cfg) -> None:
    days = retention_days(cfg)
    if not days:
        return
    cutoff = time.time() - days * 86400
    for d in all_trash_dirs(cfg):
        purge_dir(d, cutoff)


def start_purger(app, interval: int = 3600) -> None:
    """Prüft beim Start und danach stündlich, ob alte Dateien endgültig gelöscht werden müssen."""
    def loop():
        while True:
            try:
                purge_all(app.config)
            except Exception:  # noqa: BLE001 – der Hintergrund-Thread darf nie abbrechen
                app.logger.exception("Papierkorb konnte nicht geleert werden")
            time.sleep(interval)

    threading.Thread(target=loop, name="pinas-trash", daemon=True).start()


# ---------------------------------------------------------------- Seiten

def trash_base(root: str) -> Path:
    base = root_dir(root) / TRASH_NAME
    try:
        base.mkdir(exist_ok=True)
    except OSError:
        pass
    return base.resolve()


def resolve_in_trash(root: str, sub: str) -> tuple[Path, Path]:
    base = trash_base(root)
    target = (base / (sub or "").strip("/")).resolve()
    if target != base and base not in target.parents:
        abort(403)
    return base, target


def _tree_size(path: Path) -> int:
    if not path.is_dir():
        return path.lstat().st_size
    total = 0
    for dirpath, _, files in os.walk(path):
        for n in files:
            try:
                total += (Path(dirpath) / n).lstat().st_size
            except OSError:
                pass
    return total


@bp.get("/<root>/")
@bp.get("/<root>/<path:sub>")
@login_required
def browse(root, sub=""):
    if root not in ROOTS:
        abort(404)
    if root not in user_roots():
        return redirect(url_for("trash.browse", root="gemeinsam"))
    base, target = resolve_in_trash(root, sub)
    if not target.is_dir():
        abort(404)
    days = retention_days(current_app.config)
    entries, total = [], 0
    with os.scandir(target) as it:
        for e in it:
            p = Path(e.path)
            try:
                deleted = _deleted_at(p)
                size = _tree_size(p)
            except OSError:
                continue
            total += size
            entries.append({
                "name": e.name,
                "path": rel(base, p),
                "is_dir": e.is_dir(follow_symlinks=False),
                "size": size,
                "deleted": datetime.fromtimestamp(deleted).strftime("%d.%m.%Y %H:%M"),
                "expires": (datetime.fromtimestamp(deleted + days * 86400).strftime("%d.%m.%Y")
                            if days else None),
            })
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].casefold()))
    crumbs, acc = [], []
    for part in Path(rel(base, target)).parts:
        acc.append(part)
        crumbs.append((part, "/".join(acc)))
    resp = make_response(render_template(
        "trash.html", root=root, roots=user_roots(), sub=rel(base, target), entries=entries, crumbs=crumbs,
        total=total, days=days, choices=RETENTION_CHOICES))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.post("/<root>/wiederherstellen")
@login_required
def restore(root):
    if root not in ROOTS:
        abort(404)
    base, src = resolve_in_trash(root, request.form.get("path", ""))
    if src == base or not (src.exists() or src.is_symlink()):
        abort(400)
    files_base = root_dir(root)
    dest = files_base / src.relative_to(base)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest = unique_path(dest)
        shutil.move(str(src), str(dest))
        where = rel(files_base, dest.parent) or ROOTS[root]
        flash(f"„{dest.name}“ wiederhergestellt (in {where}).", "ok")
    except OSError as exc:
        flash(f"Wiederherstellen fehlgeschlagen: {exc}", "error")
    return redirect(url_for("trash.browse", root=root, sub=request.form.get("dir", "")))


@bp.post("/<root>/loeschen")
@login_required
def delete_forever(root):
    if root not in ROOTS:
        abort(404)
    base, target = resolve_in_trash(root, request.form.get("path", ""))
    if target == base or not (target.exists() or target.is_symlink()):
        abort(400)
    try:
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        flash(f"„{target.name}“ endgültig gelöscht.", "ok")
    except OSError as exc:
        flash(f"Löschen fehlgeschlagen: {exc}", "error")
    return redirect(url_for("trash.browse", root=root, sub=request.form.get("dir", "")))


@bp.post("/<root>/leeren")
@login_required
def empty(root):
    if root not in ROOTS:
        abort(404)
    base = trash_base(root)
    errors = 0
    for p in list(base.iterdir()):
        try:
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
        except OSError:
            errors += 1
    flash("Papierkorb geleert." if not errors else f"Papierkorb geleert, {errors} Einträge ließen sich nicht löschen.",
          "ok" if not errors else "error")
    return redirect(url_for("trash.browse", root=root))


@bp.post("/einstellungen")
@admin_required
def settings():
    try:
        days = int(request.form.get("days", ""))
    except ValueError:
        days = -1
    if days not in RETENTION_CHOICES:
        flash("Ungültige Auswahl.", "error")
    else:
        path = _settings_path(current_app.config)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"days": days}))
        os.replace(tmp, path)
        flash("Gespeichert. " + (f"Gelöschtes wird nach {days} {'Tag' if days == 1 else 'Tagen'} endgültig entfernt."
                                 if days else "Der Papierkorb wird nicht mehr automatisch geleert."), "ok")
        purge_all(current_app.config)
    return redirect(url_for("trash.browse", root=request.form.get("root", "privat")))
