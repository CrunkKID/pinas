"""Sichere Verbindung: PiNAS-Zertifikat herunterladen und Anleitung zum Installieren."""
import hashlib
import ssl

from flask import Blueprint, abort, current_app, render_template, request, send_file

bp = Blueprint("tls", __name__)


def ca_info() -> dict | None:
    """Fingerabdrücke der PiNAS-Zertifizierungsstelle (erzeugt von pinas-cert)."""
    try:
        der = ssl.PEM_cert_to_DER_cert(current_app.config["CA_CERT"].read_text())
    except (OSError, ValueError):
        return None
    fmt = lambda digest: ":".join(f"{b:02X}" for b in digest)
    return {"sha1": fmt(hashlib.sha1(der).digest()), "sha256": fmt(hashlib.sha256(der).digest())}


@bp.get("/zertifikat")
def index():
    return render_template("certificate.html", ca=ca_info(), secure=request.is_secure)


@bp.get("/zertifikat/pinas.crt")
def download():
    path = current_app.config["CA_CERT"]
    if not path.is_file():
        abort(404)
    # Dieser Typ öffnet unter Windows, Android und iOS direkt den Zertifikat-Import
    return send_file(path, mimetype="application/x-x509-ca-cert", as_attachment=True,
                     download_name="PiNAS-Zertifikat.crt", max_age=0)
