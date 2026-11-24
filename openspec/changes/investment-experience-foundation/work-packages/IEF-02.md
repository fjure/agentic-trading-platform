# IEF-02 — Guided Onboarding

## Ziel und Business Value

Als Einsteiger möchte ich ohne Schlüssel zur ersten sicheren Ansicht gelangen und meine Darstellung ändern können. Das Paket liefert einen eigenständig überprüfbaren vertikalen Schritt statt technischer Platzhalter.

## User Story

Als Einsteiger möchte ich ohne Schlüssel zur ersten sicheren Ansicht gelangen und meine Darstellung ändern können.

## Fachliche Requirements

IEF-O01, IEF-O02. Normative Szenarien stehen in den Capability-Deltas; sechs Journeys in user-journeys.md.

## Technische Requirements und Komponenten

Actor-gebundene versionierte Präferenzen, strukturierte freiwillige Fragen, skip/complete/unseen, Resume/Reset und Demoauswahl; Materialstepper mit Status-/Fehleransage. Backend und UI zusammen liefern.

Zielpfade: `services/control_api; services/control_api/migrations/versions; apps/dashboard/src/app`. Additive neue Dateien liegen innerhalb dieser Komponenten; vorhandene generierte Dateien werden ausschließlich regeneriert.

## Dependencies

Depends on: IEF-01.
ARP-10 ist fremdes abgeschlossenes Issue #105; technische Readiness auf0442f29 belegt, native Provenienzkante bleibt. Nicht durch IEF verändern. Siehe issue-graph.md.

## Akzeptanzkriterien und Positivtests

J1-Onboardingteilstrecke Einstieg ohne Keys, überspringen/fortsetzen/ändern/reset nach Reload; optimistische Paralleländerung und Idempotenz geprüft; Dokumentation erklärt Präferenzen ohne Geeignetheitsbehauptung. Dieses Paket beansprucht keine vollständige Analyse-/Task-/Cockpit-Journey; IEF-T-O01/J1 werden erst nach IEF-04/06/08 in IEF-09 vollständig abgenommen.

## Negativtests und Sicherheitsanforderungen

Höhere Verlusttoleranz aktiviert keine Limits/Provider/Autonomie; Sessionverlust und Versionskonflikt erhalten sichere Daten; keine personenbezogenen Freitexte oder Browserpersistenz.

## UI-/UX-Kriterien

Geltende Screenverträge aus ux-architecture.md und navigation-design.md: deutsche klare Sprache, Herkunft und letzte bestätigte Zeit, Progressive Disclosure, Text zusätzlich zu Statusfarbe, zugängliche Labels/Fokus/Fehler, mobile Karten ohne horizontale Primärnavigation. Loading/leer/offline/stale/conflict werden am Paket selbst getestet. Backendpakete liefern die hierfür erforderlichen expliziten Zustände im Vertrag.

## Verbindliche Refinement-Abnahme

IEF-T-O05. Funktionsfähige Onboarding-Teilstrecke und Testszenario für IEF-09 bereitstellen. In IEF-09 prüfen mindestens fünf fachfremde Personen diese Teilstrecke. Mindestens 80 Prozent erreichen ohne Hilfe binnen drei Minuten das markierte Demo-Cockpit und erkennen die lokale Analyse als nächsten Schritt.

## Evidenznachweis und Definition of Done

Eigene Component-/API-/SQL-/Securityprüfungen und betroffene Regressionen mit Quell-SHA, Kommando, Ergebnis und unverändertem Safetyumfang protokollieren; Pflichtszenarien aus traceability.md zuordnen. Änderungen am OAS mit Beispielen/Breaking-Diff/zweifacher Clientgenerierung prüfen. Abhängigkeiten tatsächlich erfüllt, unabhängiger begrenzter Review ohne offene kritische Befunde, native Issuekanten und Managedblock vollständig zurückgelesen. Paketdokumentation und Recoveryhinweise gehören zum selben Stand. Keine echten Provideraufrufe, Brokerorders oder neue Rechte. Erst dann Checkbox und zugehöriges Issue gemeinsam abschließen; eigener geprüfter Issuebranch und Integration nach Repositoryworkflow.

## OpenSpec-Referenz

`openspec/changes/investment-experience-foundation/work-packages/IEF-02.md`; design.md und zugehörige Capability-Deltas.

## Apply-Status

Guided Onboarding technisch abgeschlossen und mit Merge `0f18d0291059759544fd2c7c1bc98c0d568019d8` in main integriert. [Contracts](https://github.com/fjure/agentic-trading-platform/actions/runs/45405586419) und [Research integration](https://github.com/fjure/agentic-trading-platform/actions/runs/45405586547) bestanden auf `dd482c5bbf33cfd58d6f99ae37af7fd7d34082da`. Die menschliche Onboardingprüfung findet in IEF-09 statt.

Prüfevidenz, konkreter Dependencyblocker und Wiederanlauf: `docs/evidence/ief-apply-status.md`. Keine Änderung normativer Anforderungen oder Abnahmeschwellen. Issue und Taskcheckbox bleiben bis zur vollständigen Definition of Done offen.
