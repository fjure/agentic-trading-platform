# Discovery am Ausgangsstand

Lokaler HEAD, Remote-HEAD und Remote-main wurden lesend mit `git rev-parse HEAD` und `git ls-remote origin HEAD refs/heads/main` übereinstimmend auf `341edfa724a60a56a2692fe547a30fed44e90f1a` geprüft. Die lokalen Änderungen in README, Angular-Konfiguration und .tooling gehören nicht zum Change. `openspec list --json` meldet acht abgeschlossene Changes, darunter QSL (9/9), RRB (8/8), RDR (9/9) und BKT (9/9). Abgeschlossen bedeutet hier vollständige Tasks und kein behauptetes Archiv.

| Bestand | Repositorybeleg und Konsequenz |
| --- | --- |
| Produktgrenzen | `AGENTS.md`, `openspec/config.yaml`, `docs/vision/`. Main-Specs `durable-runtime`, `provider-setup`, `supervisor-control`, `risk-execution` und Research-Capabilities gelten weiter. |
| Historische Daten und Befunde | `packages/research/{storage,evidence_storage,quality_storage,context,sources}.py` und Research-/Readiness-Routen. Nur zulässige versionierte Daten und persistierte Researchresultate als Quellen. Stale Backfills sind kein Strict PIT. Die Vision zu News, Fundamentaldaten und Makro ist kein implementierter Adapter. |
| Quant Lab | `services/control_api/quant_lab_{routes,service}.py`, `services/worker/quant_lab.py`, Migration 0035. Strategy Registry, Draft, Validation, Admission, Status, Kandidaten, Vergleich und Reliability verfügbar. Persistierte Referenzen statt eigener Simulation. |
| Durable Runtime und Audit | `services/worker/jobs.py`, `packages/research/jobs.py`, `packages/observability/outbox.py`, bestehende Backtest-/Quant-Fences. Allgemeine Queue bleibt getrennt. Neue Audittypen nach bestehendem redigierten Muster. |
| Auth und BFF | `services/control_api/{auth,app,research_client}.py` und `contracts/http/control-api.openapi.yaml`. Private Research-Relaygrenze und Session-/Origin-/CSRF-Semantik wiederverwenden, Betreiberbefugnis serverseitig ableiten. |
| Setup und Isolation | `services/setup/{app,client,store}.py`, `infra/compose/compose.paper.yaml`. Dedizierte Mounts, write-only lokales Setup und getrennte Jev-/Alpaca-Pfade sind Präzedenz für Trennung, keine Autorisierung neuer Anbieter. |
| Dashboard | `apps/dashboard/src/app/research/`, `research/quant-lab/`, generierter Client unter `api/generated/control-api/`. Neue Researchnavigation und Detailverlinkungen in vorhandener Materialstruktur. |
| Evidenz | `docs/evidence/{mdr,bkt,rdr,rrb,qsl}-implementation-verification.md`, PTV/APW-Prüfnachweise. Historische Prüfungen sind Beleg des Baselinesystems. APF-HTTP/DB-/Browser-/Safety-Tests sind künftige Abnahme. |

Detaillierte Code- und Prüfreferenzen stehen in [Quant-Integrität](quant-integrity.md), [Runtime und Budget](runtime-budget.md) und [Threat Model](threat-model.md).
