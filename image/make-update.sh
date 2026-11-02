#!/bin/bash
# Baut das Update-Paket für das Update-Center: pinas-update-<version>.tar.gz
# Inhalt: pinas/ und deploy/ sowie manifest.json mit Version und SHA-256 jeder Datei.
#   bash image/make-update.sh [ZIELDATEI]
set -euo pipefail

cd "$(dirname "$0")/.."
VERSION="$(tr -d '[:space:]' < pinas/VERSION)"
OUT="${1:-pinas-update-$VERSION.tar.gz}"

# Syntaxfehler in den Skripten schon hier abfangen, nicht erst beim Installieren auf dem Pi
for f in deploy/pinas-helper deploy/install.sh deploy/setup-system.sh; do bash -n "$f"; done

python3 - "$VERSION" "$OUT" <<'EOF'
import hashlib, io, json, sys, tarfile, time
from pathlib import Path

version, out = sys.argv[1], sys.argv[2]
built = int(time.time())
files = sorted(p for d in ("pinas", "deploy") for p in Path(d).rglob("*")
               if p.is_file() and "__pycache__" not in p.parts)
manifest = {"format": 1, "name": "PiNAS", "version": version, "built": built,
            "files": {p.as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}

def info(name, size, mode=0o644):
    ti = tarfile.TarInfo(name)
    ti.size, ti.mode, ti.mtime, ti.uname, ti.gname = size, mode, built, "root", "root"
    return ti

with tarfile.open(out, "w:gz") as tar:
    data = json.dumps(manifest, indent=1).encode()
    tar.addfile(info("manifest.json", len(data)), io.BytesIO(data))
    for p in files:
        body = p.read_bytes()
        tar.addfile(info(p.as_posix(), len(body)), io.BytesIO(body))
print(f"{out}: Version {version}, {len(files)} Dateien")
EOF
