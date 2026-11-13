# Design

## Context

[Discovery](discovery.md) verankert den aktuellen Stand. APF-R04/R06/R07, APF-Q07, APF-D01–D04 sowie REQ-QEX-001/002/003/006/007/008/009 sind Anschlussverträge.

## Goals / Non-Goals

Produktionsnaher lokaler Researchbetrieb mit getrennten Identitäts-, Daten- und Ausführungsgates. Deterministische Quant- und Safety-Entscheidungen bleiben bei den bestehenden Diensten.

## Decisions

- [Provideraktivierung](provider-activation.md) und [ADR 01](adr/0001-provider-egress.md) spezifizieren die getrennte Produktionskomposition und den Versand.
- [Credential- und Egress-Bedrohungsmodell](threat-model.md) spezifiziert Prozessrollen, Schlüsselspeicher, Widerruf und Transportbarrieren.
- [CI-Design](ci-hardening.md), [Teststrategie](test-strategy.md) und [NLP-Protokoll](nlp-evaluation.md) behandeln getrennte Nachweisarten.
- [Betriebsrunbook](operator-runbook.md) und [Checkliste](operational-readiness.md) definieren Einbau und Inbetriebnahme.
- Neue HTTP-Verträge werden zuerst in contracts/http/control-api.openapi.yaml, Beispielen und DTOs präzisiert und geprüft. Der Angular-Client wird generiert. Setup nutzt einen getrennten lokalen Same-Origin-Pfad; normale Control-API-Projektionen enthalten nur Status, Revisionen und redigierte Problemcodes. Kein Research-Schlüssel erreicht Control, Supervisor oder Quant.
- Quant erhält nur lokal validierte, explizit bestätigte Hypothesen. Fachliche Bestätigung bindet Revision und Annahmen. Entwurf und gesonderter Start binden die konkrete Registry-Version, Auswahlpolicy, Snapshots, Datenrechte, Limits und Freigabe; Admission bleibt atomar. STRICT_PIT ist Standard. Eine Approximation erfordert eine separate revisionsgebundene Entscheidung, RETROSPECTIVE_EXPLORATORY eine ausdrückliche Wahl. Proaktive Vorschläge können nur durch bewusste Übernahme in den interaktiven Ablauf gelangen. Reportclaims verweisen ausschließlich auf lokal gespeicherte Evidenz.

## Risks / Trade-offs

Sofortiger Entzug ist ab dem serialisierten Sendepunkt beweisbar. Bereits extern übertragene Inhalte können verarbeitet worden sein. Konservative unbekannte Kosten können den Betrieb bis zur Reconciliation blockieren. Modellverfügbarkeit, Datenlizenz, Anbieterbedingungen und Kontozugriff sind betreiberspezifisch.

## Migration Plan

Additive DB-Migration und Verträge zuerst. Der optionale Compose-Pfad startet gesperrt und darf erst nach Offline-Gates konfiguriert werden. Rollback sperrt Profile und Zeitpläne vor dem Stoppen der optionalen Dienste, erhält Ledger und Audit und lässt lokale Klärung verfügbar. Offene Versuche werden konservativ behandelt.
