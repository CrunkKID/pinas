"""Startet den Webserver:  python -m pinas"""
import logging
import tempfile

from . import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = create_app()
    app.extensions["pinas_history"].start()
    from .trash import start_purger
    start_purger(app)
    host, port = app.config["HOST"], app.config["PORT"]
    # Upload-Zwischendateien auf die NAS-Platte statt auf die SD-Karte
    tmp = app.config["DATA_DIR"] / ".tmp"
    if tmp.is_dir():
        tempfile.tempdir = str(tmp)
    try:
        from waitress import serve
    except ImportError:
        app.run(host=host, port=port)
        return
    # Große Uploads erlauben (Standard von waitress wäre 1 GB)
    serve(app, host=host, port=port, threads=8, max_request_body_size=256 * 1024**3)


if __name__ == "__main__":
    main()
