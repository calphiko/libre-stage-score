# Plan: Songtabelle auf AG Grid Community umstellen

## Ziel und Ausgangslage

Die Songtabelle in `frontend/src/routes/songs/+page.svelte` soll durch eine wiederverwendbare Komponente in
`frontend/src/lib` ersetzt werden. Die freie Version von AG Grid ist ausreichend; Enterprise-Funktionen dürfen nicht
vorausgesetzt werden.

Die bestehende Suche, die Ansicht „Alle Stücke / Nur meine Gruppe“ für Editoren und die Zugriffsregeln bleiben erhalten.
Das Stimmenmodal und die vorhandenen Funktionen zum Anlegen, Bearbeiten und Löschen von Stücken bleiben verfügbar.

## 1. Daten und Gruppenzuordnung

- [x] Die Song-API um die pro Stück vertretenen Gruppen des angemeldeten Users erweitern: Eine Gruppe zählt als
  vertreten, wenn mindestens eine ihrer Instrumentenzuordnungen zu einer Stimme des Stücks passt.
- [x] Gruppeninformationen gesammelt statt mit einer Anfrage pro Tabellenzeile laden und dabei die bestehenden
  Zugriffsregeln und Ansichtsmodi berücksichtigen.
- [x] Die Zuordnung nach Änderungen an Stimmen, Instrumenten oder Gruppen aktualisieren. Die API berechnet sie bei
  jedem Abruf neu; die Stückseite lädt nach ihren eigenen Stimmenänderungen sowie beim erneuten Öffnen neu.

**Annahme:** Eine Gruppe gilt anhand vorhandener Stimmen als vertreten, unabhängig davon, ob die Stimme ein PDF ist.
Das entspricht der aktuellen Ermittlung zugänglicher Stücke; die Stimmenanzeige im Modal filtert zusätzlich auf PDFs.

## 2. Wiederverwendbare Grid-Komponente

- [x] AG Grid Community installieren und eine Svelte-Komponente in `frontend/src/lib` für Daten,
  Spaltendefinitionen, Filter und Aktionen bereitstellen.
- [x] Eine eigene Spaltenauswahl zum Ein- und Ausblenden anbieten, ohne die Enterprise-Spaltenseitenleiste
  vorauszusetzen.
- [x] Die bisherigen Spalten standardmäßig anzeigen: ID, Name, Tonart, Komponist, Arrangement, Sammlungen, Länge
  und Notizen. Dazu kommen standardmäßig **Meine Gruppen** und die Aktionsspalte.
- [x] Alle übrigen Songfelder auswählbar machen, darunter Verlag, Lagerort, Ensemble, Stückart, Schwierigkeitsgrad,
  Rhythmus und die beiden booleschen Felder. Verknüpfungen als lesbare Namen statt IDs darstellen.

## 3. Suche und Filter

- [x] Die vorhandene Suche oberhalb der Tabelle mit ihrem bisherigen Suchumfang erhalten; sie wirkt zusätzlich zu den
  Spaltenfiltern.
- [x] Passende Community-Filter für Text, Zahlen und weitere Datentypen einsetzen.
- [x] Für **Meine Gruppen** einen eigenen Mehrfachauswahlfilter anbieten. Er zeigt die Gruppenmitgliedschaften des
  Users und findet Stücke, in denen mindestens eine ausgewählte Gruppe vertreten ist; Enterprise-Filter werden nicht
  vorausgesetzt.

## 4. Direkte Bearbeitung und Aktionen

- [x] Editoren und Admins können bearbeitbare Songfelder direkt in der Tabelle ändern. Für Metadaten und boolesche
  Werte geeignete Auswahlfelder verwenden; ID und berechnete Gruppenzuordnung bleiben schreibgeschützt.
- [x] Sammlungen über ihren bestehenden separaten API-Endpunkt bearbeiten. Änderungen validieren, serverseitig
  autorisieren und Fehler sichtbar machen; bei Fehlschlag den bisherigen Wert wiederherstellen.
- [x] Pro Stück eine zugängliche Icon-Schaltfläche zum Öffnen des bestehenden Stimmenmodals anbieten. Anlegen,
  Löschen und die bestehenden Modal-Funktionen erhalten; Zellbearbeitung darf das Modal nicht versehentlich öffnen.
- [x] Eine sichere Speicherstrategie für gleichzeitige Änderungen vorsehen: Zelländerungen verwenden einen neuen
  partiellen `PATCH`-Endpunkt statt des vollständigen Song-`PUT`. Die UI verarbeitet Änderungen nacheinander.

## 5. Integration und Absicherung

- [x] Darstellung an das bestehende Theme und kleinere Bildschirme anpassen; Tastaturbedienung und beschriftete
  Schaltflächen berücksichtigen.
- [x] Backend-Tests für Gruppenzuordnung, Berechtigungen und Speicherung ergänzen.
- [x] Frontend-Verhalten für Spaltenauswahl, kombinierte Filter, Rollen, Bearbeitung und Modalöffnung prüfen.
- [x] Svelte-Check und Build ausführen.
