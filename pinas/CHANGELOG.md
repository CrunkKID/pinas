# Neuigkeiten in PiNAS

Neueste Version oben. Das Update-Center zeigt die Abschnitte an, die neuer sind als die installierte Version.

## 1.2.2
- Reparatur: Nach dem Update auf 1.2.1 startete die Weboberfläche nicht mehr („502 Bad Gateway“), weil die neuen Programmdateien falsche Zugriffsrechte bekamen – das ist behoben
- Update-Center: Nach jeder Installation wird geprüft, ob die Weboberfläche wirklich startet; wenn nicht, wird automatisch die vorherige Version wiederhergestellt
- Das Protokoll im Update-Center zeigt bei einem Fehler zusätzlich die Fehlermeldungen der Weboberfläche
- Enthält alle Neuerungen aus 1.2.1 (Ein/Aus-Knöpfe, Werkseinstellungen, Betriebszeit)

## 1.2.1
- Im Dashboard oben rechts gibt es jetzt die Knöpfe „Herunterfahren“, „Neu Start“, „Sicheres Herunterfahren“ und „Festplatte Ausschalten“
- Herunterfahren: PiNAS fährt nach Bestätigung herunter
- Neu Start: PiNAS startet neu und ist nach 1–2 Minuten wieder erreichbar
- Sicheres Herunterfahren: Zuerst wird die Festplatte ausgeschaltet, nach 2 Minuten wird PiNAS heruntergefahren – mit Countdown und „Abbrechen“
- Festplatte Ausschalten: Die Festplatte wird sauber abgemeldet und ausgeschaltet und lässt sich mit „Festplatte einschalten“ ohne Neustart wieder einschalten
- Jede Aktion muss vorher bestätigt werden und steht nur Administratoren zur Verfügung
- Neu im Update-Center: „Auf Werkseinstellungen zurücksetzen“ – löscht alle Benutzer, Passwörter und Einstellungen, danach startet die Ersteinrichtung
- Beim Zurücksetzen bleiben die Dateien auf der Festplatte erhalten; optional lässt sich die Festplatte zusätzlich komplett leeren (z. B. vor dem Verkaufen)
- Zum Zurücksetzen müssen das eigene Passwort und das Wort „zurücksetzen“ (bzw. „alles löschen“) eingetippt werden
- Betriebszeit: Die Gesamtbetriebszeit läuft nach Herunterfahren, Neustart, Update und Zurücksetzen auf Werkseinstellungen genau dort weiter, wo sie stand

## 1.2.0
- Update-Center: Updates ohne neues Image über die Weboberfläche einspielen, mit Sicherung und „Zurück zur vorherigen Version“
- Verschlüsselte Verbindung (https) mit eigenem Zertifikat – keine blockierten Downloads mehr
- Dateiansicht wie im Windows-Explorer: acht Ansichten, Sortieren, Rechtsklick-Menü, Vorschaubilder
- Dashboard anpassen: Kacheln ein-/ausblenden und verschieben
- Verständliche Prozessor-Auslastung in Prozent
- Kein Einrichtungs-Assistent mehr am Pi-Bildschirm beim ersten Start

## 1.1.0
- Papierkorb, Energiesparen für Festplatten, Festplatten benennen, NTFS-/exFAT-Platten ohne Formatieren

## 1.0.0
- Erste Version: Netzlaufwerk, Weboberfläche, Dashboard, mehrere Benutzer
