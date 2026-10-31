"""Konfiguration über Umgebungsvariablen (gesetzt in der systemd-Unit)."""
import os
from pathlib import Path


def load_config() -> dict:
    data_dir = Path(os.environ.get("PINAS_DATA", "/srv/nas"))
    state_dir = Path(os.environ.get("PINAS_STATE", "/var/lib/pinas"))
    return {
        "DATA_DIR": data_dir,
        "USERS_DIR": data_dir / "users",
        "SHARED_DIR": data_dir / "shared",
        "DB_PATH": state_dir / "pinas.db",
        "SECRET_FILE": state_dir / "secret.key",
        # Leerer Wert = Entwicklungsmodus ohne Samba/Systembenutzer
        "HELPER": os.environ.get("PINAS_HELPER", "/usr/local/sbin/pinas-helper"),
        "HOST": os.environ.get("PINAS_HOST", "0.0.0.0"),
        "PORT": int(os.environ.get("PINAS_PORT", "8080")),
    }
