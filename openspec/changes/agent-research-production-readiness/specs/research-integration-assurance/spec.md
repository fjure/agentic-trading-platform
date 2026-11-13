# Spec Delta

## Purpose

Sichert den produktionsnahen Researchpfad und seine Isolationsgrenzen durch reproduzierbare Pflichtprüfungen.

## ADDED Requirements

### Requirement: ARP-I01 Pflichtprüfungen

Der CI-Workflow MUST für PRs schnelle und PostgreSQL-basierte Integrationsprüfungen ausführen. Relevante Änderungen und Releasekandidaten MUST die vollständige Browserkette prüfen.

#### Scenario: Überprüfbares Verhalten

- **WHEN** ein Pflichtlauf fehlschlägt, übersprungen wird, nicht verfügbar ist oder nicht ausgeführt wird
- **THEN** der Status unterscheidet die Fälle und liefert keinen positiven Gatenachweis

### Requirement: ARP-I02 Security und Recovery

Die Releaseprüfung MUST Crash, Fencing, Wiederaufnahme, Revocation, Netzwerk- und Credentialisolation, doppelte Versuche und Kill/Reconciliation unter Researchlast nachweisen.

#### Scenario: Überprüfbares Verhalten

- **WHEN** synthetische Gateway- und Quantfehler gemeinsam injiziert werden
- **THEN** die Anwendung bleibt fail-closed und Risk und Execution behalten ihre unabhängigen Kontrollen
