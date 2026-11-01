#!/bin/bash
# Richtet PiNAS im System ein, ohne Dienste zu starten. Läuft sowohl beim Bau des
# SD-Karten-Images (im chroot) als auch über install.sh auf einem laufenden Pi.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen." >&2; exit 1; }

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/pinas
STATE=/var/lib/pinas

# Einstellungen: vorhandene /etc/default/pinas behalten, sonst Standardwerte
PINAS_DATA="${PINAS_DATA:-/srv/nas}"
PINAS_SHARED_MODE="${PINAS_SHARED_MODE:-folder}"
# shellcheck disable=SC1091
[[ -r /etc/default/pinas ]] && . /etc/default/pinas
# PINAS_PORT früherer Versionen fällt weg: Die App lauscht fest auf 127.0.0.1:8081 hinter nginx (https)
cat > /etc/default/pinas <<EOF
# PiNAS-Einstellungen (nach Änderung: sudo systemctl restart pinas)
PINAS_DATA=$PINAS_DATA
# folder = eigene Ordner users/ und shared/, root = „Gemeinsam“ zeigt die ganze Platte
PINAS_SHARED_MODE=$PINAS_SHARED_MODE
EOF
chmod 0644 /etc/default/pinas
DATA="$PINAS_DATA"

echo "==> Pakete"
export DEBIAN_FRONTEND=noninteractive
apt-get update
# ntfs-3g/exfatprogs: Platten vom Windows-PC ohne Formatieren verwenden
apt-get install -y samba python3 python3-flask python3-waitress rsync sudo hdparm sdparm ntfs-3g exfatprogs \
  nginx openssl
# Samba-Modul "recycle" für den Papierkorb beim Netzlaufwerk (je nach Debian-Version schon in samba enthalten)
apt-get install -y samba-vfs-modules || echo "samba-vfs-modules nicht verfügbar – übersprungen"
# Damit das NAS in der Windows-Netzwerkumgebung auftaucht (optional)
apt-get install -y wsdd || echo "wsdd nicht verfügbar – übersprungen"

echo "==> Benutzer und Gruppen"
getent group nasusers >/dev/null || groupadd --system nasusers
id pinas &>/dev/null || useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin pinas
# "video" erlaubt vcgencmd (Unterspannungs-Anzeige im Dashboard)
usermod -aG video pinas

echo "==> Verzeichnisse"
mkdir -p "$STATE"
if [[ "$PINAS_SHARED_MODE" == "root" ]]; then
  NAS_DIRS=("$DATA/.pinas" "$DATA/.pinas/users" "$DATA/.tmp")
else
  NAS_DIRS=("$DATA/users" "$DATA/shared" "$DATA/.tmp")
fi
mkdir -p "${NAS_DIRS[@]}"
# Auf NTFS/exFAT gibt es keine Linux-Rechte – das regeln dort die Mount-Optionen
case "$(findmnt -no FSTYPE -T "$DATA" 2>/dev/null || true)" in
  fuseblk|ntfs*|exfat|vfat) ;;
  *)
    chown pinas:pinas "$DATA" "${NAS_DIRS[@]}"
    chmod 0770 "$DATA" "${NAS_DIRS[@]}"
    ;;
esac
chown -R pinas:pinas "$STATE"
chmod 0700 "$STATE"

echo "==> Anwendung nach $APP"
mkdir -p "$APP"
rm -rf "$APP/venv"  # Altlast früherer Versionen
rsync -a --delete --exclude '__pycache__' "$SRC/pinas" "$APP/"
chown -R root:root "$APP"

echo "==> Hilfsskript und sudo-Regel"
install -m 0755 -o root -g root "$SRC/deploy/pinas-helper" /usr/local/sbin/pinas-helper
echo "pinas ALL=(root) NOPASSWD: /usr/local/sbin/pinas-helper" > /etc/sudoers.d/pinas
chmod 0440 /etc/sudoers.d/pinas
visudo -cf /etc/sudoers.d/pinas >/dev/null

echo "==> Samba"
[[ -f /etc/samba/smb.conf.pinas-backup ]] || cp /etc/samba/smb.conf /etc/samba/smb.conf.pinas-backup
install -D -m 0644 "$SRC/deploy/smb-pinas.conf" /usr/local/share/pinas/smb-pinas.conf.in
grep -q '^include = /etc/samba/pinas.conf' /etc/samba/smb.conf || \
  printf '\n# PiNAS-Freigaben\ninclude = /etc/samba/pinas.conf\n' >> /etc/samba/smb.conf
# Erzeugt /etc/samba/pinas.conf passend zur Ordner-Aufteilung und prüft sie mit testparm
/usr/local/sbin/pinas-helper samba-config

echo "==> Hinweis auf dem Pi-Bildschirm"
# Zeigt über der Anmeldezeile die echte IP-Adresse an, aktualisiert bei jeder Netzwerkänderung
mkdir -p /etc/issue.d
install -m 0755 -o root -g root "$SRC/deploy/pinas-issue" /usr/local/sbin/pinas-issue
install -m 0644 "$SRC/deploy/pinas-issue.service" /etc/systemd/system/pinas-issue.service
if [[ -d /etc/NetworkManager/dispatcher.d ]]; then
  # Neue IP-Adresse: Zertifikat anpassen und Hinweis aktualisieren
  printf '#!/bin/sh\n/usr/local/sbin/pinas-cert\n/usr/local/sbin/pinas-issue\n' > /etc/NetworkManager/dispatcher.d/90-pinas-issue
  chmod 0755 /etc/NetworkManager/dispatcher.d/90-pinas-issue
fi
/usr/local/sbin/pinas-issue
systemctl enable pinas-issue

echo "==> Verschlüsselte Weboberfläche (https)"
# Die Zertifikate entstehen erst auf dem Pi beim ersten Start (pinas-cert.service) –
# jedes Gerät bekommt so seine eigene Zertifizierungsstelle, nichts davon steckt im Image.
install -m 0755 -o root -g root "$SRC/deploy/pinas-cert" /usr/local/sbin/pinas-cert
install -m 0644 "$SRC/deploy/pinas-cert.service" /etc/systemd/system/pinas-cert.service
install -m 0644 "$SRC/deploy/pinas-cert.timer" /etc/systemd/system/pinas-cert.timer
install -m 0644 "$SRC/deploy/nginx-pinas.conf" /etc/nginx/sites-available/pinas
ln -sf /etc/nginx/sites-available/pinas /etc/nginx/sites-enabled/pinas
rm -f /etc/nginx/sites-enabled/default
mkdir -p /etc/systemd/system/nginx.service.d
printf '[Unit]\nWants=pinas-cert.service\nAfter=pinas-cert.service\n' > /etc/systemd/system/nginx.service.d/pinas.conf
systemctl enable pinas-cert.service pinas-cert.timer nginx

echo "==> Energiesparen für die Festplatte"
install -m 0755 -o root -g root "$SRC/deploy/pinas-diskpower" /usr/local/sbin/pinas-diskpower
install -m 0644 "$SRC/deploy/pinas-diskpower.service" /etc/systemd/system/pinas-diskpower.service
install -m 0644 "$SRC/deploy/pinas-diskpower.timer" /etc/systemd/system/pinas-diskpower.timer
systemctl enable pinas-diskpower.timer

echo "==> Systemdienste"
install -m 0644 "$SRC/deploy/pinas.service" /etc/systemd/system/pinas.service
systemctl enable smbd pinas
systemctl enable wsdd 2>/dev/null || true
