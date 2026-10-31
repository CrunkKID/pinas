#!/bin/bash -e
# Läuft im Image (chroot): PiNAS installieren, Dienste für den ersten Start aktivieren.

bash /opt/pinas-src/deploy/setup-system.sh
rm -rf /opt/pinas-src
apt-get clean
