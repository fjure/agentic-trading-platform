# Spec Delta

## Purpose

Sichert den produktionsnahen Researchpfad und seine Isolationsgrenzen durch reproduzierbare Pflichtprüfungen.

## ADDED Requirements

### Requirement: ARP-I01 Pflichtprüfungen

Der CI-Workflow MUST für PRs schnelle und PostgreSQL-basierte Integrationsprüfungen ausführen. Relevante Änderungen und Releasekandidaten MUST die vollständige Browserkette prüfen.

#### Scenario: ARP-I01-S01 – Vollständiger Pflichtlauf

- **GIVEN** ein Release-SHA mit versioniertem Pflichtfallmanifest
- **WHEN** alle erforderlichen Fälle bestanden sind und Artefakte zum selben SHA vorliegen
- **THEN** der aggregierte Check ist positiv
- **AND** der Nachweis ist [ARP-05](../../work-packages/ARP-05.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet

#### Scenario: ARP-I01-S02 – Fehlende Umgebung oder Skip

- **GIVEN** ein ausgewählter Pflichtfall
- **WHEN** die Umgebung fehlt oder der Test übersprungen oder nicht ausgeführt wird
- **THEN** der jeweilige Zustand ist unterscheidbar und das Gate bleibt ohne positiven Nachweis
- **AND** der Nachweis ist [ARP-05](../../work-packages/ARP-05.md) zugeordnet

#### Scenario: ARP-I01-S03 – Unvollständiges Manifest-Ergebnis

- **GIVEN** ein abgeschlossener Job mit fehlendem Artefakt, falschem SHA oder fehlender Pflichtfall-ID
- **WHEN** der Aggregator die Ergebnisse prüft
- **THEN** das Pflichtgate scheitert trotz grüner Einzeljobs
- **AND** der Nachweis ist [ARP-05](../../work-packages/ARP-05.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet

### Requirement: ARP-I02 Security und Recovery

Die Releaseprüfung MUST Crash, Fencing, Wiederaufnahme, Revocation, Netzwerk- und Credentialisolation, doppelte Versuche und Kill/Reconciliation unter Researchlast nachweisen.

#### Scenario: ARP-I02-S01 – Recovery und Isolation unter Last

- **GIVEN** aktive Researchlast mit Gatewayfehler und einem Workercrash
- **WHEN** Kill, Pause und Reconciliation sowie stale Fence und Replay geprüft werden
- **THEN** jede Safetyaktion bleibt unter fünf Sekunden, der alte Attempt und Doppelstart sind gesperrt, Brokerorders und Finanzwrites bleiben aus
- **AND** der Nachweis ist [ARP-09](../../work-packages/ARP-09.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet

#### Scenario: ARP-I02-S02 – Verletzte Sicherheitsgrenze

- **GIVEN** ein Pflichtlauf mit unerlaubtem Netz-/Mountzugriff, Ledgerveränderung oder zu langsamer Safetyaktion
- **WHEN** die Ergebnisse aggregiert werden
- **THEN** das Releasegate scheitert und nennt die verletzte Assertion
- **AND** der Nachweis ist [ARP-09](../../work-packages/ARP-09.md), [ARP-10](../../work-packages/ARP-10.md) zugeordnet
