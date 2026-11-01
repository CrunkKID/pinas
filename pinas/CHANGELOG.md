# Neuigkeiten in PiNAS

Neueste Version oben. Das Update-Center zeigt die Abschnitte an, die neuer sind als die installierte Version.

## 1.2.4
- Nach einer Update-Installation zeigt eine ablaufende Uhr „Neu Start in“ die Sekunden bis zum Neustart – im Update-Center und im Dashboard
- Das Laufwerk, auf dem PiNAS selbst läuft (SD-Karte), ist im Dashboard immer verborgen
- Neues Fenster „⚙ Optionen“ im Dashboard (nur für Administratoren): Dort lässt sich das Systemlaufwerk mit einem Häkchen einblenden
- Von Anfang an gibt es nur ein Laufwerk: „Gemeinsam“
- Das private Laufwerk „Privat“ richtet jeder Benutzer selbst unter „Konto“ ein und kann es dort wieder ausschalten – die Dateien bleiben dabei erhalten
- Benutzer, die schon Dateien in „Privat“ haben, behalten ihr privates Laufwerk automatisch
- „Herunterfahren“, „Neustart“ und „Sicheres Herunterfahren“ stehen jetzt oben rechts neben „Abmelden“ auf jeder Seite (nur für Administratoren); im Dashboard bleibt „Festplatte ausschalten“
- Ein geplantes Herunterfahren wird auf jeder Seite mit Restzeit angezeigt
- PiNAS als App: Auf Handy, Tablet und PC als App installierbar – mit eigenem Symbol, im Vollbild und mit Anleitung unter „Konto“
- Ist das NAS nicht erreichbar, zeigt die App „Gerade nicht erreichbar“ und verbindet sich wieder von selbst

## 1.2.3
- Update-Center: Vor jeder Installation wird geprüft, ob genug Speicherplatz frei ist, ob neue Programmpakete nötig sind und ob dafür Internet da ist – erst dann lässt sich installieren
- Update-Center: Vor der Installation wird angezeigt, wie lange sie ungefähr dauert; PiNAS merkt sich die Dauer jedes Updates und schätzt beim nächsten Mal genauer
- Update-Center: Während der Installation zeigt ein Fortschrittsbalken, wie lange es noch dauert
- Nach einer erfolgreichen Installation startet PiNAS nach 15 Sekunden automatisch neu – mit Countdown; danach lädt die Seite von selbst neu
- Updates laden Programmpakete nur noch aus dem Internet, wenn wirklich etwas fehlt – dadurch schneller und auch ohne Internet möglich
- Benutzer verwalten: Nur der Administrator kann jedem Benutzer einen eigenen Speicherplatz für „Privat“ freigeben (leer = unbegrenzt)
- Speicherplatz: Ist er voll, werden Uploads abgelehnt; das Netzlaufwerk zeigt den persönlichen Platz als Laufwerksgröße an
- Speicherplatz: Jeder Benutzer sieht seinen Platz unter „Dateien“ und „Konto“
- Dashboard: Neue Kachel „Energieverbrauch“ – wie viel Watt der Raspberry Pi, die Festplatte und das PiNAS insgesamt ziehen, dazu kWh und Kosten pro Monat

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
