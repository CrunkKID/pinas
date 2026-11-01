"""Ruft das privilegierte Hilfsskript (Systembenutzer + Samba-Passwörter) über sudo auf."""
import logging
import os
import subprocess

log = logging.getLogger(__name__)


class HelperError(Exception):
    pass


class SystemHelper:
    def __init__(self, path: str):
        self.path = path

    def _run(self, *args: str, stdin: str | None = None, timeout: int = 30) -> str:
        if not self.path:
            log.info("Entwicklungsmodus – Helper übersprungen: %s", args[:2])
            return ""
        is_root = getattr(os, "geteuid", lambda: 1)() == 0
        cmd = [self.path, *args] if is_root else ["sudo", "-n", self.path, *args]
        try:
            res = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HelperError(f"Hilfsskript konnte nicht ausgeführt werden: {exc}") from exc
        if res.returncode != 0:
            raise HelperError(res.stderr.strip() or f"Hilfsskript-Fehler ({res.returncode})")
        return res.stdout

    def add_user(self, username: str, password: str) -> None:
        self._run("add-user", username, stdin=password + "\n")

    def set_password(self, username: str, password: str) -> None:
        self._run("set-password", username, stdin=password + "\n")

    def delete_user(self, username: str) -> None:
        self._run("delete-user", username)

    def set_private(self, username: str, on: bool) -> None:
        """Freigabe „Privat“ im Netzlaufwerk für diesen Benutzer ein- oder ausschalten."""
        self._run("private-access", username, "on" if on else "off")

    def use_disk(self, device: str, mode: str, layout: str = "folder") -> None:
        """mode "format": Laufwerk löschen und neu formatieren, "keep": ext4-/NTFS-/exFAT-Partition übernehmen.
        layout "folder": eigene Ordner für „Gemeinsam“, "root": „Gemeinsam“ zeigt die ganze Platte."""
        self._run("use-disk", device, mode, layout, timeout=1800)

    def set_layout(self, layout: str) -> None:
        self._run("set-layout", layout, timeout=120)

    def samba_audit(self, on: bool) -> None:
        """Zugriffe übers Netzlaufwerk ins Ereignis-Protokoll schreiben (Samba-Modul full_audit)."""
        self._run("samba-audit", "on" if on else "off", timeout=60)

    def disk_sleep(self) -> None:
        self._run("disk-sleep", timeout=60)

    def power(self, action: str) -> None:
        """shutdown, reboot, safe-shutdown, disk-off, disk-on oder cancel."""
        self._run("power", action, timeout=120)

    def factory_reset(self, wipe: bool) -> None:
        """Startet das Zurücksetzen im Hintergrund; wipe=True leert zusätzlich die Festplatte."""
        self._run("factory-reset", "wipe" if wipe else "keep", timeout=60)

    def update(self, action: str, path: str | None = None) -> str:
        """Update-Center: action "stage" (mit Paketpfad), "apply", "rollback" oder "discard"."""
        return self._run(f"update-{action}", *([path] if path else []), timeout=120)
