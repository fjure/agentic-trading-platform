# Spec Delta

## Purpose

Diese Fähigkeit erhält Handelszustand, Aufträge und Auditspuren über Neustarts hinweg und begrenzt Geheimnisse, externe Inhalte und Wiederholungsfehler.

## ADDED Requirements

### Requirement: REQ-RUN-001 Dauerhafter Handelszustand
Das System SHALL Instrumente, Kerzen, Evidenz, Marktstatus, Entscheidungen, Vorschläge, Risk-Policy und Reservierungen, Orders, Ausführungsberichte, Positionen, Portfolio-Snapshots, Agentenläufe und Audit-Ereignisse dauerhaft und eindeutig verknüpft halten.

#### Scenario: T-RUN-001 Wiederanlauf
GIVEN eine reservierte Order und ein Prozessabbruch WHEN der Dienst neu startet THEN sind Vorschlag, Entscheidung, Reservierung und Orderstatus vollständig rekonstruierbar.

### Requirement: REQ-RUN-002 Eindeutige Identitäten und Wiederholung
Aufträge und fachliche Ereignisse MUST stabile Identitäten und Eindeutigkeitsgrenzen besitzen; wiederholte Zustellung SHALL keine zweite Order, Reservierung oder Positionsänderung erzeugen. Das System SHALL mindestens einmal zugestellte Ereignisse und idempotente Verarbeitung annehmen.

#### Scenario: T-RUN-002 Doppelte Zustellung
GIVEN dasselbe Brokerereignis trifft zweimal ein WHEN beide Zustellungen verarbeitet werden THEN wird seine wirtschaftliche Wirkung genau einmal gebucht.

#### Scenario: T-RUN-024 Account- und Capabilitybindung
GIVEN Markt- und Tradingbelege betreffen unterschiedliche Konten, Assets, Sitzungen oder Capabilityversionen WHEN Execution sie als Risikokontext veröffentlichen oder unmittelbar vor Submit prüfen soll THEN wird der Vorgang ohne Brokerübermittlung abgewiesen und ausschließlich ein stabiler nicht sensitiver Grund auditiert.

#### Scenario: T-RUN-025 Persistente Activities-Wiederholung
GIVEN eine bereits gebuchte FILL- oder Gebührenbeobachtung wird nach Neustart erneut oder in anderer Reihenfolge geliefert WHEN sie auf das SQL-Ledger angewendet wird THEN entstehen keine doppelten Cash-/Positionsänderungen und jeder bestätigte Effekt bleibt mit seinem unveränderlichen Beleg und Trace verknüpft.

### Requirement: REQ-RUN-003 Wiederaufnehmbare Jobs
Hintergrundarbeit SHALL dauerhaft mit Lease, Heartbeat, begrenzten Wiederholungen und Dead-Letter-Zustand verwaltet werden. Ein abgelaufenes Lease MAY nur nach Prüfung des bestehenden Arbeits- und Brokerzustands übernommen werden.

#### Scenario: T-RUN-003 Verlorener Worker
GIVEN ein Worker stirbt während einer Orderverarbeitung WHEN sein Lease abläuft THEN prüft der Nachfolger den gespeicherten Zustand, bevor er eine externe Wirkung erwägt.

### Requirement: REQ-RUN-004 Dauerhafte Ereignisweitergabe
Eine bestätigte Zustandsänderung und ihr auszulieferndes Ereignis MUST gemeinsam dauerhaft werden. Ereignisse SHALL ID, versionierten Typ, Zeitpunkt, Aggregat-ID und -Version, Trace-ID und Payload tragen. Fehlgeschlagene Zustellung SHALL wiederholt werden können; Verbraucher MUST Duplikate erkennen.

#### Scenario: T-RUN-004 Absturz vor Zustellung
GIVEN eine Orderänderung ist bestätigt WHEN der Prozess vor der Ereigniszustellung abstürzt THEN bleibt das Ereignis nach dem Neustart zur Zustellung vorhanden.

### Requirement: REQ-RUN-005 Audit und Korrelation
Handels-, Sicherheits- und Kontrollentscheidungen SHALL mit unveränderbar nachvollziehbarer Trace-ID, Akteur, Zeitpunkt, Policy-Version, Ergebnis und Reason Code auditiert werden. Audit-Einträge MUST aus Anwendungssicht nur angehängt werden; Logs SHALL Geheimnisse redigieren.

#### Scenario: T-RUN-005 Fehlgeschlagene Freigabe
GIVEN eine RiskDecision lehnt einen Vorschlag ab WHEN der Vorgang geprüft wird THEN führt seine Trace-ID zu Input, Policy, Reason Code und Audit-Eintrag ohne Secretwert.

### Requirement: REQ-RUN-006 Geheimnis- und Inhaltsgrenzen
Paper- und spätere Live-Geheimnisse MUST getrennt bleiben; nur Execution MAY Paper-Broker-Credentials erhalten. Schlüssel und personenbezogene Daten MUST aus Repository, Logs, Prompts, Browser und Dashboard fernbleiben. Externe Dokumentinhalte SHALL keine Toolausführung oder Paketinstallation auslösen.

#### Scenario: T-RUN-006 Fremdinhaltsanweisung
GIVEN ein Evidenztext enthält eine Anweisung zur Toolausführung WHEN er verarbeitet wird THEN bleibt er Dateninhalt und löst keine Ausführung aus.

### Requirement: REQ-RUN-007 Betriebsbeobachtung und Zahlenkonvention
Technische Zeitstempel MUST UTC sein und Geld, Preise sowie Mengen MUST exakt dezimal verarbeitet werden. Strukturierte Betriebsereignisse, Gesundheitszustände und Metriken SHALL Datenstillstand, Broker-, Entscheidungs- und Speicherstörung getrennt sichtbar machen, ohne sensitive Payloads offenzulegen.

#### Scenario: T-RUN-007 Datenstillstand
GIVEN keine frische Kerze erreicht das System WHEN die Frischegrenze abläuft THEN wird der Datenzustand als gestört ausgewiesen und abhängige neue Einstiege bleiben gesperrt.
