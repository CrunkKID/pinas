"""Startet den Webserver:  python -m pinas"""
import logging
import signal
import sys
import tempfile

from . import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = create_app()
    history = app.extensions["pinas_history"]
    history.start()

    # Herunterfahren, Neustart, Update: systemd beendet den Dienst mit SIGTERM –
    # vorher die Betriebszeit sichern, damit keine Minute verloren geht
    def stop(_signum, _frame):
        history.flush()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    from .trash import start_purger
    start_purger(app)
    from .updates import start_reboot_watch
    start_reboot_watch(app)
    from .events import start_importer
    start_importer(app)
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
