#!/bin/bash
# Erzeugt den Katalogeintrag für den Raspberry Pi Imager (os_list_pinas.json).
#   bash image/make-os-list.sh <image.img.xz> <Download-URL des Images> <URL des Icons> [Ausgabedatei]
set -euo pipefail

IMG="$1"
URL="$2"
ICON="$3"
OUT="${4:-os_list_pinas.json}"

download_size="$(stat -c %s "$IMG")"
download_sha="$(sha256sum "$IMG" | cut -d' ' -f1)"
extract_size="$(xz -dc "$IMG" | wc -c)"
extract_sha="$(xz -dc "$IMG" | sha256sum | cut -d' ' -f1)"

cat > "$OUT" <<EOF
{
  "os_list": [
    {
      "name": "PiNAS",
      "description": "Raspberry Pi OS Lite (64 Bit) als fertiges NAS: Netzlaufwerk, Weboberfläche, Dashboard",
      "url": "$URL",
      "icon": "$ICON",
      "release_date": "$(date +%Y-%m-%d)",
      "extract_size": $extract_size,
      "extract_sha256": "$extract_sha",
      "image_download_size": $download_size,
      "image_download_sha256": "$download_sha",
      "init_format": "cloudinit-rpi",
      "devices": ["pi5-64bit", "pi4-64bit", "pi5", "pi4"]
    }
  ]
}
EOF
echo "Geschrieben: $OUT"
