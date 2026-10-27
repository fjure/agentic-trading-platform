# Proposal

## Why

Heutige historische Backfills bleiben im bestehenden Strict-PIT-Backtester zu Recht blockiert. Ein ausdrücklich retrospektiver Forschungsmodus soll diese Bestände reproduzierbar untersuchen können und ihre fehlende damalige Verfügbarkeit sichtbar erhalten.

## What Changes

- Explizite, versionierte Auswahl `RETROSPECTIVE_EXPLORATORY`; fehlende Auswahl bleibt `STRICT_PIT`, ohne automatischen Fallback.
- Eigene typisierte Dataset-Policy und eingefrorene Revisionen; gemeinsame Strategien, Decimal-Simulation und Evaluation mit getrennten Validierungskontexten.
- Unveränderliche Experimentmanifeste sowie strukturierter Data Reliability Report mit qualitativen Einzeldimensionen, Kapitalmaßnahmenstatus und Nutzungsrechten.
- Additive Backtestverträge, dauerhafte Jobs und kleine Angular-Material-Erweiterung einschließlich Vorschau, Moduskennzeichnung und Reload.
- Nachweisbare SQL-/Runtime-Isolation und vollständige Strict-PIT-Regression vor Freigabe.

### Scope

SPY/QQQ, Alpaca/raw/IEX/15Min, bestehende drei Strategien und Ressourcenbudgets. Price-only ohne Dividendenzahlung, Splitmodell oder Total-Return-Behauptung. Keine neuen Provider, Adjustierungen, freien Programme, Optimierung, Agentenorchestrierung, Promotion, Brokeraktionen oder Paper-/LIVE-Aktivierung.

## Capabilities

### New Capabilities

- `retrospective-research-backtesting`: ausdrücklich gewählte, reproduzierbare explorative Experimente mit separater Datenselektion und unveränderter Strict-PIT-Grenze.
- `research-data-reliability-report`: snapshotgebundene Qualitäts-, Kapitalmaßnahmen-, Methoden- und Einschränkungsberichte für retrospektive Experimente.

### Modified Capabilities

Keine bestehende Main-Capability wird semantisch geändert. Bestehende Backtesting-/Readiness-Anforderungen gelten ausschließlich für ihren bisherigen Strict-PIT-Pfad fort; die neue Fähigkeit definiert einen separaten expliziten Pfad. Die abgeschlossenen, noch nicht konsolidierten Changes bleiben verbindliche Referenzen; siehe discovery.md. Keine eigenmächtige Main-Spec-Synchronisierung oder Archivierung.

## Impact

Geplant: `packages/backtesting/`, `services/worker/backtesting.py`, `services/runtime/backtesting.py`, Control-API-DTOs/-Service/-Routen, additive Migrationen, `contracts/http/control-api.openapi.yaml`, generierter Angularclient und `apps/dashboard/src/app/research/`. Bestehende Ingestion- und Handelsspeicher bleiben unverändert. Zusätzliche Quellenrechte und historische Evidenz werden nicht behauptet.

Dieser Change liefert Planung und Issues. Anwendungscode, kanonischer HTTP-Vertrag und generierter Client werden erst nach gesondertem Apply-Auftrag geändert.
