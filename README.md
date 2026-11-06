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

## Betrieb pro Standort

Jeder Standort benötigt eine eigene Backend-Instanz, Datenbank, lokale Dateiablage und
Frontend-Konfiguration. `.env` wird beim Start geladen. Pro Instanz sind mindestens
`SECRET_KEY`, `DATABASE_URL`, `NODE_NAME`, sichere Cookie-Einstellungen und die für
Peer-Verbindungen verwendeten Schlüssel-Umgebungsvariablen zu konfigurieren. Sync-Peers
werden im Admin-Bereich verwaltet; Peer-URLs müssen HTTPS verwenden. HTTP ist nur für
Loopback zulässig.

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
