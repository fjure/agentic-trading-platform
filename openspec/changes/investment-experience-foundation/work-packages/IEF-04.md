# IEF-04 — Portfolio Insights und begrenzte Erklärung

## Ziel und Business Value

Als Anleger möchte ich verifizierbare Befunde verstehen und eine zusätzliche KI-Erklärung selbst freigeben. Das Paket liefert einen eigenständig überprüfbaren vertikalen Schritt statt technischer Platzhalter.

## User Story

Als Anleger möchte ich verifizierbare Befunde verstehen und eine zusätzliche KI-Erklärung selbst freigeben.

## Fachliche Requirements

IEF-P03, IEF-P04, IEF-A01. Normative Szenarien stehen in den Capability-Deltas; sechs Journeys in user-journeys.md.

## Technische Requirements und Komponenten

Lokale begrenzte synchrone Analysen mit POST/GET aus D3 persistieren Methoden-/Snapshotreferenz, keine zweite Queue. Qualitätsbefunde sofort, Gewichte nur bei ausreichender Bewertungsbasis. Neuer Zweck PORTFOLIO_EXPLANATION_V1 mit minimiertem Projektor, eigener Policy/Rollen-/Schema-/Budgetbindung und Gatewayvalidator im vorhandenen Supervisor; kein Hypothesenkanal-Missbrauch. Vorhandene Processing-Preview/Decision/Status/Widerruf diskriminierend erweitern; Purpose-/RunKind-Enums und SQL-Constraints additiv migrieren, alte Hypothesenschemas unverändert erhalten.

Zielpfade: `services/control_api; packages/agent_research; services/runtime; services/llm_gateway; apps/dashboard/src/app`. Additive neue Dateien liegen innerhalb dieser Komponenten; vorhandene generierte Dateien werden ausschließlich regeneriert.

## Dependencies

Depends on: IEF-03, ARP-10.
ARP-10 ist fremdes abgeschlossenes Issue #105; technische Readiness auf0442f29 belegt, native Provenienzkante bleibt. Nicht durch IEF verändern. Siehe issue-graph.md.

## Akzeptanzkriterien und Positivtests

J1/J2 lokales Ergebnis ohne Provider; Inputrevisions-Reproduktion und nullable Konzentration geprüft; jede externe Erklärung nur nach konkreter Daten- und Kostenentscheidung; unreferenzierte Finanzclaims scheitern. ARP-10 Evidenz vor produktiver Integration.

## Negativtests und Sicherheitsanforderungen

Fehlende ETFholdings, FX, Gebühren/Preis/Nenner oder unbekannte Kosten erfinden keinen Befund; Widerruf/alte Fence/Promptinjection/Versionwechsel sperren Versand oder Ergebnisannahme.

## UI-/UX-Kriterien

Geltende Screenverträge aus ux-architecture.md und navigation-design.md: deutsche klare Sprache, Herkunft und letzte bestätigte Zeit, Progressive Disclosure, Text zusätzlich zu Statusfarbe, zugängliche Labels/Fokus/Fehler, mobile Karten ohne horizontale Primärnavigation. Loading/leer/offline/stale/conflict werden am Paket selbst getestet. Backendpakete liefern die hierfür erforderlichen expliziten Zustände im Vertrag.

## Evidenznachweis und Definition of Done

Eigene Component-/API-/SQL-/Securityprüfungen und betroffene Regressionen mit Quell-SHA, Kommando, Ergebnis und unverändertem Safetyumfang protokollieren; Pflichtszenarien aus traceability.md zuordnen. Änderungen am OAS mit Beispielen/Breaking-Diff/zweifacher Clientgenerierung prüfen. Abhängigkeiten tatsächlich erfüllt, unabhängiger begrenzter Review ohne offene kritische Befunde, native Issuekanten und Managedblock vollständig zurückgelesen. Paketdokumentation und Recoveryhinweise gehören zum selben Stand. Keine echten Provideraufrufe, Brokerorders oder neue Rechte. Erst dann Checkbox und zugehöriges Issue gemeinsam abschließen; eigener geprüfter Issuebranch und Integration nach Repositoryworkflow.

## OpenSpec-Referenz

`openspec/changes/investment-experience-foundation/work-packages/IEF-04.md`; design.md und zugehörige Capability-Deltas.
