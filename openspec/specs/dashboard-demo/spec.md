# dashboard-demo Specification

## Purpose

Nutzer können einen endlichen synthetischen Handelsablauf unmittelbar im lokalen Dashboard auslösen und dessen tatsächliche persistierte Ergebnisse prüfen, ohne einen Broker anzubinden.

## Requirements

### Requirement: REQ-DEMO-001 Einfacher sichtbarer Demolauf
Das lokale Fake-Lab SHALL nach der bestehenden Anmeldung „Demo starten“ anbieten. Ein Klick SHALL einen begrenzten synthetischen Lauf ohne zusätzliche Terminalbefehle, Brokercredentials oder Abhängigkeit von echten Börsenöffnungszeiten starten und seinen laufenden, erfolgreichen oder fehlgeschlagenen Zustand anzeigen. Die Oberfläche MUST während der gesamten Demo deutlich „Simulation – keine Brokerverbindung“ anzeigen.

#### Scenario: T-DEMO-001 Ein Klick bis zum sichtbaren Trade
- **WHEN** ein angemeldeter Nutzer im lokal gestarteten Fake-Lab die Demo startet
- **THEN** sieht er Fortschritt und nach Abschluss mindestens eine gefüllte synthetische Order sowie eine veränderte Demo-Position und einen entsprechenden Demo-Cashbestand.

### Requirement: REQ-DEMO-002 Tatsächlicher Handelsdurchstich
Die Demo SHALL synthetische Marktdaten über den bestehenden Mockentscheidungs-, Vorschlags-, Risk-, Reservierungs-, Intent-, FakeBroker-, Fill- und Reconciliationpfad verarbeiten. Fertige Order-/Portfoliowerte MUST NOT lediglich als Browserfixture oder SQL-Ergebnisattrappe eingeblendet werden. Geld und Mengen MUST Decimal, Zeitstempel UTC verwenden. Alle Ergebnisse SHALL im persistenten Demoledger über eine gemeinsame Trace-ID verknüpft sein.

#### Scenario: T-DEMO-002 Prüfkette und Neuladen
- **WHEN** die Demo abgeschlossen ist und der Nutzer Order, Entscheidung, Risikoprüfung, Portfolio und Audit öffnet sowie die Seite neu lädt
- **THEN** bleiben derselbe gefüllte Auftrag, seine zusammengehörigen Belege, die gebuchte Position und die Cashänderung aus dem Backend nachvollziehbar.

### Requirement: REQ-DEMO-003 Harte Trennung von Paper und bestehendem Ledger
Die Demo MUST ausschließlich in einem isolierten Demoledger mit FakeBroker und MockDecisionEngine ohne externe Provider, Credentials oder öffentlichen Egress arbeiten. Bestehendes Labor-/Paperledger und dessen Sicherheitszustand MUST unverändert bleiben. Demoaufrufe MUST echte Paperorders, LIVE, Shellausführung und globale Änderungen an Markt-/Recoverygates ausschließen.

#### Scenario: T-DEMO-003 Unzulässige Konfiguration
- **WHEN** ein Demoauftrag mit externem Provider, falschem Ledger oder fehlender eindeutiger Simulationskonfiguration ausgeführt werden soll
- **THEN** wird er ohne Providerzugriff und ohne Änderung am bestehenden Ledger abgewiesen und die Oberfläche meldet einen redigierten Fehler.

### Requirement: REQ-DEMO-004 Endliche idempotente Ausführung
Pro Demoledger SHALL höchstens ein Lauf aktiv sein. Derselbe Idempotenzschlüssel MUST denselben Lauf ohne doppelte Reservierung, Order oder Fillbuchung referenzieren. Nach Abschluss oder Fehler SHALL die Demo pausiert sein. Explizite neue Läufe SHALL Historie unter neuer Run-ID erhalten. Eine während des Laufs gesetzte Pause-/Killlatch MUST weitere Ausführung stoppen und darf durch Wiederholung nicht aufgehoben werden.

#### Scenario: T-DEMO-004 Doppelklick und Wiederholung
- **WHEN** der Startrequest mehrfach mit demselben Schlüssel eintrifft oder ein aktiver Lauf parallel erneut gestartet wird
- **THEN** entsteht höchstens eine wirtschaftliche Wirkung und die UI zeigt den existierenden Lauf beziehungsweise einen klaren Konflikt.

#### Scenario: T-DEMO-005 Fehler und Kontrollaktion
- **WHEN** während der Simulation ein Fehler oder eine Pause-/Killaktion auftritt
- **THEN** bleibt der Lauf ohne automatische Wiederholung geschlossen beziehungsweise pausiert, bestehende Belege bleiben sichtbar und der Fehler wird ohne private Daten angezeigt.

### Requirement: REQ-DEMO-005 Authentifizierung und nachgewiesene Oberfläche
Demosteuerung und Datenzugriff MUST den bestehenden Session-, Host-/Origin-, CSRF- und Projektionseinschränkungen entsprechen. Der kanonische OpenAPI-Vertrag SHALL alle Demooperationen und Simulationsmetadaten definieren; generierter Angularclient und Backend MUST demselben Vertrag entsprechen. Die Abnahme SHALL einen realen Browserklick gegen die tatsächliche API und persistente Datenbank bis zum gefüllten Trade belegen.

#### Scenario: T-DEMO-006 Unerlaubter Start
- **WHEN** ein nicht angemeldeter oder fremder Origin einen Demolauf starten will
- **THEN** wird der Start ohne Demo- oder Handelswirkung abgewiesen.

#### Scenario: T-DEMO-007 Browser bis Datenbank
- **WHEN** die automatisierte Abnahme eine echte lokale Demoanwendung öffnet und „Demo starten“ anklickt
- **THEN** zeigt der Browser die aus dem echten API-/Datenbankpfad stammende gefüllte Order, deren Trace und die Portfolioänderung auch nach Neuladen; eine reine Antwortfixture genügt nicht.
