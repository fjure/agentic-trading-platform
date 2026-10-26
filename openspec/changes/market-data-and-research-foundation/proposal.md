# Proposal

## Why

Die vorhandene Plattform besitzt einen offline zertifizierten Paperpfad, aber noch keine dauerhafte historische Marktdatenbeschaffung oder abfragbare Research-Vintages. Künftige Analysen benötigen reproduzierbare Daten ohne Look-Ahead-Bias, bevor Backtesting oder autonome Researchrollen sinnvoll werden.

## What Changes

- Kanonische Produktvision, Architekturprinzipien und Roadmap dokumentieren und mit AGENTS sowie OpenSpec-Kontext verbinden; bestehende abgeschlossene Deltas getrennt nachprüfen und synchronisieren.
- Providerunabhängige Instrumente, historische OHLCV-Vintages, Qualitätsbefunde und Instrument-/Symbolgültigkeiten ergänzen.
- Einen realen Alpaca-Historienadapter und dauerhafte, wiederaufnehmbare Backfill-/Updatejobs bereitstellen; private Anbieterverbindungen bleiben ausschließlich Execution-owned.
- ResearchEvidence, Point-in-Time-Abfragen und MarketContext mit expliziten Datenlücken anbieten; Source-Ports für News, Fundamentals und Makrodaten bleiben zunächst ohne echte Adapter.
- Additive authentifizierte Research-API und eine kleine Angular-Material-Ansicht für Historie, Evidenz, Quellenqualität und Jobstatus vorsehen.
- Bestehende Risk-/Execution-, Setup- und Ledgergrenzen erhalten. Keine Orders, Handelsaktivierung, Backtestingengine, LangGraph-Einführung oder zusätzlichen kostenpflichtigen Datenabonnements.

## Capabilities

### New Capabilities

- `historical-market-data`: Instrumentnormalisierung, historische Providerabfragen, dauerhafte Ingestion, Datenqualität und Coverage.
- `research-evidence`: versionierte nachweisbare Evidenz und konservative Point-in-Time-Selektion.
- `research-context`: verfügbarkeitsgebundene Aggregation, Quellenverträge und authentifizierte Researchbedienung.

### Modified Capabilities

Keine. Die neuen Researchdaten sind keine frischen Trading-MarketStates und dürfen bestehende `market-evidence`-Frischegates nicht erfüllen. Bestehende Anforderungen werden unverändert in die Main-Specs konsolidiert.

## Impact

Bestehende Python-/PostgreSQL-/HTTPX-Toolchain und SQL-Leases wiederverwenden; additive Migrationen in `services/control_api/migrations/versions/`, neue Module unter `packages/research/`, Execution-eigene GET-Anbindung in `services/execution/` und `packages/brokers/`, credentialfreier Job-/Querypfad in `services/worker/` und `services/control_api/`. Kanonischer HTTP-Vertrag, Beispiele, generierter Angularclient, Dashboard, Compose-/Rollenrechte und Tests werden im Apply erweitert. Keine neue Datenbanktechnologie oder Orchestrierungsbibliothek. Planung ist noch keine Implementierung oder Providerabnahme.
