import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

from . import crashlog, db
from .config import load_config, storage
from .helper import SystemHelper
from .history import History


def _load_secret(path: Path) -> str:
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    path.write_text(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.update(load_config())
    crashlog.setup(app.config["DB_PATH"].parent)
    app.config.update(
        SECRET_KEY=_load_secret(app.config["SECRET_FILE"]),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        MAX_CONTENT_LENGTH=None,
    )
    if app.config["PROXY"]:
        # Echte Client-IP (für die Login-Sperre) und https von nginx übernehmen
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
        app.config["SESSION_COOKIE_SECURE"] = True
    paths = storage(app.config)
    for d in (paths["users"], paths["shared"]):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # z. B. Platte nur lesend eingebunden
    db.init_db(app.config["DB_PATH"])
    app.teardown_appcontext(db.close_db)
    app.extensions["pinas_helper"] = SystemHelper(app.config["HELPER"])
    app.extensions["pinas_history"] = History(app.config["DB_PATH"].parent / "history.json")

    from . import admin, auth, dashboard, disk, fileops, files, pwa, shares, tls, trash, updates
    app.register_blueprint(auth.bp)
    app.register_blueprint(pwa.bp)
    app.register_blueprint(tls.bp)
    app.register_blueprint(updates.bp)
    app.register_blueprint(files.bp)
    app.register_blueprint(fileops.bp)
    app.register_blueprint(shares.bp)
    app.register_blueprint(trash.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(disk.bp)

    @app.get("/")
    def index():
        # Ohne eingerichtetes „Privat“ leitet files.browse weiter nach „Gemeinsam“
        return redirect(url_for("files.browse", root="privat"))

    @app.errorhandler(503)
    def unavailable(exc):
        # Festplatte aus: Seiten behalten das Menü, damit man zum Dashboard kommt. Skripte bekommen weiter die 503
        if request.method != "GET" or request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
            return exc
        try:
            return render_template("unavailable.html", text=exc.description), 503
        except Exception:  # noqa: BLE001
            return exc

    @app.errorhandler(500)
    def server_error(exc):
        err_id = crashlog.record_exception(getattr(exc, "original_exception", None) or exc)
        text = "PiNAS hat einen Fehler aufgeschrieben. Bitte versuch es noch einmal."
        if request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
            return jsonify(error=text, fehler=err_id), 500
        try:
            return render_template("error.html", err_id=err_id), 500
        except Exception:  # noqa: BLE001 – die Fehlerseite selbst darf nicht scheitern
            crashlog.record("web", "Die Fehlerseite konnte nicht angezeigt werden")
            return f"<!doctype html><meta charset=utf-8><title>Fehler</title><p>{text} (Nummer {err_id})</p>", 500

    @app.template_filter("filesize")
    def filesize(num):
        num = float(num or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if num < 1024 or unit == "TB":
                return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}".replace(".", ",")
            num /= 1024

    return app
