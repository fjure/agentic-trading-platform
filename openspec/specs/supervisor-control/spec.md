# supervisor-control Specification

## Purpose

Diese Fähigkeit stellt einen stabilen, lückenlos aktualisierbaren Supervisor-Zustand bereit und ermöglicht sichere Pausen- und Fortsetzungsaktionen ohne Entscheidungssystem.

## Requirements

### Requirement: REQ-CTL-001 Kanonischer Kontrollvertrag
Die Control API SHALL einen versionierten, kanonischen HTTP-Vertrag unter /api/v1 erfüllen. Öffentliche Antworten MUST stabile interne Datenmodelle statt roher Broker- oder Entscheidungsanbieter-Payloads verwenden; Geld, Preise und Mengen SHALL Dezimal-Strings und Zeitstempel UTC sein.

#### Scenario: T-CTL-001 Öffentliche Antwort
GIVEN eine Dashboard-Abfrage zu einer Order WHEN die API antwortet THEN enthält sie interne Orderdaten mit dezimalen Strings und keine rohe Brokerantwort.

### Requirement: REQ-CTL-002 Idempotente Schreibaktionen und Versionen
Jede schreibende Kontrollaktion MUST einen Idempotency-Key verlangen und bei Wiederholung semantisch dasselbe Ergebnis liefern. Risk-Policy- und Konfigurationsänderungen MUST Versionskonflikte erkennen und ablehnen; fachliche Trace-ID und Trace-Kontext SHALL erhalten bleiben.

#### Scenario: T-CTL-002 Wiederholte Pause
GIVEN eine erfolgreiche PAUSE_NEW-Anfrage WHEN dieselbe Anfrage mit demselben Schlüssel erneut eintrifft THEN entsteht keine zweite Zustandsänderung und das Ergebnis bleibt semantisch gleich.

### Requirement: REQ-CTL-003 Stabile Fehlersemantik
API-Fehler MUST als RFC-9457-Problemantwort mit stabilem maschinenlesbarem Code erscheinen und fachliche Ablehnung, Versionskonflikt, temporären Ausfall sowie UNKNOWN-Brokerstatus unterscheiden. Fehler SHALL weder Secrets noch interne Stacktraces offenlegen.

#### Scenario: T-CTL-003 Unbekannter Brokerstatus
GIVEN ein Submit-Timeout mit UNKNOWN-Orderstatus WHEN die API den Vorgang meldet THEN liefert sie einen unterscheidbaren Problemcode ohne sensible Rohdaten.

### Requirement: REQ-CTL-004 Lückenloser Reconnect
Supervisor-Listen SHALL Cursor-Pagination bereitstellen. Live-Updates SHALL Snapshot plus Cursor, Ereigniskennung, Heartbeat und Reconnect-Semantik unterstützen; ein abgelaufener Cursor MUST ausdrücklich gemeldet werden, damit ein neuer Snapshot statt einer stillen Lücke geladen wird.

#### Scenario: T-CTL-004 Unterbrochener Stream
GIVEN der Stream bricht nach Cursor C ab WHEN der Client erneut verbindet THEN erhält er alle seit C verfügbaren Ereignisse oder einen expliziten Hinweis zum erneuten Snapshot.

### Requirement: REQ-CTL-005 Unabhängige Sicherheitskontrollen
PAUSE_NEW und Kill-Switch MUST auch bei ausgefallenem Dashboard oder Decision-Service wirksam bleiben und neue Einstiege sofort sperren. Fortsetzen SHALL nur nach ausdrücklicher Aktion sowie erfolgreichem Health- und Reconciliation-Check möglich sein.

#### Scenario: T-CTL-005 Jev-Ausfall und Pause
GIVEN der Decision-Service ist ausgefallen WHEN PAUSE_NEW über den unabhängigen Kontrollpfad ausgelöst wird THEN werden neue Einstiege gesperrt und bestehende Reconciliation bleibt möglich.

### Requirement: REQ-CTL-006 Nachvollziehbare Supervisoransicht
Das Dashboard SHALL Modus, Paper-Konto, Verbindungen, Portfolio, Worker-Heartbeats, Marktstatus, Entscheidungsantworten mit Verteilung und Confidence, Vorschläge, Risk-Reason-Codes, Order- und Teilfüllstatus, Reconciliation, Trace-Audit und verfügbare API-Kosten anzeigen.

#### Scenario: T-CTL-006 Paper-Trade prüfen
GIVEN ein Paper-Trade mit Teilfüllung WHEN ein Supervisor seine Trace-ID öffnet THEN sind Evidenz, Entscheidung, Freigabe, Orderübergänge und Reconciliation nachvollziehbar.

### Requirement: REQ-CTL-007 Sichere Dashboardgrenzen
Das Dashboard SHALL standardmäßig nur lokal erreichbar sein, keine internen Gedankengänge oder Secrets anzeigen und keine Risk- oder Brokerentscheidungen im Client treffen. Gemeinsamer Anzeigezustand MAY gespeichert werden, während transaktionale Wahrheit und sensitive Payloads außerhalb des Browsers bleiben.

#### Scenario: T-CTL-007 Browserneustart
GIVEN das Dashboard wird neu geladen WHEN es den aktuellen Zustand lädt THEN stammen Modus und Portfolio aus dem Backend und kein Broker-Credential ist im Browser verfügbar.
