from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from . import quota, users
from .auth import admin_required, helper
from .config import storage
from .db import get_db

bp = Blueprint("admin", __name__, url_prefix="/benutzer")


@bp.get("/")
@admin_required
def index():
    rows = users.list_users(get_db())
    return render_template("users.html", users=rows, space={u["username"]: quota.info(u["username"]) for u in rows})


@bp.post("/<username>/speicher")
@admin_required
def space(username):
    """Nur Administratoren legen fest, wie viel Platz ein Benutzer in „Privat“ belegen darf."""
    if not users.get_user(get_db(), username):
        flash("Unbekannter Benutzer.", "error")
        return redirect(url_for("admin.index"))
    raw = request.form.get("gb", "").strip().replace(",", ".")
    try:
        gb = float(raw) if raw else 0.0
        if gb < 0 or gb > 100_000:
            raise ValueError
    except ValueError:
        flash("Bitte eine Größe in GB eingeben (leer = unbegrenzt).", "error")
        return redirect(url_for("admin.index"))
    quota.set_quota(username, int(gb * 1024**3) if gb else None)
    if gb:
        flash(f"„{username}“ darf jetzt {raw.replace('.', ',')} GB belegen.", "ok")
    else:
        flash(f"„{username}“ hat jetzt unbegrenzten Speicherplatz.", "ok")
    return redirect(url_for("admin.index"))


@bp.post("/neu")
@admin_required
def create():
    f = request.form
    if f.get("password") != f.get("confirm"):
        flash("Die Passwörter stimmen nicht überein.", "error")
    else:
        try:
            name = users.create_user(get_db(), helper(), storage(current_app.config)["users"],
                                     f.get("username", ""), f.get("password", ""), f.get("is_admin") == "1")
            flash(f"Benutzer „{name}“ angelegt.", "ok")
        except users.UserError as exc:
            flash(str(exc), "error")
    return redirect(url_for("admin.index"))


@bp.post("/<username>/passwort")
@admin_required
def password(username):
    try:
        users.set_password(get_db(), helper(), username, request.form.get("password", ""))
        flash(f"Passwort für „{username}“ geändert.", "ok")
    except users.UserError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.index"))


@bp.post("/<username>/loeschen")
@admin_required
def delete(username):
    if username == g.user["username"]:
        flash("Du kannst dich nicht selbst löschen.", "error")
    else:
        try:
            users.delete_user(get_db(), helper(), username)
            quota.set_quota(username, None)
            flash(f"Benutzer „{username}“ gelöscht. Der private Ordner wurde behalten.", "ok")
        except users.UserError as exc:
            flash(str(exc), "error")
    return redirect(url_for("admin.index"))
