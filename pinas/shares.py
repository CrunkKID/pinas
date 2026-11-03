"""Ordnerfreigaben: Jeder Benutzer kann Ordner aus seinem „Privat“ für andere Benutzer freigeben,
pro Person nur zum Ansehen oder auch zum Ändern.

Gespeichert in den Tabellen shares und share_members (siehe db.py). In der Weboberfläche erscheint jede
Freigabe beim Empfänger als eigener Bereich „freigabe-<Nummer>“ (siehe files.py). Fürs Netzlaufwerk
schreibt pinas-helper daraus eigene Samba-Freigaben. Die Dateien bleiben im privaten Ordner des
Besitzers und zählen auf sein Speicherlimit, Gelöschtes landet in seinem Papierkorb.
"""
import re
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, request, url_for

from . import events, private
from .auth import login_required
from .config import TRASH_NAME, storage
from .db import get_db
from .helper import HelperError

bp = Blueprint("shares", __name__)

PREFIX = "freigabe-"
_ROOT = re.compile(rf"^{PREFIX}([1-9][0-9]{{0,9}})$")
_LEVELS = {"read": 0, "write": 1}
# Diese Zeichen lassen sich in einer Samba-Einstellung nicht sicher schreiben (% wäre dort eine Variable)
_SAMBA_BAD_PATH = re.compile(r"[%\\\x00-\x1f\x7f]")
_SAMBA_BAD_NAME = re.compile(r"[^\w .-]")
_SAMBA_RESERVED = {"privat", "gemeinsam", "global", "homes", "printers"}


def is_share_root(root: str) -> bool:
    return bool(_ROOT.match(root or ""))


def _folder(row) -> Path:
    return storage(current_app.config)["users"] / row["owner"] / row["path"]


def _label(row) -> str:
    return f"{Path(row['path']).name} (von {row['owner']})"


def _active(row) -> bool:
    """Schaltet der Besitzer „Privat“ aus oder fehlt der Ordner, ruht die Freigabe."""
    return private.enabled(row["owner"]) and _inside_owner(row)


def _inside_owner(row) -> bool:
    home = (storage(current_app.config)["users"] / row["owner"]).resolve()
    target = _folder(row).resolve()
    return home in target.parents and target.relative_to(home).parts[0] != TRASH_NAME and target.is_dir()


def for_me(username: str) -> dict:
    """Freigaben für diesen Benutzer: {"freigabe-3": "Urlaub (von anna)"}."""
    rows = get_db().execute(
        "SELECT s.id, s.owner, s.path FROM shares s JOIN share_members m ON m.share_id = s.id "
        "WHERE m.username = ? ORDER BY s.path COLLATE NOCASE", (username,)).fetchall()
    return {f"{PREFIX}{r['id']}": _label(r) for r in rows if _active(r)}


def access(root: str) -> dict | None:
    """{"owner", "path", "folder", "can_write"} der Freigabe, falls der angemeldete Benutzer sie sehen darf."""
    cache = g.setdefault("_shares", {})
    if root not in cache:
        cache[root] = None
        m = _ROOT.match(root or "")
        if m and g.get("user"):
            row = get_db().execute(
                "SELECT s.owner, s.path, m.can_write FROM shares s JOIN share_members m ON m.share_id = s.id "
                "WHERE s.id = ? AND m.username = ?", (int(m.group(1)), g.user["username"])).fetchone()
            if row and _active(row):
                cache[root] = {"owner": row["owner"], "path": row["path"], "folder": _folder(row),
                               "can_write": bool(row["can_write"]), "label": _label(row)}
    return cache[root]


def mine(owner: str) -> list[dict]:
    """Eigene Freigaben für die Konto-Seite."""
    conn = get_db()
    result = []
    for s in conn.execute("SELECT id, owner, path FROM shares WHERE owner = ? ORDER BY path COLLATE NOCASE",
                          (owner,)).fetchall():
        members = conn.execute("SELECT username, can_write FROM share_members WHERE share_id = ? ORDER BY username",
                               (s["id"],)).fetchall()
        result.append({"id": s["id"], "path": s["path"], "active": _active(s),
                       "members": [{"username": m["username"], "can_write": bool(m["can_write"])} for m in members],
                       "samba": samba_name(s) if samba_ok(s["path"]) else None})
    return result


def samba_ok(path: str) -> bool:
    if _SAMBA_BAD_PATH.search(path) or path != path.strip():
        return False
    try:
        path.encode()
    except UnicodeEncodeError:
        return False
    return True


def samba_name(row) -> str:
    """Name im Netzlaufwerk, z. B. „Urlaub von anna“; die Nummer macht ihn eindeutig, falls nötig."""
    name = _SAMBA_BAD_NAME.sub("_", Path(row["path"]).name).strip(" .")[:50] or "Ordner"
    full = f"{name} von {row['owner']}"
    same = get_db().execute("SELECT id, path FROM shares WHERE owner = ? AND id < ?", (row["owner"], row["id"])).fetchall()
    if full.casefold() in _SAMBA_RESERVED or any(
            _SAMBA_BAD_NAME.sub("_", Path(o["path"]).name).strip(" .")[:50].casefold() == name.casefold() for o in same):
        full = f"{full} {row['id']}"
    return full


def sync() -> bool:
    """Schreibt die Freigaben fürs Netzlaufwerk neu. False: Weboberfläche gilt, das Netzlaufwerk noch nicht."""
    conn = get_db()
    lines = []
    for s in conn.execute("SELECT id, owner, path FROM shares ORDER BY id").fetchall():
        if not samba_ok(s["path"]) or not _active(s):
            continue
        members = conn.execute("SELECT username, can_write FROM share_members WHERE share_id = ?", (s["id"],)).fetchall()
        readers = ",".join(m["username"] for m in members if not m["can_write"])
        writers = ",".join(m["username"] for m in members if m["can_write"])
        # „-“ statt leer: Die Shell fasst aufeinanderfolgende Tabulatoren sonst zusammen und vertauscht die Spalten
        lines.append("\t".join((samba_name(s), s["owner"], s["path"], readers or "-", writers or "-")))
    try:
        current_app.extensions["pinas_helper"].set_shares(lines)
        return True
    except HelperError:
        return False  # steht schon im Fehlerprotokoll (helper.py)


def moved(old: Path, new: Path | None) -> None:
    """Ein Ordner im privaten Bereich wurde umbenannt, verschoben (new) oder gelöscht (None):
    Freigaben ziehen mit um. Landet der Ordner außerhalb des eigenen „Privat“, enden sie."""
    users = storage(current_app.config)["users"].resolve()
    try:
        owner, *rest = old.relative_to(users).parts
    except ValueError:
        return  # lag nicht in „Privat“, davon kann nichts freigegeben sein
    old_rel = "/".join(rest)
    new_rel = None
    if new is not None:
        try:
            new_owner, *parts = new.relative_to(users).parts
            if new_owner == owner and parts and parts[0] != TRASH_NAME:
                new_rel = "/".join(parts)
        except ValueError:
            pass
    conn = get_db()
    changed = False
    for s in conn.execute("SELECT id, path FROM shares WHERE owner = ?", (owner,)).fetchall():
        if s["path"] != old_rel and not s["path"].startswith(old_rel + "/"):
            continue
        changed = True
        if new_rel is None:
            conn.execute("DELETE FROM share_members WHERE share_id = ?", (s["id"],))
            conn.execute("DELETE FROM shares WHERE id = ?", (s["id"],))
        else:
            conn.execute("UPDATE shares SET path = ? WHERE id = ?", (new_rel + s["path"][len(old_rel):], s["id"]))
    if changed:
        conn.commit()
        g.pop("_shares", None)
        sync()


def forget_user(conn, username: str) -> None:
    """Benutzer gelöscht: seine Freigaben enden, und er verschwindet aus denen der anderen."""
    conn.execute("DELETE FROM share_members WHERE username = ? OR share_id IN (SELECT id FROM shares WHERE owner = ?)",
                 (username, username))
    conn.execute("DELETE FROM shares WHERE owner = ?", (username,))
    conn.execute("DELETE FROM shares WHERE id NOT IN (SELECT share_id FROM share_members)")
    conn.commit()


def _others(me: str) -> list[str]:
    return [r["username"] for r in get_db().execute("SELECT username FROM users WHERE username <> ? ORDER BY username", (me,))]


def _own_folder(path: str) -> tuple[Path, Path, str]:
    from .files import resolve, rel  # hier importiert, sonst Kreis-Import (files nutzt shares)
    base, target = resolve("privat", path)
    if target == base or not target.is_dir():
        abort(make_error("Nur Ordner in deinem „Privat“ lassen sich freigeben.", 400))
    return base, target, rel(base, target)


def make_error(text: str, status: int):
    resp = jsonify(error=text)
    resp.status_code = status
    return resp


@bp.get("/api/freigabe")
@login_required
def show():
    me = g.user["username"]
    _, _, path = _own_folder(request.args.get("path", ""))
    row = get_db().execute("SELECT id, owner, path FROM shares WHERE owner = ? AND path = ?", (me, path)).fetchone()
    members = {}
    if row:
        members = {m["username"]: ("write" if m["can_write"] else "read") for m in get_db().execute(
            "SELECT username, can_write FROM share_members WHERE share_id = ?", (row["id"],))}
    return jsonify(path=path, name=Path(path).name, users=_others(me), members=members,
                   samba=samba_ok(path), samba_name=samba_name(row) if row and samba_ok(path) else None)


@bp.post("/api/freigabe")
@login_required
def save():
    me = g.user["username"]
    data = request.get_json(silent=True) or {}
    _, _, path = _own_folder(str(data.get("path", "")))
    wanted = data.get("members")
    allowed = set(_others(me))
    if not isinstance(wanted, dict) or any(u not in allowed or v not in _LEVELS for u, v in wanted.items()):
        return make_error("Ungültige Auswahl.", 400)
    conn = get_db()
    row = conn.execute("SELECT id FROM shares WHERE owner = ? AND path = ?", (me, path)).fetchone()
    if row:
        conn.execute("DELETE FROM share_members WHERE share_id = ?", (row["id"],))
    if wanted:
        share_id = row["id"] if row else conn.execute("INSERT INTO shares (owner, path) VALUES (?, ?)", (me, path)).lastrowid
        conn.executemany("INSERT INTO share_members (share_id, username, can_write) VALUES (?, ?, ?)",
                         [(share_id, u, _LEVELS[v]) for u, v in wanted.items()])
    elif row:
        conn.execute("DELETE FROM shares WHERE id = ?", (row["id"],))
    conn.commit()
    g.pop("_shares", None)
    who = ", ".join(f"{u} ({'ändern' if v == 'write' else 'ansehen'})" for u, v in sorted(wanted.items()))
    if wanted:
        events.log("share", f"Privat/{path} → {who}")
    elif row:
        events.log("unshare", f"Privat/{path}")
    return jsonify(ok=True, samba=sync() and samba_ok(path))


@bp.post("/konto/freigabe/<int:share_id>/beenden")
@login_required
def end(share_id):
    conn = get_db()
    row = conn.execute("SELECT id, path FROM shares WHERE id = ? AND owner = ?", (share_id, g.user["username"])).fetchone()
    if not row:
        abort(404)
    conn.execute("DELETE FROM share_members WHERE share_id = ?", (share_id,))
    conn.execute("DELETE FROM shares WHERE id = ?", (share_id,))
    conn.commit()
    g.pop("_shares", None)
    events.log("unshare", f"Privat/{row['path']}")
    flash(f"Die Freigabe für „{Path(row['path']).name}“ ist beendet. Die Dateien bleiben in deinem „Privat“.", "ok")
    if not sync():
        flash("Im Netzlaufwerk konnte das noch nicht übernommen werden. Bitte später noch einmal versuchen.", "error")
    return redirect(url_for("auth.account"))
