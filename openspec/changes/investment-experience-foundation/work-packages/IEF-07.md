# IEF-07 — Guided Paper Order

## Ziel und Business Value

Als Betreiber möchte ich eine zulässige Paperaktion vor Bestätigung verstehen und ihren tatsächlichen Ausgang sehen. Das Paket liefert einen eigenständig überprüfbaren vertikalen Schritt statt technischer Platzhalter.

## User Story

Als Betreiber möchte ich eine zulässige Paperaktion vor Bestätigung verstehen und ihren tatsächlichen Ausgang sehen.

## Fachliche Requirements

IEF-G01, IEF-G02, IEF-G03. Normative Szenarien stehen in den Capability-Deltas; sechs Journeys in user-journeys.md.

## Technische Requirements und Komponenten

Gebundene additive Preview und Einmalcommit in bestehenden Human-Anforderungspfad, exakte LIMIT/DAY/REGULAR Terms, fehlende obligatorische Gategrundlagen blockieren; optionale unbekannte Gewichte bleiben unverfügbar, getrennte Bestätigung/Operation/Order/Fill/Reconciliationansichten. BUY Nachkaufsperre und bestehende Gates bleiben erhalten.

Zielpfade: `services/control_api/paper_routes.py; services/runtime/paper_manual.py; services/worker/paper_human_limit.py; packages/risk; services/execution; apps/dashboard/src/app/paper-workflow`. Additive neue Dateien liegen innerhalb dieser Komponenten; vorhandene generierte Dateien werden ausschließlich regeneriert.

## Dependencies

Depends on: IEF-01, IEF-03, IEF-05.
ARP-10 ist fremdes abgeschlossenes Issue #105; technische Readiness auf0442f29 belegt, native Provenienzkante bleibt. Nicht durch IEF verändern. Siehe issue-graph.md.

## Akzeptanzkriterien und Positivtests

Positivfall: fehlende optionale Gewichte allein blockieren keine sonst gültige Vorschau. J5 lokale Harnesskette Preview/Risk/Confirm/Operation/Fill/Audit sowie Doppelklick und zwei Schlüssel auf gleiche Preview; stale quote/policy/portfolio/rotation verhindern Commit; Statusabfrage nach Antwortverlust.

## Negativtests und Sicherheitsanforderungen

Kein Broker-I/O in Preview, kein LLMsubmit/LIVE/Market/Short; SELL kein garantierter Nettoerlös; UNKNOWN hält Reserve und verhindert Resubmit; Cancel/Fillrennen und Pause/Kill bestehen.

## UI-/UX-Kriterien

Geltende Screenverträge aus ux-architecture.md und navigation-design.md: deutsche klare Sprache, Herkunft und letzte bestätigte Zeit, Progressive Disclosure, Text zusätzlich zu Statusfarbe, zugängliche Labels/Fokus/Fehler, mobile Karten ohne horizontale Primärnavigation. Loading/leer/offline/stale/conflict werden am Paket selbst getestet. Backendpakete liefern die hierfür erforderlichen expliziten Zustände im Vertrag.

## Evidenznachweis und Definition of Done

Eigene Component-/API-/SQL-/Securityprüfungen und betroffene Regressionen mit Quell-SHA, Kommando, Ergebnis und unverändertem Safetyumfang protokollieren; Pflichtszenarien aus traceability.md zuordnen. Änderungen am OAS mit Beispielen/Breaking-Diff/zweifacher Clientgenerierung prüfen. Abhängigkeiten tatsächlich erfüllt, unabhängiger begrenzter Review ohne offene kritische Befunde, native Issuekanten und Managedblock vollständig zurückgelesen. Paketdokumentation und Recoveryhinweise gehören zum selben Stand. Keine echten Provideraufrufe, Brokerorders oder neue Rechte. Erst dann Checkbox und zugehöriges Issue gemeinsam abschließen; eigener geprüfter Issuebranch und Integration nach Repositoryworkflow.

## OpenSpec-Referenz

`openspec/changes/investment-experience-foundation/work-packages/IEF-07.md`; design.md und zugehörige Capability-Deltas.
