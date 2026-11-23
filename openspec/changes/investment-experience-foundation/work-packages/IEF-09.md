# IEF-09 — Integrated Experience Acceptance

## Ziel und Business Value

Als Betreiber möchte ich nachvollziehbare Gesamtnachweise vor einer produktiven Experiencefreigabe. Das Paket liefert einen eigenständig überprüfbaren vertikalen Schritt statt technischer Platzhalter.

## User Story

Als Betreiber möchte ich nachvollziehbare Gesamtnachweise vor einer produktiven Experiencefreigabe.

## Fachliche Requirements

IEF-C04. Normative Szenarien stehen in den Capability-Deltas; sechs Journeys in user-journeys.md.

## Technische Requirements und Komponenten

Sechs vollständige Journeys mit echtem Angularbuild/API/tempPG/Research/Quant und synthetischen Provider-/Paperdoubles, verbindliche CIauswahl, Manifeste, Deployment/Rollback/Recovery und unabhängige Reviews. Kein erneutes Sammeln zuvor geschuldeter Pakettests.

Zielpfade: `.github/workflows; scripts; tests/e2e; tests/integration; tests/security; docs/evidence; docs/runbooks`. Additive neue Dateien liegen innerhalb dieser Komponenten; vorhandene generierte Dateien werden ausschließlich regeneriert.

## Dependencies

Depends on: IEF-01, IEF-02, IEF-03, IEF-04, IEF-05, IEF-06, IEF-07, IEF-08, ARP-10.
ARP-10 ist fremdes abgeschlossenes Issue #105; technische Readiness auf0442f29 belegt, native Provenienzkante bleibt. Nicht durch IEF verändern. Siehe issue-graph.md.

## Akzeptanzkriterien und Positivtests

Alle J1-J6 Pflichtfälle inkl positiver kontrollierter Research- und Paperharnesskette; Securitynegative/Contractparität/Clientdrift/Issuegraph/CI grün, ARP-10 Nachweise verknüpft. Echte Operatorprobe und modellgebundene Fachfreigabe getrennt unverifiziert lassen, falls nicht autorisiert vorhanden.

## Negativtests und Sicherheitsanforderungen

Keine übersprungenen Pflichtfälle als bestanden; kein echter Provider/Brokerkontakt, keine Produktionsdaten/Secrets; fehlgeschlagene IEF-Contracts, fehlende ARP-Readiness oder Issue-Drift blockieren Abschluss.

## UI-/UX-Kriterien

Geltende Screenverträge aus ux-architecture.md und navigation-design.md: deutsche klare Sprache, Herkunft und letzte bestätigte Zeit, Progressive Disclosure, Text zusätzlich zu Statusfarbe, zugängliche Labels/Fokus/Fehler, mobile Karten ohne horizontale Primärnavigation. Loading/leer/offline/stale/conflict werden am Paket selbst getestet. Backendpakete liefern die hierfür erforderlichen expliziten Zustände im Vertrag.

## Verbindliche Refinement-Abnahme

IEF-T-C08. Mindestens fünf fachfremde Personen am integrierten Build in allen sechs Journeys prüfen. Je Aufgabe und Begriffsfrage mindestens 80 Prozent, Sicherheitsfälle 100 Prozent. Kritische Fehlbedienung oder Kernwegsperre blockiert bis zum menschlichen Nachtest. Technische CI, Prototypfreigabe und Nutzernachweis separat protokollieren.

## Evidenznachweis und Definition of Done

Eigene Component-/API-/SQL-/Securityprüfungen und betroffene Regressionen mit Quell-SHA, Kommando, Ergebnis und unverändertem Safetyumfang protokollieren; Pflichtszenarien aus traceability.md zuordnen. Änderungen am OAS mit Beispielen/Breaking-Diff/zweifacher Clientgenerierung prüfen. Abhängigkeiten tatsächlich erfüllt, unabhängiger begrenzter Review ohne offene kritische Befunde, native Issuekanten und Managedblock vollständig zurückgelesen. Paketdokumentation und Recoveryhinweise gehören zum selben Stand. Keine echten Provideraufrufe, Brokerorders oder neue Rechte. Erst dann Checkbox und zugehöriges Issue gemeinsam abschließen; eigener geprüfter Issuebranch und Integration nach Repositoryworkflow.

## OpenSpec-Referenz

`openspec/changes/investment-experience-foundation/work-packages/IEF-09.md`; design.md und zugehörige Capability-Deltas.
