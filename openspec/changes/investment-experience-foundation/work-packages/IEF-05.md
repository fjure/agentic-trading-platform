# IEF-05 — Strategy Discovery

## Ziel und Business Value

Als Anleger möchte ich Strategien verstehen und belegte Resultate fair vergleichen. Das Paket liefert einen eigenständig überprüfbaren vertikalen Schritt statt technischer Platzhalter.

## User Story

Als Anleger möchte ich Strategien verstehen und belegte Resultate fair vergleichen.

## Fachliche Requirements

IEF-S01, IEF-S02, IEF-S03. Normative Szenarien stehen in den Capability-Deltas; sechs Journeys in user-journeys.md.

## Technische Requirements und Komponenten

Versionierte Registerprojektion mit verständlichen Karten/Details, Forschungsstand und separater Papereligibility, Ergebnisreferenzen und Kompatibilitätsmatrix. Bestehende Quant-/Companionpfade für Vorbereitung verwenden.

Zielpfade: `packages/backtesting; services/control_api/quant_lab_routes.py; apps/dashboard/src/app/research`. Additive neue Dateien liegen innerhalb dieser Komponenten; vorhandene generierte Dateien werden ausschließlich regeneriert.

## Dependencies

Depends on: IEF-01.
ARP-10 ist fremdes abgeschlossenes Issue #105; technische Readiness auf0442f29 belegt, native Provenienzkante bleibt. Nicht durch IEF verändern. Siehe issue-graph.md.

## Akzeptanzkriterien und Positivtests

J3 Registrykarten und negative Resultate verständlich; zwei kompatible Resultate nebeneinander, inkompatible Modi/Zeiträume/Revisions-/Kostenstände gekennzeichnet; keine Strategie wird aus einer Idee ausführbar.

## Negativtests und Sicherheitsanforderungen

Kein Ranking ohne gleiche Basis, keine automatische Promotion oder erwartete Rendite; unsupported Strategie/Widerruf/Version unavailable sicher erklärt; keine RSI-Ersatzstrategie für freien Auftrag.

## UI-/UX-Kriterien

Geltende Screenverträge aus ux-architecture.md und navigation-design.md: deutsche klare Sprache, Herkunft und letzte bestätigte Zeit, Progressive Disclosure, Text zusätzlich zu Statusfarbe, zugängliche Labels/Fokus/Fehler, mobile Karten ohne horizontale Primärnavigation. Loading/leer/offline/stale/conflict werden am Paket selbst getestet. Backendpakete liefern die hierfür erforderlichen expliziten Zustände im Vertrag.

## Verbindliche Refinement-Abnahme

IEF-T-S06/S07. Fundstelle, Funktionsweise und historische Evidenz mit Zeitraum/Revision vor separater aktueller serverseitiger Eignungsprüfung zeigen. Vorhandene Paper-Konfiguration und Capability müssen konkrete Version und Symbol abdecken, sonst bleibt der Weg gesperrt. Positivfall mit explizit kompatibler synthetischer Fixture. Weitergabe ausschließlich des versionierten Strategie-/Instrument-/Evidenz-/Eignungskontexts an IEF-07, keine Orderparameter aus Historie oder KI. Bei fehlender oder geänderter Bindung Weiterweg sperren und Grund/Rückweg zeigen.

## Evidenznachweis und Definition of Done

Eigene Component-/API-/SQL-/Securityprüfungen und betroffene Regressionen mit Quell-SHA, Kommando, Ergebnis und unverändertem Safetyumfang protokollieren; Pflichtszenarien aus traceability.md zuordnen. Änderungen am OAS mit Beispielen/Breaking-Diff/zweifacher Clientgenerierung prüfen. Abhängigkeiten tatsächlich erfüllt, unabhängiger begrenzter Review ohne offene kritische Befunde, native Issuekanten und Managedblock vollständig zurückgelesen. Paketdokumentation und Recoveryhinweise gehören zum selben Stand. Keine echten Provideraufrufe, Brokerorders oder neue Rechte. Erst dann Checkbox und zugehöriges Issue gemeinsam abschließen; eigener geprüfter Issuebranch und Integration nach Repositoryworkflow.

## OpenSpec-Referenz

`openspec/changes/investment-experience-foundation/work-packages/IEF-05.md`; design.md und zugehörige Capability-Deltas.

## Apply-Status

Nicht begonnen. Es gelten die technischen Voraussetzungen aus dem Dependencygraph. Die menschliche Abnahme findet in IEF-09 statt.

Prüfevidenz, konkreter Dependencyblocker und Wiederanlauf: `docs/evidence/ief-apply-status.md`. Keine Änderung normativer Anforderungen oder Abnahmeschwellen. Issue und Taskcheckbox bleiben bis zur vollständigen Definition of Done offen.
