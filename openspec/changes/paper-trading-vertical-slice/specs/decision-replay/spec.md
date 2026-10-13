# Spec Delta

## Purpose

Diese Fähigkeit bindet austauschbare Entscheidungen an unveränderliche Eingaben und macht Enthaltung, Jev-Antworten und zeitlich korrektes Replay prüfbar.

## ADDED Requirements

### Requirement: REQ-DEC-001 Unveränderlicher Entscheidungsinput
Jede Entscheidung MUST auf einem unveränderlichen Input mit Instrument, Zeitpunkt, verfügbarer Evidenz, Datenqualität und Herkunft beruhen; spätere Datenkorrekturen SHALL eine neue Inputversion erzeugen.

#### Scenario: T-DEC-001 Korrigierte Kerze
GIVEN ein gespeicherter Entscheidungsinput WHEN eine zugrunde liegende Kerze korrigiert wird THEN bleibt der ursprüngliche Input unverändert und eine neue Version ist unterscheidbar.

### Requirement: REQ-DEC-002 Begrenzte Entscheidungsauswahl
Eine Entscheidung SHALL ausschließlich LONG, FLAT oder ABSTAIN als Choice liefern; andere Richtungen oder implizite Handelsaktionen MUST zurückgewiesen werden.

#### Scenario: T-DEC-002 Unzulässige Choice
GIVEN eine Adapterantwort mit SHORT WHEN sie validiert wird THEN wird sie abgewiesen und keine Freigabe ausgelöst.

### Requirement: REQ-DEC-003 Jev-Antwortgrenzen
Eine Jev-Antwort SHALL Marktregime als Choice einschließlich uncertain, Evidenzqualität als Score, Signalkonflikt als Noul und Confidence getrennt liefern; eine optionale Shadow-Choice SHALL separat bleiben. Confidence MUST weder als Gewinnwahrscheinlichkeit noch zur Positionsgröße dienen; Jev SHALL keine Order anweisen.

#### Scenario: T-DEC-003 Versteckte Größenangabe
GIVEN eine Jev-Antwort mit zusätzlicher Ordergröße WHEN sie angenommen werden soll THEN wird die unzulässige Handlungsangabe nicht in die Risikofreigabe übernommen.

### Requirement: REQ-DEC-004 Explizite Enthaltung
Bei fehlender Evidenz, ungültiger Antwort, Unsicherheit außerhalb definierter Grenzen oder nicht verfügbarer Entscheidungsfähigkeit MUST das System ABSTAIN mit nachvollziehbarem Grund erzeugen und jede neue Handelsfreigabe unterlassen.

#### Scenario: T-DEC-004 Ungültige Antwort
GIVEN eine unvollständige Entscheidungsantwort WHEN sie bewertet wird THEN entsteht ABSTAIN mit Grund und keine neue Order.

### Requirement: REQ-DEC-005 Atomare Jev-Fragen
Jede Jev-Frage SHALL genau einen fachlichen Sachverhalt abfragen; mehrere solche Fragen MAY in einem Request gestellt werden. Ihre Antworten SHALL getrennt mit Input und Frageversion verbunden sein; mehrdeutige Antworten MUST zurückgewiesen werden.

#### Scenario: T-DEC-005 Mehrdeutige Antwort
GIVEN eine Frage zu genau einem Sachverhalt WHEN die Antwort mehrere unabhängige Urteile enthält THEN wird sie nicht als gültige Antwort verwendet.

### Requirement: REQ-DEC-006 Versionierte Entscheidungsgrenzen
Jede Entscheidung SHALL vollständige typisierte Antwort, Verteilungen, Confidence, Modellkennung, Prompt- und Frageversion, Input-Hash, Latenz, API-Verbrauch, Trace-ID und Gültigkeitsende festhalten; frühere Versionen MUST erhalten und vergleichbar bleiben.

#### Scenario: T-DEC-006 Regelwechsel
GIVEN zwei Auswertungen desselben Inputs mit verschiedenen Regelversionen WHEN die Ergebnisse verglichen werden THEN sind beide Ergebnisse und ihre Versionen separat sichtbar.

### Requirement: REQ-DEC-007 Reproduzierbares Replay
Replay SHALL aus denselben versionierten Inputs und Regeln dasselbe fachliche Ergebnis reproduzieren und keine neue Paper-Order auslösen; Abweichungen MUST als solche erkennbar sein.

#### Scenario: T-DEC-007 Wiederholung
GIVEN ein gespeicherter Input und festgehaltene Versionen WHEN Replay zweimal läuft THEN stimmen die fachlichen Ergebnisse überein und es wird keine Order gesendet.

### Requirement: REQ-DEC-008 Austauschbare Entscheidungsfähigkeit
Entscheidungsquellen SHALL ihre unterstützten Eingaben und Ergebnisfelder ausweisen; eine nicht unterstützte Fähigkeit oder eine ausgefallene Quelle MUST zu einer sicheren Enthaltung führen.

#### Scenario: T-DEC-008 Nicht unterstützter Input
GIVEN eine Entscheidungsquelle ohne Fähigkeit für den angeforderten Input WHEN sie ausgewählt wird THEN entsteht ABSTAIN statt einer geschätzten Antwort.
