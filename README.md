# libre-stage-score

libre-stage-score stellt ein lokal arbeitsfähiges Backend und Frontend für die
Notenverwaltung bereit. Mehrere Standorte können Änderungen und Score-PDFs über
authentifizierte Peer-Verbindungen im Hintergrund replizieren. Architekturentscheidungen
und die Umsetzung pro Phase stehen in [`docs/`](docs/PLAN_DISTRIBUTED_SYNC.md).

## Entwicklung

1. `.env.example` nach `.env` kopieren und mindestens `SECRET_KEY` setzen.
2. `task install`
3. `task node:init`
4. `task backend-dev`
5. In einem zweiten Terminal: `task frontend-dev`

`task node:init` führt die Alembic-Migrationen aus und initialisiert die persistierte
Node-Identität. `task db:seed-demo` aktualisiert ebenfalls zuerst das Schema bis zum
aktuellen Alembic-Stand und setzt anschließend den lokalen Demo-Datenbestand (Benutzer,
Gruppen, Instrumente, Stücke und Noten-PDFs) zurück. Der Seed verweigert den Lauf, wenn
Sync-Peers konfiguriert sind. Nur für Entwicklungsdatenbanken verwenden; bei vorhandenen
Nutzdaten stattdessen `task db:migrate` ausführen.

## Desktop-Spoke

Die Desktop-App in `desktop/` verwendet Rust und Tauri für das Fenster und startet das
mit PyInstaller gebündelte Python-Backend lokal. Das gebaute Frontend und die API laufen
gemeinsam auf `127.0.0.1`; die Synchronisation arbeitet nur, während die App läuft.
Die App startet keine extern erreichbare API. Der Hub muss für den Spoke erreichbar sein.

### Bauen

Benötigt werden Python 3.11 oder neuer, `uv`, Node.js/npm, Rust und die
[Tauri-Systemvoraussetzungen](https://v2.tauri.app/start/prerequisites/) für die
jeweilige Plattform. Insbesondere benötigt Linux WebKitGTK und Windows WebView2.
Auf jedem Zielsystem und für dessen CPU-Architektur separat bauen:

```sh
uv sync --extra desktop
npm ci --prefix frontend
npm ci --prefix desktop
uv run --extra desktop python desktop/build_sidecar.py
npm --prefix desktop run build
```

Die installierbaren Pakete liegen unter `desktop/target/release/bundle/`. Für einen
schnellen lokalen Build ohne Installer `npm --prefix desktop run build -- --debug
--no-bundle` verwenden. Das Sidecar nach Änderungen an Backend oder Frontend erneut
bauen. Die Erzeugung signierter und unter macOS notariell beglaubigter Pakete erfordert
zusätzliche plattformspezifische Signierung; die obigen Befehle richten diese nicht ein.

Die PDF-Seitenvorschau benötigt weiterhin `pdftoppm` im Suchpfad des Backend-Prozesses;
optionale OCR benötigt zusätzlich `tesseract`. Diese Programme werden nicht mitgeliefert.
Stimmen-PDFs werden mit dem Standard-PDF-Programm des Betriebssystems geöffnet.
Die App erstellt dazu bei jedem Öffnen eine Kopie unter `opened-pdfs` im
App-Datenverzeichnis. Diese Kopien bleiben auch nach dem Schließen der App bestehen,
können sensible Inhalte enthalten und müssen bei Bedarf manuell gelöscht werden.
Änderungen an einer solchen Kopie verändern nicht das Original in der App.
Die Darstellung der PDF-Vorschau beim Upload hängt von der Webview des Betriebssystems ab.

### Daten und Einrichtung

Standardmäßig liegen `.env`, Datenbank, PDFs und `backend.log` im App-Datenverzeichnis
`org.librestage.score.spoke`: unter macOS in `~/Library/Application Support/`, unter
Windows in `%APPDATA%` und unter Linux in `$XDG_DATA_HOME` bzw. `~/.local/share`.
Mit `LIBRE_STAGE_DESKTOP_DATA_DIR` kann beim Start ein anderes Verzeichnis gewählt
werden. Nur eine App darf gleichzeitig dasselbe Verzeichnis verwenden.

Bei einem ersten Start ohne `.env` erzeugt die App eine Konfiguration mit zufälligem
`SECRET_KEY`, lokaler SQLite-Datenbank und aktiviertem Sync. Das neue Replikat enthält
noch keine Benutzer; für die Übernahme eines bestehenden Standorts ist das folgende
Snapshot-Onboarding vorgesehen. `.env`, Datenbank, PDF-Ablage und Snapshots enthalten
schützenswerte Daten und müssen mit geeigneten Dateiberechtigungen und Backups geschützt
werden. `DATABASE_URL` und die bestehenden Sync-Variablen bleiben konfigurierbar;
relative Datenbankpfade beziehen sich auf das App-Datenverzeichnis.

Für das Onboarding das App-Datenverzeichnis **vor dem ersten App-Start** anlegen,
einen Snapshot vom Hub exportieren und den neuen Peer am Hub registrieren. Die `.env`
im App-Datenverzeichnis beispielsweise wie folgt konfigurieren:

```dotenv
SECRET_KEY=<zufaelliger-instanzlokaler-schluessel-mit-mindestens-32-bytes>
DATABASE_URL=sqlite:///./backend/db/app.db
NODE_ID=<neue-node-uuid>
NODE_NAME=desktop-spoke
SYNC_ENABLED=true
SYNC_PDF_STRATEGY=all
DESKTOP_SNAPSHOT="/absoluter/pfad/zum/hub-snapshot.zip"
DESKTOP_HUB_URL=https://hub.example
DESKTOP_HUB_CREDENTIALS_REFERENCE=HUB_SYNC_TOKEN
HUB_SYNC_TOKEN=<paarweiser-peer-schluessel>
```

Die App stellt den Snapshot nur auf einer noch nicht vorhandenen Datenbank wieder her
und verweigert das Überschreiben einer bestehenden Datenbank. Nach erfolgreicher
Wiederherstellung den Snapshot-Cursor wie unten beschrieben am Hub bestätigen.
Der Marker `.snapshot-restored.json` ermöglicht Neustarts mit unveränderter
Snapshot-Konfiguration; die `DESKTOP_SNAPSHOT`-Einstellungen können danach entfernt
werden. Für einen späteren Restore ein neues, leeres Datenverzeichnis verwenden.
Der lokale HTTP-Port ist bei jedem Start dynamisch; Passwort-Reset-Links brauchen daher
eine separat konfigurierte, für die Empfänger erreichbare `FRONTEND_URL`.

Für eine bestehende **Standalone**-Datenbank ohne Node-Identität `SYNC_ENABLED=false`
setzen und `SYNC_PEERS` leer lassen. Ist die Datenbank bereits migriert und enthält
keine Sync-Peers oder -Cursor, erzeugt die App beim Start eine persistente lokale
Node-Identität. `NODE_ID` kann leer bleiben; falls gesetzt, muss es eine gültige UUID
sein. Ein lesbarer Gerätename gehört in `NODE_NAME`. Vor der Übernahme die Datenbank
und zugehörige WAL-Dateien sichern; sie nicht während eines laufenden Schreibzugriffs
kopieren. Für eine spätere Synchronisation mit einem Hub stattdessen das oben
beschriebene Snapshot-Onboarding in einem neuen Datenverzeichnis durchführen.

## Stücktabelle

Unter „Spalten auswählen“ lassen sich alle Stückfelder ein- und ausblenden. Die bisherige
Suche und die Spaltenfilter wirken gemeinsam; „Nach meinen Gruppen filtern“ zeigt Stücke
mit mindestens einer Stimme für eine der ausgewählten Gruppen. Die Schaltfläche „Stimmen
öffnen“ öffnet das Stimmenmodal.

Editoren und Admins können Zellen per Doppelklick oder Enter bearbeiten. Änderungen werden
nach Abschluss der Eingabe gespeichert; Sammlungen werden über eine Mehrfachauswahl
bearbeitet. Fehler werden angezeigt und der bisherige Wert bleibt erhalten.

## Song-API

`GET /admin/songs` liefert je sichtbarem Stück `my_groups` mit `{id, name, notes}`:
nur eigene Gruppen, deren Instrument mindestens einer aktiven Stimme des Stücks
entspricht, unabhängig von einer PDF-Datei. Gruppen sind nach Name und ID sortiert;
ohne zugehörige Benutzer-ID ist die Liste leer. Sichtbarkeit und `view` bleiben
unverändert. Andere Song-Antworten enthalten `my_groups` als leere Liste.

Editoren und Admins können mit `PATCH /admin/songs/{id}` einzelne Songfelder ändern;
nicht übermittelte Felder bleiben unverändert. Nullable Felder lassen sich mit `null`
leeren, Name und die beiden booleschen Felder nicht. Sammlungen werden weiterhin
separat bearbeitet. Unbekannte Felder sind unzulässig; ungültige Werte liefern 422,
fehlende oder gelöschte Stücke bzw. Metadatenreferenzen 404. Änderungen werden wie
bei PUT über die Sync-Outbox repliziert.

## Betrieb pro Standort

Jeder Standort benötigt eine eigene Backend-Instanz, Datenbank, lokale Dateiablage und
Frontend-Konfiguration. `.env` wird beim Start geladen. Pro Instanz sind mindestens
`SECRET_KEY`, `DATABASE_URL`, `NODE_NAME`, sichere Cookie-Einstellungen und die für
Peer-Verbindungen verwendeten Schlüssel-Umgebungsvariablen zu konfigurieren. Sync-Peers
werden im Admin-Bereich verwaltet; Peer-URLs müssen HTTPS verwenden. HTTP ist nur für
Loopback zulässig.

Mit `LIBRE_STAGE_DATA_ROOT` lässt sich das Basisverzeichnis für die lokale PDF- und
Blob-Ablage festlegen. Ohne diese Einstellung bleibt das Projektverzeichnis die Basis.
Die Datenbank wird weiterhin unabhängig davon über `DATABASE_URL` konfiguriert.

Der Sync-Worker kann entweder als Teil der API (`SYNC_ENABLED=true`) oder separat mit
`task sync-worker` laufen, niemals beides gleichzeitig für dieselbe Instanz. Für den
separaten Prozess bleibt `SYNC_ENABLED` in der API-Konfiguration auf `false`.

`GET /health` prüft die Datenbankverbindung und liefert zusätzlich Sync-Metriken:
`pending_changes` summiert unbestätigte Änderungen aktiver Peers,
`last_sync_age_seconds` zeigt das Alter des ältesten Peer-Erfolgs und
`peers_without_success` zählt Peers ohne erfolgreichen Erstabgleich. Ein erreichbarer
Peer ist keine Voraussetzung für den Liveness-Status.

## Standort-Onboarding und Wiederherstellung

Snapshots lassen sich mit `task snapshot:export OUTPUT=<archiv.zip>` erstellen. Das Archiv
enthält einen konsistenten SQLite-Export, den Änderungs-Cursor und einen geprüften Index
der Score-Blobs, aber keine PDF-Inhalte oder Secrets. Für ein neues Replikat zuerst eine
neue `NODE_ID` festlegen und den Peer am Hub registrieren, dann den Snapshot übertragen
und auf einer leeren Ziel-Datenbank wiederherstellen:

```sh
task node:restore-snapshot SNAPSHOT=hub-snapshot.zip \
  HUB_URL=https://hub.example \
  HUB_CREDENTIALS_REFERENCE=HUB_SYNC_TOKEN
```

`NODE_ID` und `NODE_NAME` müssen vor dem Restore in `.env` gesetzt sein. `HUB_URL` und
`HUB_CREDENTIALS_REFERENCE` sind Task-Variablen; der Referenzwert benennt eine
Umgebungsvariable, die den Schlüssel enthält. Nach erfolgreichem Restore den Cursor aus
`manifest.json` auf dem Hub bestätigen, damit der Hub ab statt vor dem Snapshot pusht:

```sh
task snapshot:ack-bootstrap PEER_ID=<neue-node-id> CURSOR=<change_cursor>
```

Eine Wiederherstellung übernimmt keine alten lokalen Tokens, Outbox-Sequenzen oder
Peer-Cursor. Der reguläre Sync setzt am Snapshot-Cursor fort, während Score-PDFs gemäß
`SYNC_PDF_STRATEGY` nachgeladen werden.

Snapshots enthalten Benutzerdaten einschließlich Passwort-Hashes und müssen geschützt
aufbewahrt und übertragen werden. Score-PDFs, die nur an einem Standort vorliegen, sowie
Original-Uploads müssen separat gesichert werden. Die Betriebsabläufe und Einschränkungen
stehen in [ADR Phase 8](docs/ADR_DISTRIBUTED_SYNC_PHASE_8.md). Test- und Pilotabnahme sind in
[ADR Phase 9](docs/ADR_DISTRIBUTED_SYNC_PHASE_9.md) beschrieben.
