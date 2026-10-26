# Produktroadmap

Die Reihenfolge ist eine Planungsrichtung. Jede Anwendungserweiterung braucht einen eigenen OpenSpec-Change, erfüllte Definition of Ready und einen gesonderten Apply-Auftrag. Die Roadmap behauptet keine bereits erteilte Umsetzungsfreigabe.

## Heute: lokales Paper-Lab

**Umgesetzt:** getrennte Control-, Worker-, Execution- und Watchdog-Rollen; lokales Dashboard; Fake-/Demoabläufe; Paperledger und kontrollierte manuelle LIMIT-Aktionen; frische Alpaca-Marktevidenz für ein kleines Symboluniversum; Jev-Shadowauswertung. Risk- und Executiongrenzen sind fail-closed, LIVE ist technisch ausgeschlossen. Die vorhandene Marktaufnahme dient laufenden Sitzungen und bleibt von historischen Researchdaten getrennt.

## Umgesetzter Baustein: market-data-and-research-foundation

**Abgeschlossen und offline geprüft,9/9 Tasks:** historische SPY-/QQQ-raw-IEX-Daten, unveränderliche Revisionen/Evidenz, konservative PIT-Abfragen, Kalender-/Qualitätsbefunde, dauerhafte Jobs und eine Researchoberfläche. Getrennte Issuecommits sind in main integriert. [Ausgeführte Prüfungen und Grenzen](../evidence/mdr-implementation-verification.md). Persönliche Providerrechte bleiben unverifiziert; Standarddispatch ist deaktiviert. `point-in-time-research-backtesting` bietet sich als nächster eigenständig freizugebender Change an.

Vor Nutzung eines Datenproviders sind Lizenz, Speicherung, Weitergabe, Anpassungen, Aufbewahrung und abgeleitete Daten zu klären. Historische Überlebendenverzerrung, Look-ahead, Zeitzonen, Splits/Dividenden, verspätete Veröffentlichungen und Providerkorrekturen müssen als konkrete Risiken adressiert werden. Ohne lückenlose Herkunft und damalige Verfügbarkeit sind Backtests nicht als reproduzierbar oder PIT-gültig zu behandeln.

## Umgesetzter Baustein: point-in-time-research-backtesting

**Umgesetzt und offline geprüft:** feste SPY-/QQQ-Long/Flat-Benchmarks, beobachtungsgebundene Datensichten, deterministischer Decimal-Simulator, unabhängige chronologische Folds, persistente begrenzte Workerjobs, authentifizierte Verträge und Materialoberfläche. [Abnahme, unabhängiger Review und Grenzen](../evidence/bkt-implementation-verification.md). Standarddispatch bleibt deaktiviert; echte historische Nutzbarkeit hängt weiterhin von tatsächlich damaliger Verfügbarkeit und expliziten Kalender-/Kapitalmaßnahmenbelegen ab. Heutige Backfills können korrekt vollständig blockieren. Kein automatischer Übergang in Paper oder LIVE.

## Danach: weiterführende Research und Strategieprüfung

**Langfristig:** versionierte Strategien, lokale Replay- und Shadowvergleiche, robuste Out-of-sample-Auswertung und ein Dashboard für Quellen, Kalender, Datenqualität, Annahmen und Ergebnisse. Neue Märkte werden erst ergänzt, wenn Datenrechte, Handelskalender, Instrumentregeln, Liquidität und Risikoparameter ausreichend belegt sind. Mehr Märkte bedeuten getrennte Quellen- und Marktregeln, nicht bloß zusätzliche Symbole.

## Spätere Autonomie

Autonomie kann sich von Beobachtung über Analyse und freigegebene Simulation bis zu eng begrenztem Paperbetrieb entwickeln. Jeder Schritt braucht eigene Akzeptanzkriterien, unabhängige Risiko- und Reconciliation-Evidenz, sichtbare Betreiberkontrolle und überprüfte Credentialgrenzen. Autonomiestufe 4 und jede denkbare LIVE-Fähigkeit bleiben langfristige, gesondert zu entscheidende Ziele; sie sind keine impliziten Roadmapfreigaben. Jev bleibt bis zu einer eigenen fachlichen Neubewertung ein Shadow-System. LangGraph ist derzeit nicht vorgesehen.

Verbindliche Aufgaben und Abhängigkeiten werden in OpenSpec geführt; diese Übersicht wird bei wesentlichen Änderungen daran angepasst. Siehe [Marktevidenz-Spezifikation](../../openspec/specs/market-evidence/spec.md), [Implementierungsnachweise](../evidence/) und [Git-/Change-Prozess](../development/codex-tooling.md).
