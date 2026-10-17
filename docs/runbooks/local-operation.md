# Lokaler Betrieb

## Dauerhafte lokale Dienste (PTV-21)

`./scripts/start-local-lab.ps1` baut die gepinnten Images und das Angular-Dashboard, migriert die separate lokale Datenbank `lab_runtime` und startet Execution, Worker, Control-API und Watchdog. Default ist Fake/Mock mit DISABLED/PAUSE_NEW. Die technische Startbasis von10000USD ist ausdrücklich STALE/PENDING; es entstehen dadurch keine aktuellen Broker-, Session- oder Marktnachweise. Bestehende Datenbanken/Volumes und vorhandene Rollensecrets werden weder gelöscht noch gedreht. `-SkipBuild` verwendet ausschließlich bereits gebaute lokale Images; nach Quelländerungen zuerst vollständig bauen. `-ProvisionOnly` startet keine Laufzeitrollen.

Der Launcher prüft und begrenzt Windows-Datei-ACLs vor privaten Schreibvorgängen. Der einmalige Provisionierungscontainer besitzt ausschließlich zusätzlich CHOWN/FOWNER für die vier exakten erzeugten Dateien; ihre Linux-Besitzrechte sind10001:10001/0600. Dienste selbst bleiben non-root, read-only, cap_drop ALL und erhalten genau ihren eigenen NOINHERIT-Login. Der Adminzugang wird ausschließlich einmalig an die Provisionierung gemountet. Private Dateien unter `.tooling/application/` niemals auslesen, kopieren oder ins Ticket übernehmen.

Das Dashboard liegt auf `http://127.0.0.1:8000`. `/health` prüft Liveness, `/ready` die Datenbank des Kontrollpfads; beide erlauben keine Orders. Docker Desktop veröffentlicht bei ausschließlich internen Netzen hier keinen Hostport. Der Launcher verwendet deshalb bei Bedarf einen festen lokalen STDIO-Tunnel über `docker compose exec -T control-api`; pro Verbindung ist ausschließlich das eigene Container-Loopbackfrontend erreichbar. Die vier Dienstnetze bleiben intern. Maximal16 Verbindungen,60s ohne Aktivität, keine HTTP-/Cookie-/Code-Logs. Start/Stop prüfen den eigenen Helfer anhand PID, Pythonpfad und Startzeit. `./scripts/start-local-lab.ps1 -Stop` stoppt genau diesen Helfer und die vier Rollen, erhält PostgreSQL und alle Volumes. Ein erneuter Start ersetzt den eigenen Helfer vor der Docker-Portreservierung.

Ein Operator fordert einen fünf Minuten gültigen Einmalcode ausschließlich in seiner privaten interaktiven Konsole an:

```powershell
docker compose -f infra/compose/compose.yaml --profile lab exec control-api python -m services.runtime activate
```

Der Agent führt diesen Aufruf nicht aus. Kein Code entsteht automatisch beim Start. Der Code gehört nur in das lokale Aktivierungsformular, niemals in Chat, URL, Log oder Screenshot. Eine Sitzung aktiviert keinen Handelsmodus.

Der unabhängige Kontrollsocket befindet sich im **Control-API-Container**, fest unter `/run/control/control.sock`:

```powershell
docker compose -f infra/compose/compose.yaml --profile lab exec -T control-api python -m services.execution.control_socket --socket /run/control/control.sock pause
docker compose -f infra/compose/compose.yaml --profile lab exec -T control-api python -m services.execution.control_socket --socket /run/control/control.sock kill
```

Resume und activate-paper bleiben ausdrücklich angeforderte Recoveryaufträge mit aktueller Policyversion, vollständigen Executionprüfungen und explizitem Paperflag. Das Fake-Lab erfindet keine Session oder Preise und gibt dadurch keinen Trade frei. HALTED wird nicht automatisch aufgehoben.

Redigierte lokale OTel-/Health-Evidenz liegt je Dienst im flüchtigen `/tmp/runtime-telemetry.json` (0600). Sie enthält feste Komponenten/Reasoncodes, Zahlen, UTC und maximal256 Spans; keine Nutzpayloads oder Anbieterexporter. `checkedAt` und UNKNOWN/DEGRADED immer beachten. SSElag misst Eventzeit bis zur serverseitigen Ausgabe, keine Browserbestätigung. Metriken sind keine Risk-Freigabe.

`infra/compose/compose.paper.yaml` ist eine getrennte, ausschließlich manuell gewählte Erweiterung: Alpaca nur Execution, Jev nur Worker, feste getrennte HTTPS-Proxys. Sie startet nicht durch dieses Runbook oder die normale CI. Kein echtes Credential wurde für PTV-21 verwendet. Der menschliche Paper-Smoke und seine ausdrücklichen Vorbedingungen gehören PTV-22.

Apply ist freigegeben. Der lokale Fake-Durchstich verbindet Marktnormalisierung, Mock-Entscheidung, Planner, deterministisches Risk-Gate, Execution, Fill-Reconciliation und persistierte Portfolio-/Audit-/Outboxevidenz. Er ist als isolierter Integrationstest ausführbar; dauerhafte Dienste und externe Paperadapter benötigen ihre gesonderten Tasks und Prüfungen. Produktions- und LIVE-Endpunkte sind ausgeschlossen. Maßgeblich sind `REQ-RSK-001..010`, `REQ-RUN-001..007`, `REQ-CTL-001..007` und Design D-01/D-04/D-06/D-08.

## Vollständiger lokaler Fake-Durchstich (PTV-13)

Im Repository-Root mit der gesperrten lokalen Python-Umgebung, laufendem PostgreSQL im internen Compose-Netz und dem bereits gebauten Smoke-Testimage ausführen:

```powershell
.venv/Scripts/python.exe -m pytest tests/integration/test_vertical_fake.py tests/recovery/test_vertical_failures.py -q
```

Bei fehlender Infrastruktur zuerst die folgende Buildprüfung durchführen. Die Testfixture erzeugt pro Fall eine eigene UUID-Datenbank und einen temporären Login, migriert bis HEAD und verwendet tatsächliche Worker-, Execution- und Control-Rollen. Der Testcontainer ist read-only, ohne Capabilities und ohne Secretmounts. Das synthetische Testpasswort bleibt ausschließlich in Memory/STDIN; keine Credentialdatei auslesen. Datenbank und Login werden anschließend entfernt; vorhandene Entwicklungsdaten und Volumes bleiben erhalten.

Die Eingabekerzen und eine ausdrücklich versionierte synthetische Testsession mit UTC-Zeitstempeln sind relativ zur DB-Uhr aufgebaut. Dadurch funktioniert der lokale Nachweis auch außerhalb der echten XNYS-Handelszeit. Diese Testsession bestätigt weder aktuelle Börsenöffnungszeiten noch produktive Kalender-/Marktfähigkeiten. Geld und Mengen bleiben Decimal. Mock und Fake sind prozesslokal und besitzen keine Netzwerk- oder Credentialabhängigkeit.

Der Test prüft die gespeicherte Identitäts-/Tracekette statt nur eines Endwerts. Nach Prozessabbruch vor beziehungsweise nach Submit bleibt der dauerhafte Auftrag sichtbar, der neue Execution-Owner startet pausiert und ein Wiederholungsversuch erzeugt keine Doppelorder. UNKNOWN hält die Reservierung und bleibt im Ledger erkennbar; Reconciliation plus ausdrücklich angeforderte und vollständig geprüfte Recovery ist die Voraussetzung für eine Freigabe. In diesen Tests bleibt das Fake-Brokerobjekt als simulierte Brokerwelt über den Execution-Neustart erhalten. Das ist ein deterministischer Prozessabbruchnachweis, kein OS-Kill-, DB-Restore- oder externer Broker-Neustartnachweis.

Bei einem fehlgeschlagenen Test das vollständige Paket nicht als freigegeben behandeln. Keine Testfixture gegen einen externen Broker richten. Redigierte Prüfevidenz: `docs/evidence/ptv-13-vertical-fake.md`; Kontroll-/Incidentablauf: `incident.md`; wirtschaftliche Reconciliation: `recovery.md`.

## Ausführbare Buildprüfung (PTV-01)

Windows: `./scripts/bootstrap-application.ps1` installiert mit uv 0.13.0 ausschließlich `uv.lock` und erzeugt bei Bedarf eine private, ignorierte lokale Datenbank-Secretdatei. Keine Credentialeingabe erforderlich; Datei nicht auslesen oder teilen. Python muss lokal vorhanden sein; das Skript lädt keinen Interpreter. Hostprüfung erfolgte mit 3.13.6, Containerprüfung mit dem Ziel 3.13.16.

`docker compose -f infra/compose/compose.yaml --profile fake config --quiet` prüft die Konfiguration. `docker compose -f infra/compose/compose.yaml --profile fake run --build --rm build-smoke` startet eine lokale PostgreSQL-18.6-Datenbank und prüft das eingefrorene Python-Paket im nichtprivilegierten Container. Der Prozess endet nach der Buildprüfung und erhält keine Secrets. Compose deklariert ausschließlich 127.0.0.1:55432 und ein internes Netz. Die aktuelle Docker-Desktop-Engine meldet jedoch PublishedPort 0; der Host-TCP-Check scheitert. PostgreSQL ist über den lokalen Containersocket und das interne Containernetz erreichbar. Integrationstests verwenden deshalb einen isolierten Testcontainer, keinen geöffneten externen Netzwerkpfad. Das Datenbanksecret bleibt ausschließlich im PostgreSQL-Service. Adminrolle `lab_migration` ist vorläufig nur für die lokale Infrastruktur; getrennte Laufzeit-/Auditrollen werden in PTV-04 eingerichtet. `docker compose -f infra/compose/compose.yaml --profile fake down` stoppt diese Buildinfrastruktur und erhält das Datenvolume. Keine Volume-Löschung im normalen Runbook.

Frontend: im Repository-Root den isolierten Nodepfad `.tooling/contracts/node/node-v22.23.3-win-x64` vorübergehend vor PATH setzen, dann in `apps/dashboard` `npm ci --no-audit --no-fund` und `npm run build` ausführen. Ein globales Node 22.18 genügt Angular 22 nicht. `npm audit` und `.venv/Scripts/pip-audit.exe --local --skip-editable` prüfen bekannte Schwachstellen; `.venv/Scripts/python.exe scripts/scan-source-secrets.py` prüft Credentialmuster in Git-relevanten Textdateien und gibt keine Werte aus. Der Musterscan ersetzt keine vollständige Secretgarantie.

Compose 5.3.1 wurde lokal erfolgreich geprüft; die zuvor geplante 5.5.0 ist keine verdeckte Voraussetzung dieses Profils. Beide verwendeten offiziellen Images sind im Dockerfile/Compose mit Manifest-Digests fixiert. Bootstrap ist keine Health-/Trading-Freigabe. Die folgenden Schritte bleiben bis zu ihren Implementierungstasks geplant.

## Vor dem Start

1. Operator prüft lokale Herkunft, gepinnte Versionen, Secret-/Dependency-Scans, verifizierte Paper-Host-Allowlist und minimale Prozessrechte. Broker-Credentials werden nur Execution als restriktiv lesbare Secretdatei bereitgestellt; keine Eingabe in Terminalhistorie, Browser, Prompt oder Logs.
2. Konfiguration wird validiert: Modus `DISABLED`, ein Paper-Konto, kleines bestätigtes Instrumentuniversum, 15-Minuten-Kerzen, versionierte Simulationslimits und UTC/Decimal-Konvention. Jede `LIVE`-Option oder unbekannte Brokerfähigkeit verhindert Startfreigabe.
3. Migration und Backup werden mit separater Rolle geprüft. Nach Restore gilt `DISABLED`/`PAUSE_NEW`; ein Restore übernimmt nie eine frühere Orderfreigabe.

## Start und Aktivierung

1. Lokale Dienste werden erst nach Implementierung mit einem geprüften Compose-Profil gestartet. **Geplanter Befehlsplatzhalter, derzeit nicht ausführbar:** `docker compose -f infra/compose/compose.yaml up -d`. Der tatsächliche Dateiname und die Dienste müssen im späteren Apply festgelegt und gegen das Runbook geprüft werden.
2. Operator liest Health/Readiness, Worker-/Execution-/Watchdog-Heartbeats, Datenalter, Outboxlag, offene Orders, UNKNOWN-Anzahl und Reconciliationstatus. `ready` allein ist keine Handelsfreigabe.
3. Execution gleicht Account, offene und abgeschlossene Orders, Fills, Positionen und Reservierungen über stabile interne und brokerseitige IDs ab. Jede Abweichung hält `PAUSE_NEW`.
4. Nur nach ausdrücklicher lokaler Operatoraktion und erfolgreicher Health-, Capability-, Policy- und Reconciliationprüfung darf PAPER aktiviert werden. Der implementierte private Linux-Kontrollsocket akzeptiert `activate-paper --policy-version <VERSION>` beziehungsweise `resume --policy-version <VERSION>` über `python -m services.execution.control_socket --socket <PFAD>`; genaue Syntax und Rollenverdrahtung siehe `incident.md`. REQUESTED bestätigt nur die Anfrage; ausschließlich Execution bestätigt nach Prüfung die Freigabe. Der dauerhafte Dienststart und die HTTP-/Dashboardkontrollen folgen ihren eigenen Tasks.

## Laufende Kontrolle

- Supervisor betrachtet Modus/SafetyState, Paper-Konto, Markt- und Jev-Frische, Budgetreservierungen, Orders/Teilfüllungen, UNKNOWN, Jobleases, Outbox, API-Kosten und Trace-Audit. Anzeigegrün ohne frischen Backend-Snapshot gilt als unbekannt.
- Aktienentscheidungen benötigen abgeschlossene Bars innerhalb der tatsächlichen Session einschließlich Feiertag, Halbtag und DST. Krypto 24/7 nur bei bestätigter Instrumentfähigkeit. Stale oder fehlende Daten sperren neue Einstiege.
- `PAUSE_NEW` verhindert neue Einstiege, lässt Reconciliation und geprüfte risikoabbauende Aktionen weiterlaufen. Der private Linux-Kontrollpfad `python -m services.execution.control_socket --socket <PFAD> pause` und Watchdog funktionieren unabhängig von Jev und Dashboard; Details in `incident.md`.
- `HALTED` stoppt neue Submits und kann best-effort Cancel auslösen. Es garantiert nicht, dass bereits in-flight Brokerrequests oder Fills rückgängig werden. Flatten ist eine eigene geprüfte Aktion, kein automatischer Bestandteil des Kill-Switches.

## Geordneter Stopp

1. `PAUSE_NEW` setzen und bestätigten persistenten SafetyState samt Trace/Audit prüfen.
2. Offene/UNKNOWN-Orders und Fills reconciliieren; unbekannte Reservierungen nicht freigeben. Bei unklarer Brokerlage Incident-Runbook verwenden.
3. Outbox- und Jobzustand sichern, dann Dienste stoppen. **Geplanter Befehlsplatzhalter:** `docker compose -f infra/compose/compose.yaml down` erst nach 1 und 2; er ist derzeit nicht ausführbar.
4. Beim nächsten Start bleibt `DISABLED` beziehungsweise `PAUSE_NEW`, bis die komplette Startprüfung erneut erfolgreich ist.

## Evidenz

Für jeden Aktivierungs-, Pausen- und Stopplauf werden UTC-Zeit, Operator-ID ohne personenbezogene Details, Konfig-/Policyversion, Health-/Reconciliation-Ergebnis, offene UNKNOWN-Zahl, Trace-ID und redigierter Auditverweis aufbewahrt. Keine Tokens, Secretinhalte oder rohen Anbieter-Payloads dokumentieren.


## Lokaler HTTP-Kontrolladapter (PTV-18)

`services.control_api.app.create_app(auth=LocalAuth(...), backend=ControlBackend(...))` verbindet den explizit injizierten lokalen Sitzungsschutz mit einer `atl_control_api`-Ledgerverbindung für genau das konfigurierte Konto. Der unkonfigurierte Modulstandard hat keine Datenbankverbindung und verweigert Kontrollwirkungen. Er ist kein fertig verdrahteter Deployment-Launcher; Prozessstart und Secretmounts werden mit PTV-21 integriert. Nur auf der exakt konfigurierten Loopback-IP und dem Port binden; Proxyheader und fremde Socketpeers werden abgelehnt. Keine Wildcard-CORS oder Vertrauensübernahme aus Forwarded-Headern.

Der vertrauenswürdige Launcher ruft `issue_activation_code()` auf und zeigt dessen Rückgabewert ausschließlich privat dem lokalen Operator. Nicht in URLs, Logs, Dateien oder Beispiele kopieren. Das spätere Same-Origin-Formular sendet JSON an `/api/v1/session`, mit frischem Idempotency-Key; der Code verfällt nach fünf Minuten und kann einmal verbraucht werden. Es werden ausschließlich lokale Sitzungstokens ausgetauscht, keine Broker-/Jev-Schlüssel. Die HttpOnly-/SameSite=Strict-Session und der Aktivierungs-Replaybeleg gelten 24 Stunden; HTTPS setzt zusätzlich Secure. Ein Retry erneuert die serverseitige Frist nicht. Neustart verwirft die lokale Sessionauthority und verlangt eine neue private Aktivierung. Die produktive Formularoberfläche folgt in PTV-20.

Jeder schreibende Request verlangt exakten Origin, JSON und Idempotency-Key; Session und Hostprüfung gelten auch für lesende Routen. Modus/Pause/Kill/Risk-Policy laufen ausschließlich über den persistenten Controlport. Eine Risk-Policy wird mit dem starken ETag aus GET und If-Match ersetzt: fehlend 428, veraltet 412. Der persistierte Replaybeleg wird vor der aktuellen Versionsprüfung gelesen; gleicher Schlüssel mit anderer Nutzlast ergibt 409. Replaybelege bleiben zunächst dauerhaft erhalten. Keine Keys blind neu erzeugen, wenn ein Ergebnis unklar ist.

PAPER/Resume bestätigt keine ungeprüfte Freigabe: Die API legt nur den bestehenden befristeten Recoveryauftrag an und meldet bis zur Execution-Bestätigung HEALTH_CHECK_FAILED/503. Der wirksame Zustand wird anschließend über GET `/api/v1/mode` gelesen; ein identischer Retry gibt weiterhin das gespeicherte Ergebnis zurück. HALTED wird über die API nicht aufgehoben. Fehlt die erste Risk-Policy, bleiben Modusänderungen gesperrt, Notfall-Pause/Kill dagegen möglich; deren Ereignisse kennzeichnen die unbekannte Policyversion ausdrücklich mit null. Snapshot, Historienpagination und SSE werden mit PTV-19 implementiert.

Offline-Prüfungen: `python -m pytest tests/contracts/test_control_api.py tests/security/test_control_auth.py` und gegen den vorhandenen Fake-PostgreSQL-Service `python -m pytest tests/integration/test_control_http.py`. Alle Tests verwenden lokale ASGI-Transporte oder isolierte synthetische PostgreSQL-Datenbanken. Keine Brokerorders oder externen Anbieterrequests werden dafür ausgeführt.


## Snapshot, Historienseiten und SSE (PTV-19)

Der Launcher injiziert zusätzlich `SnapshotReader(control_ledger, account_id)` als `supervisor` und `EventFeed(control_ledger, account_id)` als `events` in `create_app`. Alle 18 kanonischen Operationen sind damit transportseitig vorhanden. API und Reader verlangen weiterhin die lokale Session und das einzige konfigurierte Konto. Kostenbeobachtungen benötigen eine ausdrücklich bekannte `cost_currency`; unbekannte Währungen und fehlende Startbelege erzeugen 503 statt erfundener Angaben. Neue generische Worker-Laufdatensätze erhalten Rolle WORKER und den ersten dauerhaften Anlagezeitpunkt; alte unbelegte Werte bleiben unbekannt. Die Snapshot-Kontoanzeige ist ausschließlich der lokale Alias.

Nach Anmeldung zuerst GET `/api/v1/snapshot` lesen und den zurückgegebenen opaken Cursor für GET `/api/v1/events?cursor=...` verwenden. Browser-EventSource nutzt dieselbe HttpOnly-Session; bei Wiederaufnahme hat Last-Event-ID Vorrang vor dem Querycursor. Nicht selbst Cursorwerte konstruieren. Jeder Datenframe besitzt id, versionierten event-Typ und JSON-data; Cursor sind je Konto und Ressource begrenzt und sieben Tage gültig. Wiederzugestellte Event-IDs deduplizieren und Aggregatversionen berücksichtigen. Ein bereits im Snapshot enthaltener, erst später publizierter Zustand darf nochmals als Event erscheinen.

Ein unbekannter/abgelaufener Cursor oder fehlender Abschnitt ergibt vor Streamöffnung 410/CURSOR_EXPIRED. Eine erst später bemerkte Lücke sendet reset-required und schließt den Stream: Cache verwerfen und neuen Snapshot laden. Der Backendstream sendet während Leerlauf spätestens im normalen 15-Sekunden-Takt einen Kommentarheartbeat. Nach 45 Sekunden ohne Heartbeat muss der spätere Client STALE/disconnected anzeigen. Datenbankausfall oder abgelaufene Session schließt den Stream; der nächste Verbindungsversuch zeigt 503 beziehungsweise 401. Kein weiterlaufendes Anzeigegrün annehmen.

Die acht Historienlisten liefern items/nextCursor und maximal200 Datensätze pro Seite. Cursor werden nicht zwischen Ressourcen, Konten oder Auditfiltern übertragen. Seiten halten die anfängliche Erfassungsmitgliedschaft fest; aktuelle mutable DTO-Werte dürfen sich ändern. Keine langfristige Datenbanktransaktion bleibt für die Browserverbindung offen. Historische Records ohne belegte Konto-/Laufzuordnung werden nicht nachträglich einem Konto zugeschrieben. Ledger und Publishlog werden nicht zur Cursorbereinigung gelöscht; die Aufbewahrungsgrenze ist zunächst logisch.

Prüfung: `python -m pytest tests/integration/test_sse_cursor.py` verwendet ausschließlich isolierte synthetische PostgreSQL-Datenbanken. Framing/Abbruch: `python -m pytest tests/contracts/test_supervisor_transport.py`. Der Deployment-Launcher und die Benutzeroberfläche folgen den nächsten Tasks; dieser Nachweis erlaubt keine echten Brokerorders.
