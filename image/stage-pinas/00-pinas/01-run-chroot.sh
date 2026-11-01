#!/bin/bash -e
# Läuft im Image (chroot): PiNAS installieren, Dienste für den ersten Start aktivieren.

bash /opt/pinas-src/deploy/setup-system.sh
rm -rf /opt/pinas-src

# Kein blauer Einrichtungs-Assistent beim ersten Start: Systembenutzer sperren und
# den Assistenten (userconfig) abschalten, falls er trotzdem installiert ist.
passwd -l piadmin
systemctl disable userconfig.service 2>/dev/null || true
systemctl mask userconfig.service 2>/dev/null || true
rm -f /etc/systemd/system/getty@tty1.service.d/autologin.conf

apt-get clean
