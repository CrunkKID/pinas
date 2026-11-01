import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, redirect, url_for

from . import db
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
    app.config.update(
        SECRET_KEY=_load_secret(app.config["SECRET_FILE"]),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        MAX_CONTENT_LENGTH=None,
    )
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

    from . import admin, auth, dashboard, disk, files, trash
    app.register_blueprint(auth.bp)
    app.register_blueprint(files.bp)
    app.register_blueprint(trash.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(disk.bp)

    @app.get("/")
    def index():
        return redirect(url_for("files.browse", root="privat"))

    @app.template_filter("filesize")
    def filesize(num):
        num = float(num or 0)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if num < 1024 or unit == "TB":
                return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}".replace(".", ",")
            num /= 1024

    return app
