#!/bin/bash
# Baut das PiNAS-SD-Karten-Image mit pi-gen (dem offiziellen Raspberry-Pi-OS-Baukasten).
# Läuft auf Linux mit Docker (auch WSL2) oder nativ auf einem Raspberry Pi:
#   bash image/build.sh                 # mit Docker
#   PIGEN_NATIVE=1 bash image/build.sh  # ohne Docker (z. B. auf einem Pi, braucht sudo)
# Ergebnis: image/.pi-gen/deploy/*.img.xz
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${PIGEN_DIR:-$ROOT/image/.pi-gen}"
BRANCH="${PIGEN_BRANCH:-arm64}"   # arm64 = 64-Bit-Raspberry-Pi-OS

if [[ ! -d "$WORK/.git" ]]; then
  git clone --depth 1 --branch "$BRANCH" https://github.com/RPi-Distro/pi-gen.git "$WORK"
fi

# Eigene Stage samt aktuellem PiNAS-Code einsetzen
rm -rf "$WORK/stage-pinas"
cp -r "$ROOT/image/stage-pinas" "$WORK/stage-pinas"
DEST="$WORK/stage-pinas/00-pinas/files/pi-nas"
mkdir -p "$DEST"
cp -r "$ROOT/pinas" "$ROOT/deploy" "$DEST/"
find "$DEST" -name '__pycache__' -prune -exec rm -rf {} +
chmod +x "$WORK/stage-pinas/prerun.sh" "$WORK/stage-pinas/00-pinas/00-run.sh"

# Nur das PiNAS-Image exportieren, nicht zusätzlich das normale Lite-Image
touch "$WORK/stage2/SKIP_IMAGES"
cp "$ROOT/image/config" "$WORK/config"
# Zufälliges Einmal-Passwort (wird nirgends gespeichert und im Image gesperrt, siehe config)
echo "FIRST_USER_PASS=\"$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')\"" >> "$WORK/config"

cd "$WORK"
rm -rf deploy
if [[ "${PIGEN_NATIVE:-0}" == "1" ]]; then
  sudo ./build.sh
else
  docker rm -v pigen_work >/dev/null 2>&1 || true
  ./build-docker.sh
fi

echo
echo "Fertiges Image:"
ls -lh "$WORK"/deploy/*.img.xz
