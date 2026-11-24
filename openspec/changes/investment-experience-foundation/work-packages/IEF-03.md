# IEF-03 — Portfolio Hub

## Ziel und Business Value

Als Anleger möchte ich Cash, Positionen und Datenlücken mit belastbarer Herkunft verstehen. Das Paket liefert einen eigenständig überprüfbaren vertikalen Schritt statt technischer Platzhalter.

## User Story

Als Anleger möchte ich Cash, Positionen und Datenlücken mit belastbarer Herkunft verstehen.

## Fachliche Requirements

IEF-P01, IEF-P02. Normative Szenarien stehen in den Capability-Deltas; sechs Journeys in user-journeys.md.

## Technische Requirements und Komponenten

Readports für DEMO/FAKE/ALPACA_PAPER, konsistente snapshots und Cursor, MetricValue mit nullable Wert/Einheit/Reason/Methode/Zeiten; Portfolio- und Positionsansicht mit sicheren Leer- und Stalezuständen. Keine neue Finanzwahrheit oder historische Bewertungsengine.

Zielpfade: `services/control_api; services/runtime; packages/persistence/ledger.py; apps/dashboard/src/app`. Additive neue Dateien liegen innerhalb dieser Komponenten; vorhandene generierte Dateien werden ausschließlich regeneriert.

## Dependencies

Depends on: IEF-01.
ARP-10 ist fremdes abgeschlossenes Issue #105; technische Readiness auf0442f29 belegt, native Provenienzkante bleibt. Nicht durch IEF verändern. Siehe issue-graph.md.

## Akzeptanzkriterien und Positivtests

J2 Datenstand/Quelle/Kontext sichtbar; Fillparallelität und snapshotgebundene Seiten geprüft; missing basis vs empty position vs zero unterschieden; Performance-/FX-/Gebühren-/Basislücken explizit unbekannt.

## Negativtests und Sicherheitsanforderungen

Keine aktuelle Quote als alte Portfoliobewertung; kein gemischter Ledger; keine Browserrechnung; kein GET mit Provider-I/O oder Kontoneubasierung.

## UI-/UX-Kriterien

Geltende Screenverträge aus ux-architecture.md und navigation-design.md: deutsche klare Sprache, Herkunft und letzte bestätigte Zeit, Progressive Disclosure, Text zusätzlich zu Statusfarbe, zugängliche Labels/Fokus/Fehler, mobile Karten ohne horizontale Primärnavigation. Loading/leer/offline/stale/conflict werden am Paket selbst getestet. Backendpakete liefern die hierfür erforderlichen expliziten Zustände im Vertrag.

## Evidenznachweis und Definition of Done

Eigene Component-/API-/SQL-/Securityprüfungen und betroffene Regressionen mit Quell-SHA, Kommando, Ergebnis und unverändertem Safetyumfang protokollieren; Pflichtszenarien aus traceability.md zuordnen. Änderungen am OAS mit Beispielen/Breaking-Diff/zweifacher Clientgenerierung prüfen. Abhängigkeiten tatsächlich erfüllt, unabhängiger begrenzter Review ohne offene kritische Befunde, native Issuekanten und Managedblock vollständig zurückgelesen. Paketdokumentation und Recoveryhinweise gehören zum selben Stand. Keine echten Provideraufrufe, Brokerorders oder neue Rechte. Erst dann Checkbox und zugehöriges Issue gemeinsam abschließen; eigener geprüfter Issuebranch und Integration nach Repositoryworkflow.

## OpenSpec-Referenz

`openspec/changes/investment-experience-foundation/work-packages/IEF-03.md`; design.md und zugehörige Capability-Deltas.

## Apply-Status

Nicht begonnen. Es gelten die technischen Voraussetzungen aus dem Dependencygraph. Die menschliche Abnahme findet in IEF-09 statt.

Prüfevidenz, konkreter Dependencyblocker und Wiederanlauf: `docs/evidence/ief-apply-status.md`. Keine Änderung normativer Anforderungen oder Abnahmeschwellen. Issue und Taskcheckbox bleiben bis zur vollständigen Definition of Done offen.
