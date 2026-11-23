# IEF-01 — Experience Architecture und API Contracts

## Ziel und Business Value

Als Betreiber möchte ich konsistente Ansichten und stabile Links, damit alle Produktwege dieselben Objekte verwenden. Das Paket liefert einen eigenständig überprüfbaren vertikalen Schritt statt technischer Platzhalter.

## User Story

Als Betreiber möchte ich konsistente Ansichten und stabile Links, damit alle Produktwege dieselben Objekte verwenden.

## Fachliche Requirements

IEF-C01, IEF-P01, IEF-A01, IEF-G01. Normative Szenarien stehen in den Capability-Deltas; sechs Journeys in user-journeys.md.

## Technische Requirements und Komponenten

Additive Experience- und Previewverträge aus design.md D1-D6, Schemas/Problemcodes/Beispiele/Operationenmatrix, gepinnter Angular Router, Shell-Routebasis und sichere Legacy-Linkadapter. Neue zweckdiskriminierte Portfolio-Processing-Preview-/Decision-/Status-/Widerrufsschemas sowie lokale portfolio-analyses POST/GET explizit aufnehmen. Vertrag zuerst, keine Stub-Routen zum Vortäuschen von Fähigkeiten.

Zielpfade: `services/control_api; contracts/http/control-api.openapi.yaml; apps/dashboard/package.json; apps/dashboard/src/app; scripts/validate-contracts.py`. Additive neue Dateien liegen innerhalb dieser Komponenten; vorhandene generierte Dateien werden ausschließlich regeneriert.

## Dependencies

Depends on: keine.
ARP-10 ist fremdes abgeschlossenes Issue #105; technische Readiness auf0442f29 belegt, native Provenienzkante bleibt. Nicht durch IEF verändern. Siehe issue-graph.md.

## Akzeptanzkriterien und Positivtests

OAS/Beispiele und Breaking-Diff bestehen; Client zweimal bytegleich generiert und TypeScript geprüft; Routerbuild und Legacy-ID-Erhalt ohne Mutation belegt. Routenparität erst mit allen Implementierungen in IEF-09 vollständig, Zwischenstand explizit offen.

## Negativtests und Sicherheitsanforderungen

Unbekannte Felder, ungültige Decimalstrings/UTC, freie URLs, fremde IDs und alte Linkbindungen werden sicher abgelehnt. Keine Broker-/Providerwirkung durch Navigation.

## UI-/UX-Kriterien

Geltende Screenverträge aus ux-architecture.md und navigation-design.md: deutsche klare Sprache, Herkunft und letzte bestätigte Zeit, Progressive Disclosure, Text zusätzlich zu Statusfarbe, zugängliche Labels/Fokus/Fehler, mobile Karten ohne horizontale Primärnavigation. Loading/leer/offline/stale/conflict werden am Paket selbst getestet. Backendpakete liefern die hierfür erforderlichen expliziten Zustände im Vertrag.

## Verbindliche Refinement-Abnahme

IEF-C01/C02 und IEF-T-C06. Vor Übergabe an Featurepakete einen zusammenhängenden klickbaren Prototyp aller zehn Screens und J1–J6 samt Screen-Flow, Informationsarchitektur, Material-Tokens, Fokuspfaden und Sperrzuständen liefern. Desktop und Mobile prüfen. Mindestens fünf fachfremde Personen durchlaufen die formative Prüfung gemäß ux-validation.md. Befunde, Revision und Freigabe der Prototypfassung dokumentieren. Die integrierte Abnahme bleibt IEF-09.

## Evidenznachweis und Definition of Done

Eigene Component-/API-/SQL-/Securityprüfungen und betroffene Regressionen mit Quell-SHA, Kommando, Ergebnis und unverändertem Safetyumfang protokollieren; Pflichtszenarien aus traceability.md zuordnen. Änderungen am OAS mit Beispielen/Breaking-Diff/zweifacher Clientgenerierung prüfen. Abhängigkeiten tatsächlich erfüllt, unabhängiger begrenzter Review ohne offene kritische Befunde, native Issuekanten und Managedblock vollständig zurückgelesen. Paketdokumentation und Recoveryhinweise gehören zum selben Stand. Keine echten Provideraufrufe, Brokerorders oder neue Rechte. Erst dann Checkbox und zugehöriges Issue gemeinsam abschließen; eigener geprüfter Issuebranch und Integration nach Repositoryworkflow.

## OpenSpec-Referenz

`openspec/changes/investment-experience-foundation/work-packages/IEF-01.md`; design.md und zugehörige Capability-Deltas.
