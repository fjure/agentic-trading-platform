# IEF-06 — AI Task Center

## Ziel und Business Value

Als Nutzer möchte ich eine Frage stellen, Grenzen prüfen und Auftrag sowie Ergebnis wiederfinden. Das Paket liefert einen eigenständig überprüfbaren vertikalen Schritt statt technischer Platzhalter.

## User Story

Als Nutzer möchte ich eine Frage stellen, Grenzen prüfen und Auftrag sowie Ergebnis wiederfinden.

## Fachliche Requirements

IEF-A01, IEF-A02, IEF-A03, IEF-A04. Normative Szenarien stehen in den Capability-Deltas; sechs Journeys in user-journeys.md.

## Technische Requirements und Komponenten

Runprojektion und versionierte Intentmetadaten, lokale Analysequelle aus IEF-04, actor-scoped Listen/Details, alle echten RunStates, konkrete allowedActions; vorhandene Dialog-/Ideen-/Cancel-/Approvalpfade und Budgets/Fencing wiederverwenden. Keine neue Runtime oder generischer execute.

Zielpfade: `packages/agent_research; services/runtime/agent_status.py; services/control_api; apps/dashboard/src/app/research`. Additive neue Dateien liegen innerhalb dieser Komponenten; vorhandene generierte Dateien werden ausschließlich regeneriert.

## Dependencies

Depends on: IEF-01, IEF-04, ARP-10.
ARP-10 ist fremdes abgeschlossenes Issue #105; technische Readiness auf0442f29 belegt, native Provenienzkante bleibt. Nicht durch IEF verändern. Siehe issue-graph.md.

## Akzeptanzkriterien und Positivtests

J4 Absicht/Rückfragen/Testbarkeit und gesonderte Freigaben bis Report; J1 lokaler Analyseauftrag auffindbar; 13 Zustände und unknown output geprüft, Reload/Abbruch/Frist/Checkpoint mit vorhandener Runtime. ARP-10 vor produktiver Researchintegration.

## Negativtests und Sicherheitsanforderungen

Nicht implementierte Rollen sind nicht ausführbar; Auftraganlage ohne Providerkosten; stale grants, Cancel/in-flight, unbekannte Usage und alte Fence ohne erneuten Versand; keine Finanzwrites.

## UI-/UX-Kriterien

Geltende Screenverträge aus ux-architecture.md und navigation-design.md: deutsche klare Sprache, Herkunft und letzte bestätigte Zeit, Progressive Disclosure, Text zusätzlich zu Statusfarbe, zugängliche Labels/Fokus/Fehler, mobile Karten ohne horizontale Primärnavigation. Loading/leer/offline/stale/conflict werden am Paket selbst getestet. Backendpakete liefern die hierfür erforderlichen expliziten Zustände im Vertrag.

## Evidenznachweis und Definition of Done

Eigene Component-/API-/SQL-/Securityprüfungen und betroffene Regressionen mit Quell-SHA, Kommando, Ergebnis und unverändertem Safetyumfang protokollieren; Pflichtszenarien aus traceability.md zuordnen. Änderungen am OAS mit Beispielen/Breaking-Diff/zweifacher Clientgenerierung prüfen. Abhängigkeiten tatsächlich erfüllt, unabhängiger begrenzter Review ohne offene kritische Befunde, native Issuekanten und Managedblock vollständig zurückgelesen. Paketdokumentation und Recoveryhinweise gehören zum selben Stand. Keine echten Provideraufrufe, Brokerorders oder neue Rechte. Erst dann Checkbox und zugehöriges Issue gemeinsam abschließen; eigener geprüfter Issuebranch und Integration nach Repositoryworkflow.

## OpenSpec-Referenz

`openspec/changes/investment-experience-foundation/work-packages/IEF-06.md`; design.md und zugehörige Capability-Deltas.

## Apply-Status

Nicht begonnen. Es gelten die technischen Voraussetzungen aus dem Dependencygraph. Die menschliche Abnahme findet in IEF-09 statt.

Prüfevidenz, konkreter Dependencyblocker und Wiederanlauf: `docs/evidence/ief-apply-status.md`. Keine Änderung normativer Anforderungen oder Abnahmeschwellen. Issue und Taskcheckbox bleiben bis zur vollständigen Definition of Done offen.
