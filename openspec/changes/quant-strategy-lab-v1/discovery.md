# Discovery

Stand: 30.10.2026. Lokaler HEAD und Remote-main: `379ebff7921e078969c0c255a54706f3a9b6fe5a` (RRB-08). Remote lesend mit `git ls-remote` bestätigt. Vorbestehende Änderungen: README.md, apps/dashboard/angular.json und sieben unversionierte .tooling-Verzeichnisse; nicht Teil dieses Auftrags.

## Verbindliche Basis

AGENTS.md, openspec/config.yaml, alle drei docs/vision-Dokumente, docs/development/git-workflow.md und github-cli-sync.md gelesen. Produktvision beschreibt langfristige Autonomie, erteilt keine Handelsberechtigung. Main-Specs enthalten historische Daten/Evidenz, Researchcontext, Runtime und Risk/Execution. Maßgebliche zusätzliche implementierte Deltas: market-data-and-research-foundation, point-in-time-research-backtesting, research-data-readiness und retrospective-research-backtesting, einschließlich jeweiliger Design-/Task-/Evidenzdateien. Planungsreview und technische Offlineevidenz ersetzen keine reale Marktvalidierung.

## Beobachtete Implementierung

| Bereich | Quelle / Befund | Konsequenz |
|---|---|---|
| Registry | packages/backtesting/strategies.py; drei Definitionen, version=1; models.py validiert fast/slow, exakte ints 2<=fast<slow<=100 | zentrale Metadaten und versionierte Resolver ergänzen; alte IDs und Hashpräimages erhalten |
| Warm-up | dataset.py, retrospective.py; rollendes Fenster slow, mehrfach hardcodiertes slow-1, <100 | rekursive Indikatoren brauchen neuen gebundenen Signalzustand; kein heimlich neu gesätes Rolling-EMA |
| Simulator | simulator.py; Decimal38, half-even, Geld 8 Stellen, Menge 0.000001; Signal Ende+60s, nächste rechtzeitig beginnende Fillbar; kein finales Zwangsschließen | derselbe Simulator bleibt alleiniger Fill-/Kosten-/Ledgerkern |
| Evaluation | evaluation.py; Kandidat und flat/buy-hold pro Fold; Cash/Position reset; training dient derzeit Warm-up, keiner Optimierung | neue Auswahlorchestrierung um bestehende Evaluation; kein Verketten resetter Equitykurven |
| Metrics | metrics.py; Nettorendite, Drawdown inkl. Startcash, Turnover=sum(abs(notional))/Startcash, Fees/Slippage, Counts | Bestand wiederverwenden; begrenzte neue Methodenversion |
| Speicherung | storage.py, jobs.py, retrospective_storage.py, Migrationen 0029..0034 | backtest-Schema und bestehende Run-/Resultatreferenzen erweitern, kein paralleler Resultatstore |
| Daten | Datasetports, RetrospectivePolicyV1/StrictPitPolicyV1, Reliabilityreport, Snapshot-/Revisionsbindungen | einmal bindendes Batchmanifest, keine Kandidatenselektion über latest |
| Runtime | services/worker/backtesting.py, retrospective_backtesting.py, services/runtime/backtesting.py | Dispatch standardmäßig aus, CPUauswertung ohne SQLtransaktion |
| Grenzen | 295s Arbeitsdeadline, 300s Lease, drei Versuche, eine gemeinsame aktive Lease, Queue100; 100000 Revisions-/Metadatenzeilen, 12MiB Publikation, Seite200, 366 Tage, 12 Folds im Einzelbacktest | Batch darf diese Grenzen nicht pro Kandidat vervielfachen |
| Container | infra/compose/compose.yaml; 0.5 CPU, 256MiB, nur backtest_db, nur eigene DBidentität, Pool1/Overflow0 | kein zusätzlicher Workerpool; bounded Streaming und Negativtests |
| API/UI | services/control_api/backtest_*.py, retrospective_*.py; research/backtesting.component.*, Reportdialog; kanonischer Vertrag | bestehende Auth/Origin/CSRF/Idempotenz, Material und Clientgenerierung weiterführen |
| Sicherheitsbarrieren | Migration 0033 gemeinsame Admission/oldest_job und DBguard, 0034 aktuelle negative Lizenz-/Trust-/Maßnahmengates | neue Jobvariante in denselben DBbarrieren, exaktes Provisionierungsmanifest erweitern |

## Historische Evidenz und offene Schulden

docs/evidence/rrb-implementation-verification.md dokumentiert 1378 Unit/Property/Securityfälle, 1899 Contract-/Transportfälle, echte PostgreSQL-/Browser-/Containerketten und bedienbare Safetykontrollen unter voller Queue. Das sind gespeicherte frühere Ergebnisse, hier nicht erneut ausgeführt. Keine verifizierten persönlichen Providerrechte, keine Profitabilitätsaussage; Produktionstrust leer und Dispatch deaktiviert.

OpenSpec list meldet sieben abgeschlossene aktive Changes. Main inventory besitzt keine research-backtesting-, readiness- oder retrospective-Capability; die implementierten Deltas bleiben daher explizite Basis. MDR ist nur teilweise konsolidiert (historical-market-data/research-evidence/research-context vorhanden). BKT/RDR/RRB sind separat zu konsolidieren/archivieren. Weder Änderung noch Archivierung dieser Vorarbeiten ist Teil QSL. Keine blockierende semantische Kollision mit den drei neuen additiven Capabilities erkannt.

## Dokumentationsquellen

Context7 resolve für OpenSpec und PostgreSQL versucht: Monatskontingent erschöpft, keine Dokumentationsantwort. Fallback auf offizielle Quellen, abgerufen 30.10.2026: [OpenSpec CLI](https://github.com/Fission-AI/OpenSpec/blob/main/docs/cli.md), [PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html), [GitHub Issue Dependencies](https://docs.github.com/en/rest/issues/issue-dependencies). Lokale gepinnte OpenSpec-Version über scripts/openspec.ps1 bleibt maßgeblich; kein Toolupdate. SKIP LOCKED allein gewährleistet keine globale Admission: vorhandene Advisory-/DBguards bleiben nötig. GitHub-MCP bietet keine nativen Dependencyoperationen; hierfür wird der ausdrücklich autorisierte enge GH-CLI-Fallback genutzt.
