"""Lokaler Testlauf ohne Samba/Root (z. B. auf dem PC):

    python run_dev.py add-user test --admin   # Benutzer anlegen
    python run_dev.py                         # Server auf http://localhost:8080
"""
import os
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
os.environ.setdefault("PINAS_DATA", str(here / "devdata" / "nas"))
os.environ.setdefault("PINAS_STATE", str(here / "devdata" / "state"))
os.environ.setdefault("PINAS_HELPER", "")  # Entwicklungsmodus: keine Systembenutzer/Samba
os.environ.setdefault("PINAS_HOST", "127.0.0.1")

if len(sys.argv) > 1:
    from pinas.cli import main
else:
    from pinas.__main__ import main
main()
