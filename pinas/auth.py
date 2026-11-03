import functools
import hashlib
import hmac
import secrets
import threading
import time

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, session, url_for)

from . import events, transfers, users
from .config import storage
from .db import get_db
from .helper import HelperError

bp = Blueprint("auth", __name__)

_MAX_FAILS = 5
_FAIL_WINDOW = 300  # Sekunden
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def _recent_fails(ip: str) -> list[float]:
    now = time.monotonic()
    with _fails_lock:
        _fails[ip] = [t for t in _fails.get(ip, []) if now - t < _FAIL_WINDOW]
        return _fails[ip]


def _record_fail(ip: str) -> None:
    with _fails_lock:
        _fails.setdefault(ip, []).append(time.monotonic())


def helper():
    return current_app.extensions["pinas_helper"]


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _pw_stamp(row) -> str:
    """Fingerabdruck des Passworts im Sitzungs-Cookie: Ändert es sich, gelten alte Anmeldungen nicht mehr."""
    return hashlib.sha256(row["pw_hash"].encode()).hexdigest()


def keep_login() -> None:
    """Nach dem eigenen Passwortwechsel: Dieser Browser bleibt angemeldet, alle anderen Geräte müssen sich neu anmelden."""
    session["pw"] = _pw_stamp(users.get_user(get_db(), g.user["username"]))


def _revoke_session() -> None:
    """Nach dem Abmelden gilt diese Sitzung auch dann nicht mehr, wenn jemand das Cookie kopiert hat."""
    sid = session.get("sid")
    if sid:
        conn = get_db()
        conn.execute("DELETE FROM revoked_sessions WHERE expires < ?", (time.time(),))
        conn.execute("INSERT OR REPLACE INTO revoked_sessions (sid, expires) VALUES (?, ?)",
                     (sid, time.time() + current_app.permanent_session_lifetime.total_seconds()))
        conn.commit()


@bp.app_context_processor
def inject():
    return {"csrf_token": csrf_token, "user": g.get("user")}


_setup_lock = threading.Lock()


def setup_done() -> bool:
    if not current_app.config.get("SETUP_DONE"):
        current_app.config["SETUP_DONE"] = get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    return current_app.config["SETUP_DONE"]


@bp.before_app_request
def load_user_and_check_csrf():
    # Zertifikat-Seite schon vor der Einrichtung erreichbar, damit die Warnung gleich verschwindet
    if request.endpoint not in ("auth.setup", "static") and request.blueprint not in ("tls", "pwa") and not setup_done():
        return redirect(url_for("auth.setup"))
    g.user = None
    uid = session.get("uid")
    if uid is not None:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        sid = session.get("sid")
        if (g.user is None or not hmac.compare_digest(session.get("pw", ""), _pw_stamp(g.user))
                or (sid and get_db().execute("SELECT 1 FROM revoked_sessions WHERE sid = ?", (sid,)).fetchone())):
            g.user = None
            session.clear()
        elif not sid:
            session["sid"] = secrets.token_urlsafe(16)  # Anmeldungen von vor dieser Version
    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf", "")
        # Ohne Sitzung gibt es keinen gültigen Wert: Leer darf nicht zu leer passen
        if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
            abort(400, "Ungültiges oder abgelaufenes Formular – bitte Seite neu laden.")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not g.user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr or "?"
        name = request.form.get("username", "").strip().lower()
        if len(_recent_fails(ip)) >= _MAX_FAILS:
            events.log("login_block", username=name)
            flash("Zu viele Fehlversuche. Bitte in ein paar Minuten erneut versuchen.", "error")
            return render_template("login.html"), 429
        row = users.verify(get_db(), request.form.get("username", ""), request.form.get("password", ""))
        if row is None:
            _record_fail(ip)
            events.log("login_fail", "mit Benutzername „%s“" % name[:40], username=name[:40])
            flash("Benutzername oder Passwort falsch.", "error")
            return render_template("login.html"), 401
        session.clear()
        session.permanent = True
        session["uid"] = row["id"]
        session["pw"] = _pw_stamp(row)
        session["sid"] = secrets.token_urlsafe(16)
        events.log("login", username=row["username"])
        nxt = request.args.get("next", "")
        # Nur relative Pfade zulassen (kein Open Redirect)
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = url_for("index")
        return redirect(nxt)
    return render_template("login.html")


@bp.route("/einrichtung", methods=["GET", "POST"])
def setup():
    """Ersteinrichtung nach dem ersten Start: legt den ersten Administrator an."""
    if setup_done():
        return redirect(url_for("index"))
    if request.method == "POST":
        f = request.form
        if f.get("password") != f.get("confirm"):
            flash("Die Passwörter stimmen nicht überein.", "error")
        else:
            with _setup_lock:
                if setup_done():
                    return redirect(url_for("auth.login"))
                try:
                    users.create_user(get_db(), helper(), storage(current_app.config)["users"],
                                      f.get("username", ""), f.get("password", ""), True)
                except users.UserError as exc:
                    flash(str(exc), "error")
                    return render_template("setup.html")
                current_app.config["SETUP_DONE"] = True
            row = users.get_user(get_db(), f.get("username", "").strip().lower())
            session.clear()
            session.permanent = True
            session["uid"] = row["id"]
            session["pw"] = _pw_stamp(row)
            session["sid"] = secrets.token_urlsafe(16)
            flash("Willkommen! Als Nächstes die Festplatte einrichten.", "ok")
            return redirect(url_for("disk.index"))
    return render_template("setup.html")


@bp.post("/logout")
def logout():
    # Ohne Anmeldung würden laufende Übertragungen dieses Browsers abbrechen
    running = transfers.mine() if g.user else []
    if running:
        flash(transfers.describe(running), "error")
        return redirect(url_for("index"))
    events.log("logout")
    _revoke_session()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/konto", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        conn = get_db()
        if not users.verify(conn, g.user["username"], request.form.get("current", "")):
            flash("Das aktuelle Passwort ist falsch.", "error")
        elif request.form.get("new") != request.form.get("confirm"):
            flash("Die neuen Passwörter stimmen nicht überein.", "error")
        else:
            try:
                users.set_password(conn, helper(), g.user["username"], request.form.get("new", ""))
                keep_login()
                flash("Dein Passwort wurde geändert. Es gilt ab sofort auch für das Netzlaufwerk. "
                      "Deine anderen Geräte wurden abgemeldet.", "ok")
                return redirect(url_for("auth.account"))
            except users.UserError as exc:
                flash(str(exc), "error")
    from . import private, shares
    from .quota import info
    return render_template("account.html", space=info(g.user["username"]),
                           has_private=private.enabled(g.user["username"]),
                           my_shares=shares.mine(g.user["username"]))


@bp.post("/konto/privat")
@login_required
def private_drive():
    """Jeder Benutzer richtet sein privates Laufwerk selbst ein – oder schaltet es wieder aus."""
    from . import private, shares
    on = request.form.get("on") == "1"
    try:
        private.set_enabled(g.user["username"], on, helper())
    except HelperError as exc:
        flash(f"Das hat nicht geklappt: {exc}", "error")
        return redirect(url_for("auth.account"))
    if shares.mine(g.user["username"]):
        shares.sync()  # Freigaben ruhen, solange „Privat“ aus ist
    if on:
        flash("Dein privates Laufwerk „Privat“ ist eingerichtet – nur du siehst die Dateien darin.", "ok")
        return redirect(url_for("files.browse", root="privat"))
    flash("„Privat“ ist ausgeschaltet. Deine Dateien darin bleiben erhalten und sind wieder da, "
          "sobald du es erneut einrichtest.", "ok")
    return redirect(url_for("auth.account"))
