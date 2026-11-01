# PiNAS-App für Android

Die Weboberfläche des PiNAS als eigene App (Kotlin, ab Android 10). Sie baut auf der Web-App aus
Version 1.2.4 auf (`pinas/pwa.py`): gleiche Symbole, gleiche Startseite `/?app=1`, gleiche
Verknüpfungen „Dateien“ und „Dashboard“, eigene Anzeige „Gerade nicht erreichbar“.

## Was die App zusätzlich kann
- **Kein Zertifikat in Android installieren:** Beim Verbinden lädt die App die Zertifizierungsstelle
  des eigenen Pi (`/zertifikat/pinas.crt`), zeigt den Fingerabdruck (SHA-1, wie auf der Seite
  „Sichere Verbindung“) und vertraut danach nur Zertifikaten, die genau diese CA unterschrieben hat
  (`NasTrust.kt`).
- **PiNAS im WLAN finden:** Samba meldet sich per mDNS (`_smb._tcp`); die App prüft jedes gefundene
  Gerät und listet nur echte PiNAS auf (`NasDiscovery.kt`).
- **Downloads** landen im Ordner „Download“ – mit derselben Anmeldung wie die Weboberfläche
  (`NasDownloader.kt`). **Hochladen** öffnet die Dateiauswahl von Android, auch mehrere Dateien.
- Zurück-Taste, Herunterziehen zum Aktualisieren, automatisches Neuverbinden, wenn das WLAN wiederkommt.
- Die Weboberfläche erkennt die App am User-Agent `PiNAS-App/<version>`; unter „Konto“ gibt es dann
  „Anderes PiNAS verbinden“ (Link `pinasapp://verbindung`).

## Bauen
Auf GitHub: Actions → **„PiNAS-App bauen“** → Run workflow → Artifact `PiNAS-App-<version>` laden,
entpacken, die APK aufs Handy kopieren und öffnen („Installation aus unbekannten Quellen“ erlauben).

Lokal: Ordner `android/` in Android Studio öffnen.

Version: `versionCode` und `versionName` in `app/build.gradle.kts`.
