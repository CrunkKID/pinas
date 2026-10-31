# PiNAS – schlankes NAS für den Raspberry Pi 5

- **Netzlaufwerk (SMB)** für Windows, macOS, Linux, Android/iOS – Freigaben `Privat` (pro Benutzer) und `Gemeinsam`
- **Web-Oberfläche**: Dateien hochladen (auch per Drag & Drop), herunterladen, Ordner anlegen, umbenennen, löschen
- **Dashboard**: Speicherplatz, CPU-Temperatur, Last, RAM, Unterspannungs-Warnung, Laufwerke, Dienststatus
- **Mehrere Benutzer**: ein Konto gilt für Web und Netzlaufwerk
- **Einrichtung komplett im Browser**: Admin-Konto anlegen und USB-Festplatte einrichten – ohne Terminal

PiNAS gibt es als **fertiges SD-Karten-Image für den Raspberry Pi Imager** (Raspberry Pi OS Lite 64 Bit
+ PiNAS vorinstalliert). Gedacht für das **Heimnetz** – nicht per Portweiterleitung ins Internet stellen.

---

## 1. Image bauen (einmalig)

Ein Raspberry-Pi-Image lässt sich nicht unter Windows bauen. Am einfachsten baut GitHub es kostenlos in der Cloud:

1. Kostenloses Konto auf github.com anlegen, ein **neues öffentliches Repository** erstellen
   (z. B. `pinas`) und den **Inhalt** des Ordners `pi-nas` hochladen (Ordner `.github` nicht vergessen).
2. Im Repository: **Releases → „Create a new release“ → Tag `v1.0`** eintragen → „Publish release“.
   Das startet den Bau (Reiter **Actions**, dauert ca. 1–2 Stunden).
   Der Workflow hängt anschließend `pinas-….img.xz`, `os_list_pinas.json` und das Icon an das Release.

> Ohne Release: Actions → „PiNAS-Image bauen“ → „Run workflow“. Das Image liegt danach als ZIP unter „Artifacts“.
>
> Selbst bauen auf Linux/WSL2 mit Docker oder direkt auf einem Pi: `bash image/build.sh`
> (bzw. `PIGEN_NATIVE=1 bash image/build.sh`). Ergebnis in `image/.pi-gen/deploy/`.

## 2. Mit dem Raspberry Pi Imager installieren

**Variante A – PiNAS erscheint direkt in der Imager-Liste (empfohlen):**
Imager mit eigenem Katalog starten (Windows: Win+R, eine Zeile, `DEIN-NAME` und Repo-Name anpassen):

```
"C:\Program Files\Raspberry Pi Imager\rpi-imager.exe" --repo https://github.com/DEIN-NAME/pinas/releases/latest/download/os_list_pinas.json
```

Dann: Gerät **Raspberry Pi 5** → Betriebssystem **PiNAS** → SD-Karte wählen.

**Variante B – Datei direkt:** `.img.xz` herunterladen, im Imager „Eigenes Image verwenden“
(„Use custom“) wählen.

**In beiden Fällen bei „OS-Anpassungen“ einstellen:** Hostname `pinas`, Benutzername + Passwort
(für SSH/Notfälle), ggf. WLAN, Zeitzone *Europe/Berlin*, Tastatur *de*, SSH aktivieren (optional).
Ein Netzwerkkabel ist für ein NAS aber deutlich schneller als WLAN.

## 3. Erster Start

1. SD-Karte und USB-Festplatte in den Pi, einschalten, ca. 2–3 Minuten warten.
2. Im Browser **http://pinas.local:8080** öffnen (oder `http://<IP-des-Pi>:8080`, die IP steht im Router).
3. **Einrichtungsassistent**: Administrator-Konto anlegen.
4. Seite **Laufwerk**: USB-Platte wählen → *Formatieren & verwenden* (löscht die Platte) oder eine
   vorhandene ext4-Partition mit *Verwenden* übernehmen.
5. Unter **Benutzer** weitere Konten anlegen – fertig.

> Bis das Admin-Konto angelegt ist, kann jeder im Heimnetz die Einrichtung abschließen.
> Mach Schritt 3 also gleich nach dem ersten Start.

**Hinweis zur Stromversorgung:** Nutze das **offizielle 27-W-Netzteil (5 V/5 A)**. Mit schwächeren
Netzteilen begrenzt der Pi 5 den USB-Strom auf 600 mA – 2,5"-Platten brechen dann ab.
Das Dashboard warnt bei Unterspannung.

## Netzlaufwerk verbinden

| System  | Adresse |
|---------|---------|
| Windows | Explorer → „Netzlaufwerk verbinden“ → `\\pinas\Privat` bzw. `\\pinas\Gemeinsam` (oder `\\<IP>\…`) |
| macOS   | Finder → ⌘K → `smb://pinas.local/Privat` |
| Linux   | `smb://pinas.local/Privat` im Dateimanager |

Anmeldung jeweils mit PiNAS-Benutzername und Passwort.

## Alternative: auf bestehendem Raspberry Pi OS installieren

```bash
scp -r pi-nas pi@raspberrypi.local:~/
```
Dann auf dem Pi:
```bash
cd ~/pi-nas && sudo bash deploy/install.sh
```
Dasselbe Skript aktualisiert eine bestehende Installation (Daten und Benutzer bleiben erhalten).

## Aufbau

```
pinas/                 Flask-Anwendung (läuft als Dienstbenutzer „pinas“ über waitress)
  auth.py              Login, Einrichtungsassistent, CSRF-Schutz, Sperre nach 5 Fehlversuchen
  files.py             Dateibrowser (mit Schutz gegen ../-Pfadausbrüche)
  disk.py              Festplatte im Browser einrichten
  dashboard.py         Systemwerte aus /proc, /sys, lsblk, vcgencmd
  users.py             Benutzerlogik (App-DB + Samba-Konto + Ordner synchron)
deploy/
  setup-system.sh      Systemeinrichtung (genutzt vom Image-Bau und von install.sh)
  install.sh           Installation/Update auf laufendem Pi
  pinas-helper         einziges Programm, das „pinas“ per sudo als root ausführen darf
image/
  build.sh             baut das SD-Karten-Image mit pi-gen
  stage-pinas/         pi-gen-Stufe, die PiNAS ins Image installiert
  make-os-list.sh      erzeugt den Imager-Katalog (os_list_pinas.json)
.github/workflows/     automatischer Image-Bau auf GitHub
```

Daten liegen unter `/srv/nas/users/<name>` und `/srv/nas/shared`, Benutzerdatenbank unter
`/var/lib/pinas`, Einstellungen (Datenpfad, Port) in `/etc/default/pinas`.

## Nützliche Befehle (per SSH)

```bash
journalctl -u pinas -f
```
```bash
sudo systemctl restart pinas smbd
```
```bash
cd /opt/pinas && sudo -u pinas python3 -m pinas.cli set-password NAME
```

## Lokal testen (ohne Pi)

```bash
pip install -r requirements.txt
```
```bash
python run_dev.py
```
Dann `http://localhost:8080` öffnen. Im Entwicklungsmodus werden keine System- oder Samba-Konten angelegt.

## Tipps

- **Backup**: Ein NAS ist kein Backup. Wichtige Daten zusätzlich auf eine zweite Platte sichern.
- **Feste IP**: Im Router dem Pi eine feste IP-Adresse zuweisen.
- **Zugriff von unterwegs** (später): lieber per VPN (Tailscale/WireGuard) als per Portfreigabe.
