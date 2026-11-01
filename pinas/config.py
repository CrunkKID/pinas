"""Konfiguration über Umgebungsvariablen (gesetzt in der systemd-Unit)."""
import os
from pathlib import Path

SETTINGS_FILE = Path("/etc/default/pinas")

# Papierkorb-Ordner ganz oben in „Privat“ und „Gemeinsam“ (gleicher Name wie bei Samba "recycle")
TRASH_NAME = ".papierkorb"

# Ordner, die im Modus "root" in „Gemeinsam“ ausgeblendet und geschützt werden
# (PiNAS-eigene Ordner und Systemordner von Linux/Windows)
ROOT_MODE_HIDDEN = frozenset({".pinas", ".tmp", "lost+found", "System Volume Information", "$RECYCLE.BIN"})


def load_config() -> dict:
    data_dir = Path(os.environ.get("PINAS_DATA", "/srv/nas"))
    state_dir = Path(os.environ.get("PINAS_STATE", "/var/lib/pinas"))
    return {
        "DATA_DIR": data_dir,
        "DB_PATH": state_dir / "pinas.db",
        "SECRET_FILE": state_dir / "secret.key",
        # Leerer Wert = Entwicklungsmodus ohne Samba/Systembenutzer
        "HELPER": os.environ.get("PINAS_HELPER", "/usr/local/sbin/pinas-helper"),
        "HOST": os.environ.get("PINAS_HOST", "0.0.0.0"),
        "PORT": int(os.environ.get("PINAS_PORT", "8080")),
        # Hinter nginx mit https (auf dem Pi immer, im Entwicklungsmodus nicht)
        "PROXY": os.environ.get("PINAS_PROXY") == "1",
        "CA_CERT": Path(os.environ.get("PINAS_CA_CERT", "/etc/pinas/tls/ca.crt")),
        # Update-Center: Status des Installers und GitHub-Repository für „Nach Updates suchen“
        "UPDATE_DIR": Path(os.environ.get("PINAS_UPDATE_DIR", "/var/lib/pinas-update")),
        "UPDATE_REPO": os.environ.get("PINAS_UPDATE_REPO", "CrunkKID/pinas"),
    }


def shared_mode() -> str:
    """"folder": eigene Ordner users/ und shared/ (Standard).
    "root": „Gemeinsam“ zeigt die ganze Platte (z. B. vom Windows-PC übernommene Dateien).

    Wird bei jedem Aufruf frisch gelesen, damit eine Umstellung ohne Neustart wirkt."""
    try:
        for line in SETTINGS_FILE.read_text().splitlines():
            if line.startswith("PINAS_SHARED_MODE="):
                return "root" if line.split("=", 1)[1].strip() == "root" else "folder"
    except OSError:
        pass
    return "root" if os.environ.get("PINAS_SHARED_MODE") == "root" else "folder"


def storage(cfg) -> dict:
    data = cfg["DATA_DIR"]
    if shared_mode() == "root":
        return {"mode": "root", "users": data / ".pinas" / "users", "shared": data, "hidden": ROOT_MODE_HIDDEN}
    return {"mode": "folder", "users": data / "users", "shared": data / "shared", "hidden": frozenset()}
