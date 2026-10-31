#!/bin/bash
# PiNAS auf einem bereits laufenden Raspberry Pi OS installieren oder aktualisieren.
# (Alternative zum fertigen SD-Karten-Image, siehe README.)
#   sudo ./deploy/install.sh
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Bitte mit sudo ausführen: sudo $0" >&2; exit 1; }

bash "$(dirname "$0")/setup-system.sh"

systemctl daemon-reload
systemctl restart smbd pinas
systemctl restart wsdd 2>/dev/null || true

# shellcheck disable=SC1091
. /etc/default/pinas
IP="$(hostname -I | awk '{print $1}')"
echo
echo "Fertig! Weboberfläche: http://$IP:$PINAS_PORT  (oder http://$(hostname).local:$PINAS_PORT)"
echo "Beim ersten Aufruf wirst du durch die Einrichtung geführt (Admin-Konto, Festplatte)."
