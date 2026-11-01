"""Dauerhafte Statistik, die Neustarts übersteht: Gesamtbetriebszeit und höchste CPU-Temperatur.

Ein Hintergrund-Thread misst alle 10 Sekunden. Gespeichert wird höchstens einmal pro Minute
(und sofort bei einem neuen Temperatur-Höchstwert), um die SD-Karte zu schonen. Beim Herunterfahren,
Neustart oder Update (der Dienst wird gestoppt) wird zusätzlich sofort gespeichert – nur bei einem
plötzlichen Stromausfall kann höchstens eine Minute Betriebszeit fehlen.

Ein Neustart des Dienstes ohne Neustart des Pi (z. B. Update) zählt nichts doppelt: Die Laufzeit kommt
aus /proc/uptime und gehört zur selben boot_id.
"""
import json
import os
import threading
import time
from pathlib import Path


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


class History:
    SAVE_EVERY = 60

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"total_before": 0.0, "boot_id": None, "last_uptime": 0.0,
                     "since": None, "temp_max": None, "temp_max_at": None}
        try:
            self.data.update(json.loads(path.read_text()))
        except (OSError, ValueError):
            pass
        self._saved = 0.0
        self._thread = None

    def sample(self) -> None:
        boot_id = _read("/proc/sys/kernel/random/boot_id")
        raw = _read("/proc/uptime")
        if not boot_id or not raw:
            return  # kein Linux (lokaler Testlauf)
        uptime = float(raw.split()[0])
        t = _read("/sys/class/thermal/thermal_zone0/temp")
        temp = round(int(t) / 1000, 1) if t and t.isdigit() else None

        with self.lock:
            d = self.data
            changed = False
            if d["since"] is None:
                d["since"] = time.time() - uptime
                changed = True
            if d["boot_id"] != boot_id:
                # Neuer Start: Laufzeit des vorherigen Starts zur Gesamtzeit addieren
                if d["boot_id"] is not None:
                    d["total_before"] += d["last_uptime"]
                d["boot_id"] = boot_id
                changed = True
            d["last_uptime"] = uptime
            if temp is not None and (d["temp_max"] is None or temp > d["temp_max"]):
                d["temp_max"], d["temp_max_at"] = temp, time.time()
                changed = True
            if changed or time.monotonic() - self._saved >= self.SAVE_EVERY:
                self._save()

    def flush(self) -> None:
        """Letzten Stand sofort speichern (beim Beenden des Dienstes)."""
        self.sample()
        with self.lock:
            self._save()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.data))
            os.replace(tmp, self.path)
            self._saved = time.monotonic()
        except OSError:
            pass

    def snapshot(self) -> dict:
        with self.lock:
            d = dict(self.data)
        return {"total": d["total_before"] + d["last_uptime"], "since": d["since"],
                "temp_max": d["temp_max"], "temp_max_at": d["temp_max_at"]}

    def start(self, interval: int = 10) -> None:
        if self._thread:
            return

        def loop():
            while True:
                self.sample()
                time.sleep(interval)

        self._thread = threading.Thread(target=loop, name="pinas-history", daemon=True)
        self._thread.start()
