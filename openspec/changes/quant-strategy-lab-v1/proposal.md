# Proposal

## Why

Einzelbacktests erlauben noch keine kontrollierte, dauerhaft nachvollziehbare Parametersuche. Quant Strategy Lab v1 verbindet die vorhandene Engine mit einer gemeinsamen Datenbasis, vorab festgelegter Auswahl und ehrlicher Out-of-Sample-Auswertung.

## What Changes

- Versionierter, geschlossener Strategiekatalog einschließlich EMA Crossover und RSI Mean Reversion mit exakten Initialisierungsregeln.
- Begrenzte deterministische Grids, unveränderliche Experimentversionen, gemeinsamer Dataset-Snapshot und fairer Warm-up.
- Chronologische In-Sample-/Parameterauswahl-/OOS-Protokolle mit separatem Holdout; unveränderliche versionierte Strategy Selection Policies, risikobegrenzte Nettorenditemaximierung als Standard und ausdrückliche Nichtwahl.
- Transparenz über Multiple Testing, Kandidaten-/Rundenumfang und Wiederverwendung sämtlicher ausgewerteter Zeitrollen, ohne behauptete statistische Sicherheit.
- Persistente Batches mit bestehenden Backtestresultaten, Reliabilityreferenzen, begrenzter Wiederaufnahme und gemeinsamem Workerbudget.
- Serverseitige Vergleichsberichte einschließlich barbasierter Investiertheitsquote, Exposition, Fill-/Positionsaktivität und Materialoberfläche mit persistenten Links; keine versteckte Aktivitätspräferenz.
- Additive versionierte Researchverträge, ohne LLM-Integration oder zusätzliche Handelsrechte.

## Capabilities

### New Capabilities

- `research-strategy-registry`: unveränderliche lokale Strategieversionen und Indikatorsemantik.
- `research-experiments`: begrenzte Suche, unveränderliches Forschungsprotokoll und sichere Persistenz/Orchestrierung.
- `research-comparison`: vergleichbare, methodisch definierte Ergebnisse und authentifizierte Bedienung.

### Modified Capabilities

Keine Main-Spec wird geändert. Bestehende Research-/Safety-Anforderungen gelten weiter. BKT/RDR/RRB sind implementierte, teilweise noch nicht konsolidierte Delta-Basen; dieser Change erweitert sie additiv, ohne sie zu archivieren oder deren Requirements umzuschreiben. Siehe discovery.md.

## Impact

`packages/backtesting`, isolierter Backtestworker, additive Migration nach 0034, Control-API, kanonischer OpenAPI-Vertrag und generierter Angularclient, Researchkomponenten und zugehörige Tests. Kein neues Framework, Dienst, Provider oder Simulator. Scope: ein Instrument SPY oder QQQ, USD/raw/IEX/15Min, Long/Flat, beide bestehenden Forschungsmodi. Maximal 32 Suchkandidaten plus zwei feste Referenzen, vier Walk-forward-Folds und ein finales Holdout; tatsächliche Zulassung zusätzlich durch Gesamtarbeitsbudget begrenzt.

Propose erstellt ausschließlich Planungsartefakte und synchronisierte offene Issues. Keine Anwendungsimplementierung, Aktivierung, Order, Strategy Promotion, Riskänderung, LangGraph, dynamischen Imports oder Strategieuploads. Apply erfordert einen neuen Nutzerauftrag.
