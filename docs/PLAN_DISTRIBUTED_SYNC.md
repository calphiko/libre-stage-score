# Plan: Umstellung auf verteilte Instanzen mit Hintergrund-Synchronisation

## Ziel

Mehrere vollständige libre-stage-score-Instanzen (API + DB + PDF-Speicher) bilden einen Verbund. Jede Instanz arbeitet
auch ohne Internet vollständig lokal und gleicht sich im Hintergrund bandbreitenschonend mit den anderen ab.

## Ausgangslage (Ist-Zustand)

- Eine FastAPI-App (`backend/main.py`, `backend/routers/admin.py`), eine DB (`DATABASE_URL`, standardmäßig SQLite).
- Lokale Integer-Autoincrement-IDs in allen Tabellen (`backend/models.py`), keine Änderungs-Metadaten.
- Löschungen sind harte Deletes (Songs, Scores, Gruppen, Instrumente …).
- PDFs liegen lokal unter `backend/storage/scores` und `backend/storage/upload`; `Score.file_hash` existiert bereits.
- Auth: JWT in Cookies, `refresh_tokens`, `token_blacklist`, lokal in der DB.
- Frontend (`frontend/src/lib/api.js`) kennt genau eine `API_URL`.
- Schema-Änderungen: Alembic, zusätzlich Ad-hoc-Anpassungen in `_ensure_backward_compatible_schema()` (main.py).

## Architekturentscheidungen (Phase 0)

| Thema | Entscheidung |
|---|---|
| Topologie | Hub-and-Spoke; Pilot mit zwei bis drei Instanzen; Peer-to-Peer später möglich halten |
| Modell | Offline-first: jede Instanz schreibt lokal, Sync asynchron |
| Konflikte | HLC/LWW pro Zeile; konkurrierende Berechtigungsentzüge und Tombstones gewinnen; Konflikte sichtbar machen |
| Transport | Authentifizierte Peer-Verbindungen über TLS; Batching und Cursor-Details in Phase 3 festlegen |
| Dateien | SHA-256; Metadaten sofort und Score-PDFs bei Bedarf; Original-Upload-Dateien zunächst nur am Hub |
| Auth zwischen Instanzen | Pro Instanz eigene Peer-Identität; lokale Sessions/Tokens nicht replizieren |
| Benutzer/Rollen | Offline-Änderungen an Benutzern, Rollen und Passwörtern erlaubt; Änderungen priorisiert synchronisieren; kein Widerrufsfrist-SLA während Netztrennung |

Die Entscheidungen und Datenklassifikation aus Phase 0 sind in
[ADR: Verteilte Synchronisation – Phase 0](ADR_DISTRIBUTED_SYNC_PHASE_0.md) festgehalten. Für den Pilotbetrieb
bleiben Standortzahl, dauerhafte Erreichbarkeit, PDF-Volumen und Datenschutzanforderungen zu validieren.

## Phase 0 – Anforderungen und Datenklassifikation

**Status: abgeschlossen.** Entscheidungen und Regeln: [ADR: Verteilte Synchronisation – Phase 0](ADR_DISTRIBUTED_SYNC_PHASE_0.md).

1. [x] Tabellen klassifizieren:
   - **Repliziert:** users, groups, user_groups, instruments, user_instruments, group_instruments, songs, scores,
     collections, song_collections, ensembles, kind_of_song, difficulty_level, song_rythm, song_storage_physical,
     document_access, song_uploads.
   - **Lokal:** refresh_tokens, token_blacklist, used_password_reset_tokens sowie alle Sync-Zustandstabellen.
2. [x] Konfliktregel und Löschsemantik pro Tabelle festlegen.
3. [x] Topologie und Berechtigungen für Offline-Änderungen entscheiden.
4. [x] Ergebnis als ADR in `docs/` festhalten.

## Phase 1 – Datenmodell für Replikation

**Status: abgeschlossen.** Details: [ADR: Verteilte Synchronisation – Phase 1](ADR_DISTRIBUTED_SYNC_PHASE_1.md).

1. [x] Replikationsmetadaten (UUIDv7, HLC, Node, Version, Tombstone-Feld) für alle replizierten Tabellen ergänzen;
   lokale Integer-IDs und Datenbank-Fremdschlüssel bleiben erhalten.
2. [x] UUIDs als Identität in Sync-Payloads festlegen; Fremdschlüssel dort über UUIDs referenzieren und beim
   Import auf lokale IDs auflösen.
3. [x] Einzigartige Namen und Kollisionen regeln: lokale Constraints bleiben erhalten; gleichnamige Datensätze
   verschiedener UUIDs werden als sichtbare Konflikte behandelt, nicht automatisch zusammengeführt.
4. [x] Lokale Tabellen für Node-Identität, Peers, Cursor, Outbox, Konflikte und Blob-Status ergänzen.
5. [x] Alembic-Migration mit UUID-/Versions-Backfill und initialer Node-Identität ergänzen. Die
   Ad-hoc-Schemalogik in `_ensure_backward_compatible_schema()` wurde nicht erweitert.

## Phase 2 – Änderungserfassung (Outbox)

**Status: abgeschlossen.** Änderungserfassung und Tombstone-Verhalten sind in
[ADR: Verteilte Synchronisation – Phase 2](ADR_DISTRIBUTED_SYNC_PHASE_2.md) beschrieben.

1. [x] SQLAlchemy-Session-Events (`before_flush`/`after_flush`) schreiben Änderungen an replizierten ORM-Entitäten
   transaktional ins `change_log`; Fremdschlüssel werden in UUID-Referenzen umgewandelt.
2. [x] Destruktive Admin-Routen erzeugen Tombstones einschließlich betroffener replizierter Zuordnungen; normale
   ORM-Lesezugriffe blenden Tombstones aus. Die bestehende Benutzer-Deaktivierung bleibt als Statusänderung
   reaktivierbar.
3. [x] Mit `changes_from_peer()` markierte, eingehende Änderungen erzeugen kein Echo-Outbox-Event.
4. [x] `compact_change_log()` entfernt alte Einträge begrenzt und nur bis zum bestätigten Cursor jedes aktiven Peers.

## Phase 3 – Sync-Protokoll und Endpunkte

**Status: abgeschlossen.** Protokoll und Betriebsdetails: [ADR: Verteilte Synchronisation – Phase 3](ADR_DISTRIBUTED_SYNC_PHASE_3.md).
Router und Import-Service liegen in `backend/sync/`; die Endpunkte sind nur für authentifizierte Peers verfügbar:

- [x] `GET /sync/hello` – Node-ID, Protokoll- und Schema-Version, Cursor-Stand.
- [x] `GET /sync/changes?since=<cursor>&limit=N` – Änderungsbatch (gzip, paginiert).
- [x] `POST /sync/changes` – Änderungen empfangen; idempotent, Antwort mit bestätigtem Cursor.
- [x] `GET /sync/blobs/{sha256}` – PDF-Download mit Range-Unterstützung.
- [x] `PUT /sync/blobs/{sha256}` – PDF-Upload (resumable, Hash-Prüfung).
- [x] `GET /sync/status` – Rückstand und Konfliktanzahl.

Regeln:
- [x] Anwendung in Abhängigkeitsreihenfolge (Eltern vor Kindern); fehlende Eltern blockieren den Batch.
- [x] Konfliktauflösung gemäß Phase 0 (LWW per HLC, Tombstone-vs-Update, Namenskollisionen).
- [x] Protokoll-/Schema-Version in `hello`; inkompatible Versionsheader werden mit HTTP 426 abgewiesen.
- [x] Konfigurierbare Batchgröße und Bandbreitenlimit (`SYNC_MAX_BATCH`, `SYNC_BANDWIDTH_LIMIT_BYTES_PER_SECOND`).

## Phase 4 – Hintergrund-Worker

**Status: umgesetzt.** Betrieb und Konfiguration: [ADR: Verteilte Synchronisation – Phase 4](ADR_DISTRIBUTED_SYNC_PHASE_4.md).

1. [x] Worker als asyncio-Task im App-Lifespan; bei App-Shutdown sauber abbrechen.
2. [x] Je Peer: hello/Kompatibilität prüfen, Änderungen pushen und pullen sowie referenzierte Score-PDFs
   abgleichen.
3. [x] Exponentielles Backoff mit Jitter pro Peer; Fortsetzung an den lokal bestätigten Cursorpositionen.
4. [x] Konfiguration über `.env`: `NODE_ID`, `SYNC_PEERS`, `SYNC_INTERVAL_SECONDS`, `SYNC_MAX_BATCH`,
   `SYNC_BANDWIDTH_LIMIT`, `SYNC_ENABLED`; `.env.example` erweitern.
5. [x] SQLite-Verbindungen mit WAL und Busy-Timeout betreiben.

## Phase 5 – PDF-/Dateisynchronisation

**Status: umgesetzt.** Strategie und Aufbewahrung: [ADR: Verteilte Synchronisation – Phase 5](ADR_DISTRIBUTED_SYNC_PHASE_5.md).

1. [x] Dateipfade vom Inhalt entkoppeln: Score-PDFs werden zusätzlich unter
   `blobs/<sha256[:2]>/<sha256>.pdf` abgelegt; `Score.storage_path` bleibt kompatibel und synchronisierte Scores
   benötigen einen SHA-256-Hash.
2. [x] `GET /scores/{id}/document` lädt fehlende Dateien nach erfolgreicher Zugriffsprüfung gezielt von Peers oder
   meldet sie als lokal nicht verfügbar.
3. [x] Pro Instanz konfigurierbare Strategie: `all` (alles vorab), `metadata` (nur Metadaten), `on_access` (bei
   Zugriff) und `authorized` (vorab nur für Gruppen-/Instrumentenzugriffe).
4. [x] Chunk-Übertragung mit Hash-Prüfung, Wiederaufnahme und Deduplizierung.
5. [x] Originalarchiv (`song_uploads`, `backend/storage/upload`) getrennt behandeln: Metadaten werden repliziert,
   Originaldateien selbst nicht über den Score-PDF-Blob-Sync übertragen.
6. [x] Verwaiste Blobs nach konfigurierbarer Tombstone-Haltefrist bereinigen.

## Phase 6 – Authentifizierung und Sicherheit

**Status: umgesetzt.** Betrieb und Schlüsselrotation: [ADR: Verteilte Synchronisation – Phase 6](ADR_DISTRIBUTED_SYNC_PHASE_6.md).

1. [x] Peer-Authentifizierung über kurzlebige signierte JWTs mit paarweisem Schlüssel, überlappender Rotation und
   Widerruf über den Peer-Status.
2. [x] Sync-Endpunkte verlangen Peer-Authentifizierung und TLS (HTTP nur auf Loopback); sie sind nicht in
   `CSRF_EXEMPT_PATHS` aufgenommen und akzeptieren keine Benutzer-Cookies als Peer-Anmeldung.
3. [x] Passwort-Hashes, Rollenänderungen und Deaktivierungen werden wie in Phase 0 repliziert; restriktive
   Änderungen gewinnen Konflikte. Widerrufe auf getrennten Instanzen greifen erst nach deren Synchronisation.
4. [x] `SECRET_KEY` ist instanzlokal; Benutzer-Tokens werden nicht repliziert und standardmäßig nicht zwischen
   Instanzen geteilt.
5. [x] Sync-Anfragen haben Größen- und Rate-Limits; ausgehende Peer-Verbindungen verwenden TLS.
6. [x] Eingehende Änderungen werden transaktional und ohne Nutzdaten im Audit-Log festgehalten.

## Phase 7 – Frontend

**Status: umgesetzt.** Details: [ADR: Verteilte Synchronisation – Phase 7](ADR_DISTRIBUTED_SYNC_PHASE_7.md).

1. [x] `API_URL` pro Deployment konfigurierbar; Frontend spricht typischerweise mit der lokalen Instanz.
2. [x] Sync-Statusanzeige: letzter Abgleich, ausstehende Änderungen, Peer erreichbar/nicht erreichbar.
3. [x] Admin-Seite für Peers (hinzufügen/entfernen/pausieren), Konfliktliste mit Auflösen-Aktion, Blob-Status
   („PDF noch nicht lokal verfügbar“).
4. [x] Bessere Fehlermeldungen in `fetchWithAuth` bei nicht erreichbarer Instanz.

## Phase 8 – Betrieb und Deployment

**Status: umgesetzt.** Betriebsabläufe und Grenzen: [ADR: Verteilte Synchronisation – Phase 8](ADR_DISTRIBUTED_SYNC_PHASE_8.md).

1. [x] Pro Standort: Backend, Frontend, Datenbank und Speicher; Taskfile-Targets
   (`task sync-worker`, `task node:init`) ergänzen.
2. [x] Snapshot-Onboarding eines neuen Standorts: konsistenter SQLite-Export und verifizierter Blob-Index vom Hub,
   lokale Identität/Sync-Zustände zurücksetzen und ab Snapshot-Cursor inkrementell abgleichen.
3. [x] Backup/Restore pro Instanz dokumentieren; Restore auf leerer Datenbank als neues Replikat erzwingen, damit
   alte Node-Identitäten und Cursor nicht inkonsistent weiterverwendet werden.
4. [x] Monitoring: `/health` um ausstehende Änderungen und Alter des letzten Peer-Abgleichs erweitern.
5. [x] README und `docs/` um Architektur, Betrieb und Fehlersuche erweitern.

## Phase 9 – Tests und Abnahme

**Status: automatisierte Verifikation umgesetzt; Pilotabnahme offen.** Details und Pilot-Runbook:
[ADR: Verteilte Synchronisation – Phase 9](ADR_DISTRIBUTED_SYNC_PHASE_9.md).

1. [x] Unit-Tests für Outbox, HLC, Konfliktregeln, Idempotenz, Tombstones und UUID-Auflösung.
2. [x] Integrationstests mit zwei Instanzen und getrennten SQLite-Testdatenbanken:
   Offline-Änderungen beidseitig, konkurrierende Bearbeitung/Löschung, Batch-Rollback und Wiederholung,
   unterbrochener Blob-Transfer mit Wiederaufnahme, doppelte Zustellung, vertauschte Abhängigkeiten,
   Namenskollisionen, inkompatible Protokollversion und Dokumentberechtigungen nach Sync.
3. [x] Gedrosselter PDF-Lasttest mit konfigurierbarem Umfang (`task test:load`).
4. [ ] Pilotbetrieb mit zwei realen Standorten, Messprotokoll und Freigabe, bevor weitere hinzukommen.

## Meilensteine

1. **M1:** Phase 0 + 1 (Entscheidungen, UUIDs, Replikationsmetadaten, Migrationen).
2. **M2:** Phase 2 + 3 (Outbox, Sync-API) – Metadatensync zwischen zwei Instanzen.
3. **M3:** Phase 4 (Hintergrund-Worker, Backoff, Konfiguration).
4. **M4:** Phase 5 + 6 (PDF-Sync, Sicherheit).
5. **M5:** Phase 7 + 8 (UI, Betrieb) und Pilot.
6. **M6:** Härtung, optional Peer-to-Peer und PostgreSQL.

## Risiken

- Größter Aufwand: Soft-Delete und Outbox quer durch `admin.py`.
- `unique`-Namen führen zu Merge-Konflikten.
- Verzögerter Widerruf von Benutzerrechten bei getrennten Instanzen.
- SQLite bei parallelen Schreibzugriffen (Worker + API).
- Großes PDF-Volumen bei geringer Bandbreite – „bei Bedarf laden“ priorisieren.

## Nicht-Ziele (vorerst)

- Echtzeit-Zusammenarbeit/Live-Updates zwischen Standorten.
- Automatische Auflösung semantischer Konflikte über LWW hinaus.
- Aufteilung in fachliche Microservices.
