# Libre Stage Score Desktop-Spoke

Dieser eigenständige Branch enthält den Desktop-Wrapper und die Build-Dateien.
Backend und Frontend liegen auf [`main`](https://github.com/calphiko/libre-stage-score/tree/main)
und werden für jeden Desktop-Build von dort bezogen. Der Branch ist nicht für einen
Merge nach `main` vorgesehen.

Die Desktop-App in `desktop/` verwendet Rust und Tauri für das Fenster und startet das
mit PyInstaller gebündelte Python-Backend lokal. Das gebaute Frontend und die API laufen
gemeinsam auf `127.0.0.1`; die Synchronisation arbeitet nur, während die App läuft.
Die App startet keine extern erreichbare API. Der Hub muss für den Spoke erreichbar sein.

### Bauen

Benötigt werden Git, Python 3.11.4 oder neuer, `uv`, Node.js/npm, Rust und die
[Tauri-Systemvoraussetzungen](https://v2.tauri.app/start/prerequisites/) für die
jeweilige Plattform. Insbesondere benötigt Linux WebKitGTK und Windows WebView2.
Mit [Task](https://taskfile.dev/) auf dem jeweiligen Zielsystem und für dessen
CPU-Architektur separat bauen:

```sh
task desktop:linux
task desktop:windows
task desktop:mac
```

Jeder dieser Befehle installiert die Desktop-Build-Abhängigkeiten und prüft das
Host-Betriebssystem. Cross-Builds werden nicht unterstützt. Alternativ ohne Task
`npm ci --prefix desktop` und anschließend `npm --prefix desktop run build` ausführen.

Vor jedem Build oder `npm --prefix desktop run dev` wird `origin/main` abgerufen.
Backend und Frontend werden gemeinsam aus genau diesem Commit in einem isolierten
Build-Verzeichnis gebaut; lokale Änderungen im Arbeitsverzeichnis werden nicht
übernommen. Der verwendete Commit steht in `desktop/target/main-build.json`. Der
Build benötigt daher Netzwerkzugriff auf GitHub und die Paketregistries. Änderungen
an `main` gelangen beim **nächsten Build**, nicht automatisch in bereits installierte
Desktop-Apps.

Die installierbaren Pakete liegen unter `desktop/target/release/bundle/`. Für einen
schnellen lokalen Build ohne Installer beispielsweise
`task desktop:mac -- --debug --no-bundle` verwenden. Die Erzeugung signierter und unter macOS notariell
beglaubigter Pakete erfordert zusätzliche plattformspezifische Signierung; die obigen
Befehle richten diese nicht ein.

Die PDF-Seitenvorschau benötigt weiterhin `pdftoppm` im Suchpfad des Backend-Prozesses;
optionale OCR benötigt zusätzlich `tesseract`. Diese Programme werden nicht mitgeliefert.
Unter macOS ergänzt die App für das Backend die üblichen Homebrew-Verzeichnisse
`/opt/homebrew/bin` und `/usr/local/bin`, ohne den vorhandenen Suchpfad zu ersetzen.
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
`SECRET_KEY`, lokaler SQLite-Datenbank und aktiviertem Sync. Ohne Snapshot wird eine
fehlende Datenbank mit Demo-Daten und bekannten Demo-Zugangsdaten angelegt; diese sind
nur für lokale Tests geeignet. Für die Übernahme eines bestehenden Standorts ist das
folgende Snapshot-Onboarding vorgesehen. `.env`, Datenbank, PDF-Ablage und Snapshots enthalten
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
Wiederherstellung den Snapshot-Cursor wie in der
[Onboarding-Dokumentation auf `main`](https://github.com/calphiko/libre-stage-score/blob/main/README.md#standort-onboarding-und-wiederherstellung)
beschrieben am Hub bestätigen.
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
