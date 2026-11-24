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

IEF-T-C06, IEF-T-O05 und IEF-T-C08. Die formative Prototypprüfung, die funktionale Onboardingprüfung und die integrierte Abnahme gemeinsam in IEF-09 durchführen und separat auswerten. Je Prüfung mindestens fünf fachfremde Personen gemäß ux-validation.md. Für die integrierte Prüfung frische Personen einsetzen. Alle festgelegten Zeitgrenzen, Erfolgsquoten, 100 Prozent sichere Entscheidungen sowie Korrektur und menschliche Nachtests bleiben verpflichtend. Technische CI und menschliche Ergebnisse separat protokollieren.

## Evidenznachweis und Definition of Done

Eigene Component-/API-/SQL-/Securityprüfungen und betroffene Regressionen mit Quell-SHA, Kommando, Ergebnis und unverändertem Safetyumfang protokollieren; Pflichtszenarien aus traceability.md zuordnen. Änderungen am OAS mit Beispielen/Breaking-Diff/zweifacher Clientgenerierung prüfen. Abhängigkeiten tatsächlich erfüllt, unabhängiger begrenzter Review ohne offene kritische Befunde, native Issuekanten und Managedblock vollständig zurückgelesen. Paketdokumentation und Recoveryhinweise gehören zum selben Stand. Keine echten Provideraufrufe, Brokerorders oder neue Rechte. Erst dann Checkbox und zugehöriges Issue gemeinsam abschließen; eigener geprüfter Issuebranch und Integration nach Repositoryworkflow.

## OpenSpec-Referenz

`openspec/changes/investment-experience-foundation/work-packages/IEF-09.md`; design.md und zugehörige Capability-Deltas.

## Apply-Status

Nicht begonnen. IEF-01–08 sind technische Voraussetzungen. Die drei menschlichen Prüfungen sind vor Abschluss von IEF-09 durchzuführen (HUMAN_UX_VALIDATION_PENDING).

Prüfevidenz, konkreter Dependencyblocker und Wiederanlauf: `docs/evidence/ief-apply-status.md`. Keine Änderung normativer Anforderungen oder Abnahmeschwellen. Issue und Taskcheckbox bleiben bis zur vollständigen Definition of Done offen.
