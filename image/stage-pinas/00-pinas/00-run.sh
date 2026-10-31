#!/bin/bash -e
# Läuft auf dem Build-Rechner: PiNAS-Quellen ins Image kopieren.
# (build.sh legt sie vorher unter files/pi-nas ab.)

install -d "${ROOTFS_DIR}/opt/pinas-src"
cp -a files/pi-nas/. "${ROOTFS_DIR}/opt/pinas-src/"
