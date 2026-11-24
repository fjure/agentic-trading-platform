# IEF-08 — Personal Cockpit und Activity Timeline

## Ziel und Business Value

Als Nutzer möchte ich Portfolio, Erkenntnisse und offene Entscheidungen auf einer übersichtlichen Startseite erreichen. Das Paket liefert einen eigenständig überprüfbaren vertikalen Schritt statt technischer Platzhalter.

## User Story

Als Nutzer möchte ich Portfolio, Erkenntnisse und offene Entscheidungen auf einer übersichtlichen Startseite erreichen.

## Fachliche Requirements

IEF-C01, IEF-C02, IEF-C03. Normative Szenarien stehen in den Capability-Deltas; sechs Journeys in user-journeys.md.

## Technische Requirements und Komponenten

Gemeinsame Navigation, zehn Screens, Dashboardkarten und quellengebundene Timeline mit Cursor/Filter, progressive Details, Legacylinks und direkte Betriebskontrollen. Keine clientseitige Fachaggregation.

Zielpfade: `apps/dashboard/src/app; services/control_api; services/runtime; services/control_api/migrations/versions`. Additive neue Dateien liegen innerhalb dieser Komponenten; vorhandene generierte Dateien werden ausschließlich regeneriert.

## Dependencies

Depends on: IEF-02, IEF-03, IEF-04, IEF-05, IEF-06, IEF-07.
ARP-10 ist fremdes abgeschlossenes Issue #105; technische Readiness auf0442f29 belegt, native Provenienzkante bleibt. Nicht durch IEF verändern. Siehe issue-graph.md.

## Akzeptanzkriterien und Positivtests

J1-J6 Übergänge mit 360/768/1440px, Keyboard/Screenreaderlabels/Fokus und 200% Zoom; leere/stale/offline/loading/conflict states und Back/Forward/Reload; Timeline reagiert auf Cursorablauf ohne stille Lücke.

## Negativtests und Sicherheitsanforderungen

Kein Dashboardmockwert bei Ausfall; keine doppelte Aktivität oder versteckte Wirkung durch Navigation; Kontextwechsel mischt keine Werte, Authverlust startet nichts.

## UI-/UX-Kriterien

Geltende Screenverträge aus ux-architecture.md und navigation-design.md: deutsche klare Sprache, Herkunft und letzte bestätigte Zeit, Progressive Disclosure, Text zusätzlich zu Statusfarbe, zugängliche Labels/Fokus/Fehler, mobile Karten ohne horizontale Primärnavigation. Loading/leer/offline/stale/conflict werden am Paket selbst getestet. Backendpakete liefern die hierfür erforderlichen expliziten Zustände im Vertrag.

## Verbindliche Refinement-Abnahme

IEF-T-C06/C07. Den technisch geprüften IEF-01-Flow in allen zehn Screens umsetzen und Abweichungen begründen. Desktop/Mobile, Tastatur, Screenreader, Progressive Disclosure, vier klare Wirkungsarten und sichere Fehlerfolgen gegen die Prototypfassung prüfen. Die menschliche Gesamtfreigabe erfolgt in IEF-09.

## Evidenznachweis und Definition of Done

Eigene Component-/API-/SQL-/Securityprüfungen und betroffene Regressionen mit Quell-SHA, Kommando, Ergebnis und unverändertem Safetyumfang protokollieren; Pflichtszenarien aus traceability.md zuordnen. Änderungen am OAS mit Beispielen/Breaking-Diff/zweifacher Clientgenerierung prüfen. Abhängigkeiten tatsächlich erfüllt, unabhängiger begrenzter Review ohne offene kritische Befunde, native Issuekanten und Managedblock vollständig zurückgelesen. Paketdokumentation und Recoveryhinweise gehören zum selben Stand. Keine echten Provideraufrufe, Brokerorders oder neue Rechte. Erst dann Checkbox und zugehöriges Issue gemeinsam abschließen; eigener geprüfter Issuebranch und Integration nach Repositoryworkflow.

## OpenSpec-Referenz

`openspec/changes/investment-experience-foundation/work-packages/IEF-08.md`; design.md und zugehörige Capability-Deltas.

## Apply-Status

Nicht begonnen. Es gelten die technischen Voraussetzungen aus dem Dependencygraph. Die menschliche Abnahme findet in IEF-09 statt.

Prüfevidenz, konkreter Dependencyblocker und Wiederanlauf: `docs/evidence/ief-apply-status.md`. Keine Änderung normativer Anforderungen oder Abnahmeschwellen. Issue und Taskcheckbox bleiben bis zur vollständigen Definition of Done offen.
