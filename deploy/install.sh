#!/bin/bash
# PiNAS auf einem bereits laufenden Raspberry Pi OS installieren oder aktualisieren.
# (Alternative zum fertigen SD-Karten-Image, siehe README.)
#   sudo ./deploy/install.sh
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Bitte mit sudo ausführen: sudo $0" >&2; exit 1; }

bash "$(dirname "$0")/setup-system.sh"

systemctl daemon-reload
/usr/local/sbin/pinas-cert
/usr/local/sbin/pinas-issue
systemctl restart smbd pinas nginx
systemctl restart wsdd 2>/dev/null || true

IP="$(hostname -I | awk '{print $1}')"
echo
echo "Fertig! Weboberfläche: https://$(hostname).local  (oder https://$IP)"
echo "Damit der Browser nicht warnt: dort unter „Sichere Verbindung“ das PiNAS-Zertifikat installieren."
echo "Beim ersten Aufruf wirst du durch die Einrichtung geführt (Admin-Konto, Festplatte)."
