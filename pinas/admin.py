from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from . import users
from .auth import admin_required, helper
from .config import storage
from .db import get_db

bp = Blueprint("admin", __name__, url_prefix="/benutzer")


@bp.get("/")
@admin_required
def index():
    return render_template("users.html", users=users.list_users(get_db()))


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
            flash(f"Benutzer „{username}“ gelöscht. Der private Ordner wurde behalten.", "ok")
        except users.UserError as exc:
            flash(str(exc), "error")
    return redirect(url_for("admin.index"))
