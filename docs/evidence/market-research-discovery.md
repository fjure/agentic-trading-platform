# Bestandsaufnahme für Market Data und Research

Stand 25.10.2026. Quellenprüfung, keine Behauptung erneuter Gesamtzertifizierung. Bestehende fremde Arbeitsänderungen an `README.md` und `apps/dashboard/angular.json` bleiben erhalten.

| Prüffeld | Beobachteter Stand / Wiederverwendung | Fehlend / Grenze |
|---|---|---|
| Struktur/Architektur | Pythonmodule in packages und services; Control/Worker/Execution/Watchdog; getrennte Fake-/Demo-/Paperledger, Setupdienst | Kein Researchmodul oder Agentenorchestrator |
| Domain | `packages/domain/models.py`: Decimal, UTC, MarketBar, MarketState, Zeit-/Qualitätsfelder | Historische Vintages, Symbolgültigkeiten und PIT-Query fehlen |
| Marktdaten | `packages/brokers/alpaca_market_data.py`: GET, feste Hosts, Bars/Quotes, heutige Sitzung, begrenzte Pagination; `services/execution/market_gateway.py`: credentialfreier Workerfeed | Kein beliebiger Backfill, kein dauerhafter Historiencursor; Livebaraufnahme ist keine Ingestionpipeline |
| Kalender | `config/markets/paper.yaml`: begrenzte 2026-Fixture; tatsächlicher aktueller Sitzungskalender aus Alpaca im Reader | Keine historische versionierte Kalendercoverage, außerhalb Fixture keine sichere Annahme |
| Decision | `packages/decisions/jev.py`, `replay.py`, `neutral_evidence.py`; lokale Shadow-/Replaytests | Kein Quantbacktesting, keine Researchstrategie-Lifecycleverwaltung; Replay ist kein Backtest |
| Risk/Execution | `packages/risk/`, `services/execution/`, `services/runtime/paper_coordinator.py`; deterministische Gates, Reconciliation, LIMIT/Cancel/Recovery | Kein LIVE; keine Agentenberechtigung zu Brokerorders; Research darf Gates nicht erreichen |
| Datenbank | `packages/persistence/schema.py`, Alembic in `services/control_api/migrations/versions/`; immutable Markt-/Evidenztabellen, granulare Grants | Neue Researchdaten dürfen bestehende Handelsreferenzen nicht umschreiben |
| Jobs | `services/worker/jobs.py`: Lease, Fencing, Retry und durable Outbox | Kein Researchjobtyp; tradingbezogene Recovery nicht pauschal wiederverwenden |
| UI/Verträge | OpenAPI3.1.2 `contracts/http/control-api.openapi.yaml`, Schemas/Beispiele, generierter Client, Angular22/Material22/TypeScript6 | Keine Research-API oder Historienoberfläche |
| Toolchain | OpenSpec1.14.1 via Wrapper, Python3.13, gepinnte HTTPX/SQLAlchemy/Alembic; CI `.github/workflows/contracts.yml` | Keine neue Bibliothek erforderlich; LangGraph derzeit nicht adoptiert |
| Tests | unit/contracts/integration/security/recovery/property/e2e; PostgreSQL- und Browserketten in bestehender Evidenz | Neue Historien-/PIT-/Researchrollenfälle noch zu implementieren |
| Specs | Drei abgeschlossene Changes:25+6+10 Tasks, zunächst keine Main-Specs | Konsolidierung separat: [Syncnachweis](research-spec-consolidation.md); keine blinde Archivierung |
| GitHub | Aktueller GH-CLI-Read am25.10.:41Issues #1–#41, alle CLOSED, passend zu bisherigen Taskgruppen | Offizieller MCP fehlt im Sitzungskatalog; autorisierter GH-CLI-Fallback nötig; neue Aufgaben noch separat synchronisieren |

## Externe Hinweise überprüft

Alpaca-Paper ist als echter Adapter mit umfangreicher lokaler Offlinezertifizierung implementiert, kein Beweis tatsächlicher privater Providerabnahme. Jev ist als austauschbarer Shadowadapter vorhanden. Research/Backtesting und Multi-Agent-Orchestrierung fehlen. Sämtliche bisherige Deltas waren noch nicht mit Main-Specs synchronisiert. Der statische Kalender ist tatsächlich zeitlich begrenzt; aktueller Alpaca-Sitzungsabruf ersetzt keine historische Kalenderversionierung.

## Voraussetzungen und Grenzen

Keine Secrets oder privaten Runtimeartefakte gelesen. Keine Providerrequests oder Orders ausgeführt. GitHub-MCP nicht verfügbar; GH CLI konnte nach Sandbox-Netzfreigabe lesen. Context7 `resolve-library-id` meldete Monatsquota erschöpft; Fallback auf offizielle OpenSpec-/Providerquellen im Design. Keine globalen Einstellungen geändert.

Neue Spezifikation ist Planung; Apply benötigt gesonderten Auftrag gemäß AGENTS. Fehlende persönliche Entitlements/Lizenzen werden in der späteren Anwendung sichtbar blockiert. Historische Originalverfügbarkeit von Alpaca-Revisionen ist nicht belegt; konservative FIRST_OBSERVED-Semantik ist deshalb bewusst eingeschränkt.
