"""PiNAS als App aufs Handy (Progressive Web App): Manifest, Service Worker und Offline-Seite.

Installiert wird ohne App Store direkt aus dem Browser („Zum Startbildschirm hinzufügen“).
Voraussetzung für Android/Chrome: https mit installiertem PiNAS-Zertifikat (siehe tls.py).
"""
from flask import Blueprint, current_app, g, jsonify, make_response, render_template, send_from_directory, url_for

bp = Blueprint("pwa", __name__)

THEME = "#c51a4a"


@bp.get("/manifest.webmanifest")
def manifest():
    icons = [
        {"src": url_for("static", filename="app/icon-192.png"), "sizes": "192x192", "type": "image/png"},
        {"src": url_for("static", filename="app/icon-512.png"), "sizes": "512x512", "type": "image/png"},
        {"src": url_for("static", filename="app/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ]
    resp = jsonify({
        "name": "PiNAS",
        "short_name": "PiNAS",
        "description": "Dein NAS auf dem Raspberry Pi – Dateien, Dashboard und Einstellungen",
        "lang": "de",
        "id": "/",
        "start_url": "/?app=1",
        "scope": "/",
        "display": "standalone",
        "background_color": "#161616",
        "theme_color": THEME,
        "icons": icons,
        "shortcuts": [
            {"name": "Dateien", "url": "/", "icons": [icons[0]]},
            {"name": "Dashboard", "url": url_for("dashboard.index"), "icons": [icons[0]]},
        ],
    })
    resp.mimetype = "application/manifest+json"
    return resp


@bp.get("/sw.js")
def service_worker():
    # Muss unter "/" liegen, damit er für die ganze Oberfläche gilt; nie aus dem Cache laden
    resp = make_response(send_from_directory(current_app.static_folder, "sw.js"))
    resp.headers["Content-Type"] = "text/javascript; charset=utf-8"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.get("/offline")
def offline():
    """Wird vom Service Worker angezeigt, wenn das NAS nicht erreichbar ist (z. B. unterwegs)."""
    g.user = None  # wird zwischengespeichert – also ohne Menü und ohne persönliche Daten
    return render_template("offline.html")
