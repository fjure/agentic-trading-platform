# Lokaler Betrieb

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
