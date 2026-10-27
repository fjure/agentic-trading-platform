# Discovery

Stand 27.10.2026, lokaler HEAD und remote main lesend identisch: `5bffd2488ad3b42b3a2284c75d9a6ff4db16437d`. Branch zu Beginn main; vorhandene fremde Änderungen `README.md` und `apps/dashboard/angular.json` werden weder geändert noch gestaged. GitHubinventar: Issues #1..#68 geschlossen, keine offenen bestehenden Taskissues. Offizieller GitHubconnector bestätigt Zielrepository fjure/agentic-trading-platform; GH CLI 2.100.0 bestätigt Login fjure. Keine Credentials gelesen.

## Nachgewiesene Basis

| Komponente | Gelesene Implementierung / Evidenz | Konsequenz |
|---|---|---|
| Datenidentität/Manifeste | packages/backtesting/models.py | bestehende config/hash-/Decimal-/Foldregeln erhalten |
| Snapshot und PIT | packages/backtesting/dataset.py | versionierter Commitstand, keyset pages, separate Signal-/Outcomesweeps wiederverwenden |
| Readiness | packages/backtesting/readiness.py, coverage.py, calendar.py | historische Sichtbarkeit in mehreren Schichten; eigener Kontext erforderlich |
| Simulation/Evaluation | simulator.py, evaluation.py, strategies.py | gemeinsame Arithmetik, registrierte drei Strategien, getrennte Outcomes; keine Timestampmanipulation |
| Dauerhafte Jobs | packages/backtesting/jobs.py, services/worker/backtesting.py | Leases/Fencing/atomare Publikation, Revision-/Publikationsbudgets erhalten |
| Runtime/SQL | services/runtime/backtesting.py, Migrationen 0028/0029 und folgende | Worker nur lokale Research-Reads und eigene Ergebnisse, kein Netz-/Finanzzugriff |
| HTTP/UI | services/control_api/backtest_routes.py, backtest_dto.py, backtest_service.py; contracts/http/control-api.openapi.yaml; apps/dashboard/src/app/research/ | additive Requests/Projektionen und vorhandene Seiten/Client erweitern |
| Offlineabnahme | docs/evidence/bkt-implementation-verification.md, rdr-implementation-verification.md, mdr-implementation-verification.md | dokumentierte echte PG-/HTTP-/Browser-/ACLtests auf synthetischen Quellen; hier nicht als erneut ausgeführt dargestellt |

Die Vision ist langfristig; ihre älteren Abschnitte nennen Research teils noch geplant. Roadmap und konkrete Implementierung/Evidenz weisen MDR/BKT/RDR als offline implementiert aus. Das beweist keine realen Rechte, historische Eignung oder Profitabilität. Sammlung global deaktiviert, Trustregister leer; kein Aktivierungsauftrag.

## Erforderliche spätere Konsolidierung

`openspec list --json` meldet sechs abgeschlossene, nicht archivierte Changes: paper-trading-vertical-slice, dashboard-trading-demo, alpaca-paper-workflow, market-data-and-research-foundation, point-in-time-research-backtesting, research-data-readiness. Nicht automatisch archivieren.

`openspec/specs/research-backtesting/spec.md` und `openspec/specs/research-data-readiness/spec.md` fehlen. Main `historical-market-data` enthält nur REQ-HMD-001..006; RDR-Deltas ergänzen 007..010. Verbindliche Planungsbasis sind deshalb zusätzlich die vollständigen abgeschlossenen BKT-/RDR-Spec-Deltas unter ihren Changeverzeichnissen, nicht nur das Maininventar. Ältere HMD-/Researchkontextdeltas ebenfalls vor späterer Konsolidierung vollständig vergleichen. Keine Anforderung dieses neuen Changes überschreibt die Strict-Anforderungen REQ-BKT-001..008 oder REQ-RDR-001..008.

Empfehlung: eigener freigegebener Sync-/Archive-Durchlauf mit vollständigem Delta-/Mainvergleich und unveränderten Requirement-IDs. Für diesen Propose ist die explizite überlagerte Basis ausreichend; automatische Archivierung des neuen Changes ohne diese Basisprüfung wäre unzulässig. Diese Konsolidierung ist kein verstecktes Implementierungsarbeitspaket und erhält hier kein dupliziertes Taskissue.
