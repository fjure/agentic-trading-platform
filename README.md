# Agentic Trading Lab

Lokale Testplattform mit Angular-Material-Dashboard, Control API, Worker, Execution und unabhängigem Watchdog. PostgreSQL hält Handelszustand, Audit und Ereignisse dauerhaft fest.

**Der Einstieg verwendet FakeBroker und MockDecisionEngine und startet mit `DISABLED` / `PAUSE_NEW`.** Dafür brauchst du keine Broker-, Jev- oder GitHub-Zugangsdaten. Die Anmeldung am Dashboard erteilt keine Handelsfreigabe. LIVE ist technisch ausgeschlossen; ein tatsächlicher Paperlauf ist ein separater menschlicher Ablauf.

## Schnellstart auf deinem bereits eingerichteten Rechner

1. Docker Desktop öffnen und warten, bis die Linux-Engine läuft.
2. PowerShell im Repository öffnen:

   ```powershell
   Set-Location 'C:\Users\Domenik\ai-projects\agentic-trading-platform'
   .\scripts\start-local-lab.ps1 -SkipBuild
   ```

   `-SkipBuild` verwendet die bereits gebauten Images und das vorhandene Dashboard. **Nach Änderungen am Anwendungscode** stattdessen vollständig bauen und starten:

   ```powershell
   .\scripts\start-local-lab.ps1
   ```

3. [http://127.0.0.1:8000](http://127.0.0.1:8000) im Browser öffnen. Die genaue Adresse verwenden, auch beim späteren Anmelden.
4. Den folgenden Befehl **selbst in einer privaten interaktiven PowerShell** ausführen:

   ```powershell
   docker compose -f infra/compose/compose.yaml --profile lab exec control-api python -m services.runtime activate
   ```

5. Den dort angezeigten Einmalcode in das Feld **„Aktivierungscode“** der lokalen Seite eingeben und den Anmeldebutton betätigen. Er gilt fünf Minuten und kann einmal verwendet werden. Nicht in Chat, URL, Logs oder Screenshots übernehmen. Bei Ablauf oder Neustart der Control API einen neuen Code anfordern.

Der Launcher erzeugt beim Start keinen Code. Ein leerer Codebereich im Terminal ist deshalb zunächst normal. Diese Aktivierung öffnet ausschließlich die lokale Browsersitzung.

## Was du beim ersten Test erwarten solltest

| Anzeige | Erwartetes Verhalten im normalen Fake-Lab |
|---|---|
| Modus / neue Einstiege | `DISABLED`, Einstiege pausiert |
| Portfolio | Technische Simulationsstartbasis von 10.000 USD; kein bestätigter Brokerkontostand |
| Reconciliation / Marktdaten | `PENDING`, veraltete oder fehlende Evidenz kann sichtbar sein |
| Historien / Orders | Bei frischer Einrichtung leer; keine automatische Befüllung mit Demotrades |
| Fortsetzen | Kann gesperrt sein oder vom Backend abgelehnt werden, solange Pflichtnachweise fehlen |

Das Lab erfindet keine aktuellen Preise oder Börsensitzungen. Gesunde Container und ein erreichbares Dashboard allein erlauben keine Orders. Der vollständige simulierte Trade wird über den isolierten Akzeptanztest unten geprüft.

### Kurzer manueller Dashboardtest

1. Nach der Anmeldung Übersicht, Modus, Portfolio und Dienststatus ansehen.
2. Die Historienansichten für Markt, Entscheidungen, Vorschläge, Risiko, Orders und Audit öffnen. Leere Ansichten beziehungsweise „nicht verfügbar“ sind ohne gespeicherte Evidenz zulässig.
3. **„Neue Einstiege pausieren“** anklicken, den Dialog bestätigen und prüfen, dass der Zustand weiterhin pausiert ist. Eine erfolgreiche Kontrollaktion soll in der Audithistorie nachvollziehbar sein.
4. Seite neu laden: Die Anzeige muss erneut aus dem Backend geladen werden. Nach einem API-Neustart kann eine erneute Anmeldung nötig sein.

Den **Kill-Switch** nur gezielt testen: Er setzt `HALTED` und wird weder durch einen Neustart noch durch „Fortsetzen“ automatisch aufgehoben. Für den ersten Oberflächentest genügt Pause. Details: [Dashboard bedienen](docs/runbooks/dashboard.md) und [Incidentablauf](docs/runbooks/incident.md).

## Erstinstallation auf einem neuen Windows-Rechner

Voraussetzungen: Windows x64, PowerShell 7, Git, lokal verfügbares Python **3.13.x** und Docker Desktop mit laufender Linux-Engine sowie Docker Compose v2. Für den ersten Aufbau werden Internetzugriff und freie lokale Ports benötigt; das Dashboard verwendet `127.0.0.1:8000`. Die Skripte installieren keine Python-Laufzeit.

Im Repository-Root ausführen:

```powershell
python --version
docker compose version
.\scripts\bootstrap-contract-tools.ps1 -SkipPython
.\scripts\bootstrap-application.ps1
.\scripts\start-local-lab.ps1
```

`python --version` muss eine 3.13-Version anzeigen. Bei mehreren Installationen kannst du dem Anwendungsskript den passenden Interpreter über `-Python 'C:\Pfad\zu\Python313\python.exe'` übergeben.

Die Contract-Tools richten die gepinnte lokale Node-/Java-/Generator-Toolchain unter `.tooling/` ein; ein globales Node oder Java ist für diese Schritte nicht nötig. Das Anwendungsskript installiert die gesperrten Pythonabhängigkeiten und erzeugt ein privates lokales Datenbanksecret ohne Credentialeingabe. Der Launcher baut Dashboard und Images, migriert vorwärts und startet die Dienste. Private Dateien unter `.tooling/application/` nicht auslesen, teilen oder einchecken.

Die automatischen Bootstrap- und Launcher-Skripte sind derzeit für Windows ausgelegt. Linux wird als Containerlaufzeit verwendet; eine native Linux-/macOS-Installation ist hier nicht als geprüft beschrieben.

## Status prüfen und stoppen

```powershell
# Container und Healthzustände
docker compose -f infra/compose/compose.yaml --profile lab ps

# Prozessliveness und Datenbankbereitschaft, ohne Dashboardanmeldung
Invoke-RestMethod 'http://127.0.0.1:8000/health'
Invoke-RestMethod 'http://127.0.0.1:8000/ready'

# Unabhängige Pause, auch bei gestörter Browseroberfläche
docker compose -f infra/compose/compose.yaml --profile lab exec -T control-api python -m services.execution.control_socket --socket /run/control/control.sock pause

# Vier Anwendungsdienste und lokalen Browser-Tunnel stoppen
.\scripts\start-local-lab.ps1 -Stop
```

Der Stoppschritt erhält PostgreSQL und sämtliche Volumes. Falls du auch PostgreSQL anhalten möchtest:

```powershell
docker compose -f infra/compose/compose.yaml --profile lab stop postgres
```

Ein späteres `start-local-lab.ps1 -SkipBuild` startet die vorhandene Umgebung wieder. Für einen normalen Stopp keine Volumes löschen oder Docker zurücksetzen. Bestehende Ledgerdaten und Secrets werden durch den Launcher nicht automatisch ersetzt.

## Automatisierte Tests

Alle folgenden Befehle werden im Repository-Root ausgeführt und verwenden synthetische Daten beziehungsweise lokale Komponenten. Sie senden keine echten Brokerorders.

### Schnelle lokale Prüfungen

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit tests/contracts tests/property tests/paper tests/integration/test_replay.py tests/integration/test_jev_shadow.py tests/recovery/test_alpaca_fixtures.py -q
```

Diese Gruppe benötigt die eingerichtete Pythonumgebung, aber keinen externen Anbieter. Der Linux-tmpfs-Fall wird auf Windows übersprungen; dafür existiert eine separate Containerprüfung.

### Vollständiger Fake-Trade bis zur HTTP-Supervisoransicht

Einmalig das Smoke-Testimage bauen und dessen PostgreSQL-Abhängigkeit starten:

```powershell
docker compose -f infra/compose/compose.yaml --profile fake run --build --rm build-smoke
.\.venv\Scripts\python.exe -m pytest tests/integration/test_acceptance.py -q
```

Der Akzeptanztest prüft Markt → Mockentscheidung → Vorschlag → Riskreservation → Fake-Order → Teil-/Endfill → Portfolio/Audit → Recovery → authentifizierte HTTPansichten → Pause. Er legt eine isolierte temporäre Datenbank an und räumt sie anschließend auf. **Seine Trades erscheinen nicht im normalen Dashboard-Lab.** Die Abnahme ruft die tatsächliche API über einen ASGI-Testclient auf; die Browserprüfung folgt separat.

Optionaler größerer Fake-/Recoverydurchlauf mit derselben Testinfrastruktur:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_vertical_fake.py tests/recovery/test_vertical_failures.py -q
```

### Oberfläche im Browser automatisiert prüfen

```powershell
.\scripts\test-dashboard.ps1 -InstallBrowser
```

Der erste Aufruf installiert das gepinnte Chromium-Testpaket. Danach genügt `test-dashboard.ps1`; mit vorhandenem aktuellen Build auch `-SkipBuild`. Die synthetische Browserfixture verwendet `127.0.0.1:4200`; dieser Port muss frei sein. Das normale Dashboard auf Port 8000 bleibt eine andere Instanz.

### Verträge und generierten Client prüfen

```powershell
.\.venv\Scripts\python.exe scripts/validate-contracts.py
.\scripts\check-angular-client.ps1
```

Die Clientprüfung generiert zweimal temporär, vergleicht mit dem vorhandenen Angularclient und kompiliert. Generierte Anwendungsdateien nicht manuell ändern. Der datierte [Abnahmebericht](docs/evidence/ptv-23-final-acceptance.md) enthält die bereits ausgeführten Ergebnisse und Prüfgrenzen; er ersetzt keinen eigenen neuen Testlauf.

## Häufige Startprobleme

| Problem | Nächster Schritt |
|---|---|
| Docker nicht erreichbar | Docker Desktop öffnen und auf laufende Linux-Engine warten; danach erneut starten. |
| `Verified Node22.23.3 missing` | `bootstrap-contract-tools.ps1 -SkipPython` ausführen. |
| Datenbanksecret fehlt | `bootstrap-application.ps1` ausführen; die Datei nicht selbst anzeigen oder kopieren. |
| Image oder Dashboard fehlt / Oberfläche veraltet | `start-local-lab.ps1` ohne `-SkipBuild` ausführen. |
| Seite nicht erreichbar | Containerstatus und `/health` prüfen. Port 8000 auf Konflikt prüfen und erneut über den Launcher starten; dieser verwaltet den lokalen Tunnel. |
| Anmeldung abgelehnt | Exakt `http://127.0.0.1:8000` verwenden, frischen Code selbst im privaten Terminal anfordern; keine umgeleiteten Eingaben oder `-T` beim Aktivierungsbefehl. |
| Fortsetzen verweigert / `PENDING` | Im unveränderten Fake-Lab erwartbar: aktuelle Markt-/Broker-/Recoverybelege fehlen. Für den funktionalen Handelsdurchstich den isolierten Akzeptanztest verwenden. |
| Launcher verweigert vorhandene Paperkonfiguration | Er überschreibt bewusst keine Anbieter-/Paperkonfiguration. Den separaten Paperablauf und dessen Vorbedingungen prüfen. |

Fehlerberichte auf Schritte, sichtbaren Fehlercode und redigierte Statusangaben begrenzen. Keine Aktivierungscodes, privaten Dateien oder Anbieterpayloads weitergeben.

## Weiterführende Dokumentation

- [Lokaler Betrieb und Kontrollpfade](docs/runbooks/local-operation.md)
- [Dashboard und Verbindungsverhalten](docs/runbooks/dashboard.md)
- [Störungen und Kill-Switch](docs/runbooks/incident.md), [Recovery und Reconciliation](docs/runbooks/recovery.md)
- [Separater menschlicher Paper-Smoke](docs/runbooks/paper-smoke.md) – ausdrücklich außerhalb dieses Schnellstarts; kein realer Paperlauf wurde durch die Entwicklungsagenten ausgeführt.
- [Gesamtabnahme](docs/evidence/ptv-23-final-acceptance.md), [aktueller Entwicklungsstand](docs/development/codex-tooling.md)
- [OpenSpec-Change](openspec/changes/paper-trading-vertical-slice/), [kanonischer HTTP-Vertrag](contracts/http/control-api.openapi.yaml)

Die Anwendung besteht ihre lokale Abnahme mit 25 abgeschlossenen Implementierungstasks. Main-Spec-Sync und Archivierung sind weiterhin gesonderte Schritte. Für OpenSpec ausschließlich `scripts/openspec.ps1` verwenden. Hinweise zu Compose-Profilen und Stop/Run-Semantik: [offizielle Docker-Compose-Dokumentation](https://docs.docker.com/compose/).
