# Proposal

## Why

Die Backtestsoftware ist offline geprüft, reale Experimente können jedoch an erst heute beobachteten Bars, unbekannten Kalendern oder unbelegten Kapitalmaßnahmen scheitern. Betreiber brauchen vor einem Experiment nachvollziehbare Lücken und einen sicheren Weg, künftig beobachtete Daten und überprüfbare Belege aufzubauen.

## What Changes

- Eine dauerhafte, begrenzte Datenzulässigkeitsprüfung zeigt pro Experiment und Fold fehlende oder verspätete Bars, Kalender-, Qualitäts- und Kapitalmaßnahmenbelege mit Zeitpunkt und Herkunft. Sie verwendet dieselben Regeln wie der Backtest und startet weder Simulation noch Beschaffung.
- Ein lokaler Offlineimport übernimmt streng typisierte, quellengebundene Kalender-, Qualitäts- und Kapitalmaßnahmenbelege unveränderlich. Herkunft, tatsächliche Beobachtung und Konflikte bleiben erhalten; menschliche Eingabe allein ist kein Beweis historischer Verfügbarkeit.
- Integrität, Herkunft, zeitliche Authentizität, Vollständigkeit und fachliche Qualität erhalten unabhängige Bewertungen; erst gemeinsame protokollgebundene Regeln bestimmen Research Eligibility. Signatur und Existenzzeitstempel beweisen keine historische Veröffentlichung. Vier Zulässigkeitszustände verhindern positive Aussagen bei unbekannten oder ungeprüften Pflichtbereichen.
- Explizit aktivierbare Sammlungspläne für SPY/QQQ, raw IEX, 15Min ergänzen bestehende Updatejobs um begrenzte fortlaufende Beobachtung. Kalender werden vor Sitzungen abgefragt; Bars werden zeitnah nach Schluss abgefragt und später kontrolliert erneut beobachtet. Ausfälle bleiben sichtbare Lücken.
- Angular Material zeigt Datenzulässigkeit, Belegreferenzen, Sammlungsfortschritt und Blockiergründe einschließlich nicht überprüfter Providerrechte. Standardmäßig bleibt die Sammlung aus.
- Sechs getrennte Zeitbegriffe, kalendergebundene Fristquoten und tatsächlich zusammenhängende Testfenster machen den Nutzwert der Sammlung messbar. IEX-Eignung bleibt ohne Beobachtungsnachweis unverifiziert; weder +60s noch +300s werden gelockert. Details und mögliche spätere Datenpfade: `provider-assessment.md`.
- RDR-03 liefert Persistenz und grundlegende Nachweisverwaltung; RDR-09 liefert den eigenständig prüfbaren Trusted Evidence Import. Sammlung und Diagnose hängen nicht von RDR-09 ab; die gemeinsame Endabnahme umfasst weiterhin alle neun Pakete.
- Keine automatische Reparatur alter Experimente, keine zurückdatierten Daten, keine neue Datenquelle, keine Kapitalmaßnahmen-/Total-Return-Simulation und keine Handelsaktivierung. Ein zukünftiges zulässiges Fenster wird nicht versprochen: Quellen- und Beleglücken können auch fortlaufend gesammelte Daten blockieren.

## Capabilities

### New Capabilities

- `research-data-readiness`: Unveränderliche Zulässigkeitsberichte und kontrollierter Offlineimport ihrer Beleggrundlagen.

### Modified Capabilities

- `historical-market-data`: Additive Anforderungen für explizite, dauerhafte und begrenzte Sammlungspläne auf bestehenden Ingestionpfaden.

Bestehende Anforderungen aus `research-evidence` und dem abgeschlossenen, noch nicht archivierten Change `point-in-time-research-backtesting` bleiben verbindlich. Dessen Delta und Implementierungsnachweis bilden die Backtestbasis; Main-Spec-Synchronisierung ist nicht Teil dieses Changes.

## Impact

Betroffen sind `packages/backtesting/`, `packages/research/`, der Execution-eigene Researchdispatcher, additive PostgreSQL-Migrationen und Rollen, Control-API, kanonischer OpenAPI-Vertrag, generierter Angularclient, Researchoberfläche und Runbooks. Es werden keine zusätzlichen Frameworks oder Provider eingeführt. Alpaca-Credentials verbleiben ausschließlich in Execution; der isolierte Backtestworker erhält nur lesenden Datenzugriff und eigene Berichtspersistenz. Umsetzung benötigt erfüllte Definition of Ready und einen gesonderten Apply-Auftrag.
