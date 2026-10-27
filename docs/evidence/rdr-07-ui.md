# RDR-07: Materialoberfläche für Diagnose und Sammlungspläne

Stand: 27.10.2026. Die vorhandene Research-Experimentmaske verwendet für Diagnose
und Simulation denselben geschlossenen BacktestRequest mit Decimalstrings und
UTC-Folds. Diagnose bleibt ein ausdrücklich eigener Auftrag. Die Oberfläche
verwendet ausschließlich den generierten Researchclient und Angular Material;
generierte Dateien wurden nicht bearbeitet.

Ein dauerhafter `readinessAssessment`-URLparameter lädt Status, ursprünglichen
Bericht und gebundene Befundseiten nach einem Reload. Status und die vier
Zulässigkeitswerte werden separat gezeigt. Die fünf Vertrauensdimensionen zeigen
jeweils Status, Methode, Regel, Grund und sichere Referenzen. Ursprünglicher
Snapshot/Hash und aktuelle Widerrufswarnungen bleiben getrennt. Kategorien sind
filterbar; weitere Seiten müssen zum ursprünglichen Bericht und Snapshot passen.
Foldbefunde zeigen Abhilfe und die sechs getrennten Zeitbegriffe. Empfangs- und
Verwendbarkeitsquoten zeigen absolute Zähler, Nenner und unbekannte Fälle; die
drei Latenzpaare zeigen gültige, unbekannte und ungültige Paare. Beobachtungs-,
Strict-PIT- und Experimentfenster werden getrennt mit Grenzen und Versionen
ausgewiesen. Leere Fenster, unbekannte Rechte und Providerverifikation ergeben
keine positive Freigabe.

SPY-/QQQ-Pläne zeigen Wunsch, Version/Generation, Quellengates, Rechte, Fälligkeit,
letzten Versuch, Lücken und offene STARTED-Abrufe. Der feste Zeitplan und seine
Budgets sind lesbar. Aktivierung/Pause senden ausschließlich enabled und
expectedVersion mit Idempotency-Key. Bei 409 wird der Zustand neu gelesen und
eine erneute menschliche Aktion verlangt; die Änderung wird nicht automatisch
wiederholt. Eine verlorene Sitzung entfernt Daten, beendet Polling und sperrt
weitere Aktionen. Diagnose startet weder Simulation noch Sammlung oder Handel.

Reproduzierbare Prüfungen:

- `./scripts/test-readiness-browser.ps1`: Produktionsbuild mit Angular 22.2.2
  bestanden. Bestehende Warnungen: initiales Bundle 924,96kB gegenüber 500kB
  Warnbudget und Supervisor-CSS 7,12kB gegenüber 4kB Warnbudget. Die zugehörige
  Angular-Konfiguration gehört nicht zu dieser Änderung.
- `./scripts/test-readiness-browser.ps1 -SkipBuild`: echte isolierte PostgreSQL
  18.6-/API-/Worker-/Browserabnahme mit Playwright 1.64.1: **1 bestanden in 33,9s**;
  die acht ausdrücklich synthetischen Fälle wurden hierbei übersprungen. Die
  Vorbereitung aktiviert nur QQQ im isolierten Testbestand und führt einen echten
  Collectorpfad über CollectionScheduler/CalendarCollection mit lokalem
  `httpx.MockTransport` aus. Echte DBaufnahme, append-only Kalender-/Messreceipts
  und gesonderte Commitbestätigung bleiben erhalten. Danach wird QQQ pausiert und
  die Quelle wieder DISABLED/UNKNOWN gesetzt. Der Browser bedient SPY über echte
  Session-, API- und SQLports: Diagnose, erfolgreicher dauerhafter Bericht, Reload,
  fünf Trustdimensionen, BLOCKED-Aktivierungswunsch und gespeicherte Pause. Der
  abschließende SQLnachweis prüft genau einen veröffentlichten Bericht, SPY-Pause
  Version/Generation 2, unveränderte globale Gates und keinen zusätzlichen
  Quellenabruf, keine Barwrites, keine Simulation und keine Brokerorder.
- `./scripts/test-readiness-browser.ps1 -SkipBuild -Synthetic`: **8 bestanden in
  10,7s**, echter Integrationstest bewusst übersprungen. Diese Fälle verwenden
  kanonische HTTPfixtures und prüfen alle vier Zulässigkeitswerte, Budgetfehler,
  unbekannte Kalender-/Kapitalmaßnahmenstände und deren Abhilfe, Nenner, Cursor/Filter,
  unveränderten ursprünglichen Bericht plus aktuelle Warnung, Reload, 409 ohne
  Retry, Pause mit bereits begonnenem Abruf und Sitzungsausfall. Sie beanspruchen
  keine echte Provider- oder PostgreSQLabnahme.
- Unabhängige lesende Prüfung der Komponenten und E2E-Fälle durch den
  RDR-05-Verantwortlichen: keine offenen P1/P2-Befunde. Pythonharness Ruff sauber.

Der Harness verwendet ausschließlich eigene flüchtige Container in einem
isolierten internen Netzwerk, ohne Host-DBport, dauerhafte Volumes oder echte
Provider. Nur eigene Ressourcen werden entfernt. Synthetische Zugangsdaten und
Aktivierungscode werden aus Testausgabe redigiert; Screenshots, Traces und Video
sind deaktiviert. Diese Abnahme belegt UI- und Softwareverhalten, keine realen
historischen Nutzungsrechte oder künftige Providerlatenz. Die vollständige
übergreifende Systemabnahme bleibt in RDR-08 dokumentiert.

## Bestandsregression durch den Hauptagenten

Die tatsächliche bestehende Research-Browserprüfung bestand mit **2 Tests in
15,0s**, die Backtest-Browserprüfung mit **4 Tests in 39,0s** und die vollständige
Paper-Browserprüfung mit **1 Test in 31,3s** samt SQLnachweis. Der Researchtest
unterscheidet nun explizit die Importmeldung von weiteren Statusmeldungen und
sichert Antwortkörper und Idempotenzschlüssel unmittelbar beim HTTPempfang.
Alle ursprünglichen Replay- und Persistenzassertionen bleiben erhalten.

Ein anfänglich parallel gestarteter Paperlauf blieb nach dem ersten simulierten
Fill am Reconciliationgate stehen. Der isolierte Wiederholungslauf bestand; es
wurden keine Produktionsgates, Frischegrenzen oder Handelsregeln geändert. Die
Ursache dieses zeitabhängigen Testlaufs wurde nicht abschließend bestimmt.
Die diagnostische Testausgabe enthält ausschließlich synthetische Zustände.
