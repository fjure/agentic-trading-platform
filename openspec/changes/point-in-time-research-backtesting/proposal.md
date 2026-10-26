# Proposal

## Why

Die abgeschlossene Researchfoundation speichert historische Evidenz und konservative Wissensstände, bietet aber noch keinen reproduzierbaren Strategieprüfzyklus. Ein begrenzter Backtest soll belastbar unterscheiden, was mit damals belegtem Wissen auswertbar ist und was wegen fehlender damaliger Verfügbarkeit blockiert bleibt.

## What Changes

- Lokale, versionierte Strategiedefinitionen und unveränderliche Experimentmanifeste für SPY/QQQ, raw IEX, 15Min, Long/Flat, ohne Leverage oder Shortpositionen.
- Streng beobachtungsgebundene historische Simulation mit je Entscheidung fortschreitendem Wissensstand, eingefrorenen Revisionen, Kalendern und Qualitätsbelegen. Heutige Backfills begründen keine damalige Verfügbarkeit.
- Explizites, deterministisches Simulationsmodell für nächste handelbare Kerze, Gebühren, Slippage, Stückelung und getrenntes Simulationscash; keine Brokeranbindung.
- Vorab festgelegte zeitliche Trainings-/Testfenster und Walk-forward-Auswertung, Referenzstrategien, Drawdown, Nettorendite, Turnover und sichtbare Einschränkungen.
- Dauerhafte begrenzte Backtestjobs, authentifizierte additive API und kleine Ergebnisansicht im bestehenden Researchbereich.

Nicht enthalten: retrospektiver Modus mit gelockerten Wissensgrenzen, neue Datenprovider oder Märkte, News/Makro, Jev-Aufrufe, freie Strategieprogramme, Parametersuche, autonome Agentenrollen, Paperpromotion oder LIVE. Persönliche Providerrechte bleiben eine private menschliche Voraussetzung für echte Datenaufnahme; Offlinefixtures ermöglichen die Implementierungsabnahme ohne Providerzugriff.

## Capabilities

### New Capabilities

- `research-backtesting`: Versionierte Experimente, zeitlich zulässige lokale Simulation, Auswertungsprotokoll und dauerhafte sichere Bereitstellung ihrer Ergebnisse.

### Modified Capabilities

Keine. Bestehende Research-, Replay- und Handelsanforderungen werden unverändert genutzt; insbesondere wird REQ-REV-002 nicht gelockert.

## Impact

Vorgesehene Erweiterungen: `packages/backtesting/`, additive Researchpersistenz unter `services/control_api/migrations/versions/`, begrenzter Workerjob, `services/control_api/backtest_*.py`, `contracts/http/control-api.openapi.yaml` und JSON-Schemas sowie `apps/dashboard/src/app/research/`. Vorhandene Query-/Kalender-/Qualitätsports werden wiederverwendet; generierter Angularclient entsteht ausschließlich aus dem Vertrag. Kein neues Framework und keine zusätzlichen Credential-, Broker- oder Handelsrechte. Vor Apply müssen Vertragsentwurf, Requirement-/Taskzuordnung, Issue-Sync und unabhängiger Review vollständig sein; dieser Vorschlag erteilt keinen Apply-Auftrag.
