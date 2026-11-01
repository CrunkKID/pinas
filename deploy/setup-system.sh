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
PINAS_PORT="${PINAS_PORT:-8080}"
# shellcheck disable=SC1091
[[ -r /etc/default/pinas ]] && . /etc/default/pinas
cat > /etc/default/pinas <<EOF
# PiNAS-Einstellungen (nach Änderung: sudo systemctl restart pinas)
PINAS_DATA=$PINAS_DATA
PINAS_PORT=$PINAS_PORT
EOF
DATA="$PINAS_DATA"

echo "==> Pakete"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y samba python3 python3-flask python3-waitress rsync sudo
# Damit das NAS in der Windows-Netzwerkumgebung auftaucht (optional)
apt-get install -y wsdd || echo "wsdd nicht verfügbar – übersprungen"

echo "==> Benutzer und Gruppen"
getent group nasusers >/dev/null || groupadd --system nasusers
id pinas &>/dev/null || useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin pinas
# "video" erlaubt vcgencmd (Unterspannungs-Anzeige im Dashboard)
usermod -aG video pinas

echo "==> Verzeichnisse"
mkdir -p "$STATE" "$DATA/users" "$DATA/shared" "$DATA/.tmp"
chown pinas:pinas "$DATA" "$DATA/users" "$DATA/shared" "$DATA/.tmp"
chmod 0770 "$DATA" "$DATA/users" "$DATA/shared" "$DATA/.tmp"
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
sed "s|@DATA_DIR@|$DATA|g" "$SRC/deploy/smb-pinas.conf" > /etc/samba/pinas.conf
grep -q '^include = /etc/samba/pinas.conf' /etc/samba/smb.conf || \
  printf '\n# PiNAS-Freigaben\ninclude = /etc/samba/pinas.conf\n' >> /etc/samba/smb.conf
testparm -s >/dev/null 2>&1 || { echo "Samba-Konfiguration fehlerhaft:"; testparm -s; exit 1; }

echo "==> Hinweis auf dem Pi-Bildschirm"
# Erscheint über der Anmeldezeile am Monitor; \4 setzt agetty durch die aktuelle IP-Adresse
mkdir -p /etc/issue.d
cat > /etc/issue.d/pinas.issue <<EOF

  PiNAS laeuft. Bedienung im Browser eines anderen Geraets:

      http://\\4:$PINAS_PORT      (oder http://\\n.local:$PINAS_PORT)

  Hier am Bildschirm ist keine Anmeldung noetig.

EOF

echo "==> Systemdienste"
install -m 0644 "$SRC/deploy/pinas.service" /etc/systemd/system/pinas.service
systemctl enable smbd pinas
systemctl enable wsdd 2>/dev/null || true
