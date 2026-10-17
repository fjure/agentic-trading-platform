# Tasks

Der Planungsdurchlauf ist abgeschlossen; Apply wurde gesondert freigegeben. Dauerhafte IDs PTV-01..PTV-25; Checkboxnummern dienen nur der OpenSpec-Struktur. Die am 2026-10-17 ausdrücklich freigegebenen PTV-24/25 schließen die SQL-Paperintegrationslücke vor PTV-21, ohne bestehende IDs umzunummerieren. Quellen/Versionen: technology-baseline.md. Gemeinsame Definition of Done: Verhalten und paketzugehörige Tests bestanden, Dokumentation/Contracts aktuell, secretfreie Evidenz gespeichert, Safetyreview soweit betroffen, GitHub-Sync im selben Durchlauf verifiziert. Kein Taskabschluss durch bloße Issueänderung. Ausgeführte Prüfungen stehen in implementation-evidence.md.

Parallelität: nach abgeschlossenen Domain-/Persistenzverträgen dürfen PTV-06 und PTV-08 getrennt arbeiten; nach PTV-14 PTV-15 und PTV-16; PTV-18 parallel zu externen Adaptern. Gemeinsame Contract-, Compose- oder Runbookdateien nur seriell durch Integrator. Größenrahmen eine Arbeitssitzung pro Leaf; bei notwendiger Aufteilung erst OpenSpec und Issue-Mapping gemeinsam ändern.

## 1. Build und Verträge

- [x] 1.1 PTV-01 Reproduzierbare Buildgrundlage; Evidenz: Build-Smoke, Dependency-/Secret-Scan.
  - Requirements: REQ-RUN-006, REQ-RUN-007
  - Zielpfade: `pyproject.toml`, `apps/dashboard/package.json`, `infra/compose/compose.yaml`, `docs/runbooks/local-operation.md`
  - Abhängigkeiten: keine
  - Verhalten: Python-/Angular-Locks, lokale Fake-Composeprofile und default DISABLED ohne Secrets anlegen.
  - Akzeptanz: Clean install und Compose config passieren; keine PrimeNG-/LIVE-Abhängigkeit.
  - Tests: Build-Smoke, Dependency-/Secret-Scan
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 1.2 PTV-02 Domainwerte und typisierte Ports; Evidenz: T-MKT-001/003/008, T-DEC-008, T-RSK-003, T-RUN-001.
  - Requirements: REQ-MKT-001, REQ-MKT-003, REQ-MKT-008, REQ-DEC-008, REQ-RSK-003, REQ-RUN-001
  - Zielpfade: `packages/domain/models.py`, `packages/domain/ports.py`, `tests/unit/test_domain.py`, `tests/property/test_decimal.py`
  - Abhängigkeiten: PTV-01
  - Verhalten: DTOs, Decimal/UTC, Entitäten und alle sieben Ports nach D-02/D-03 abgrenzen.
  - Akzeptanz: Nichtfinite Werte, unbestätigte Capability und unzulässige Zielposition werden verworfen.
  - Tests: T-MKT-001/003/008, T-DEC-008, T-RSK-003, T-RUN-001
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 1.3 PTV-03 Contract-CI und Angular-Client; Evidenz: T-CTL-001/002/003, T-RUN-004; validate-contracts + double generation.
  - Requirements: REQ-CTL-001, REQ-CTL-002, REQ-CTL-003, REQ-RUN-004
  - Zielpfade: `scripts/validate-contracts.py`, `scripts/check-angular-client.ps1`, `.github/workflows/contracts.yml`, `apps/dashboard/src/app/api/generated/`, `tests/contracts/test_examples.py`
  - Abhängigkeiten: PTV-01, PTV-02
  - Verhalten: Gepinnte Vertragsprüfung, echte Mainbaseline-Diffprüfung und Clientgenerierung in CI integrieren; Initialcontract separat erkennen.
  - Akzeptanz: Ungültige Beispiele und generierter Drift blockieren CI; kein FastAPI-Stub.
  - Tests: T-CTL-001/002/003, T-RUN-004; validate-contracts + double generation
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

## 2. Persistenz und Dauerhaftigkeit

- [x] 2.1 PTV-04 Ledgerpersistenz und Migrationen; Evidenz: T-RUN-001/002/005, T-RSK-007.
  - Requirements: REQ-RUN-001, REQ-RUN-002, REQ-RUN-005, REQ-RSK-007
  - Zielpfade: `services/control_api/migrations/`, `packages/domain/repositories.py`, `tests/integration/test_ledger.py`, `docs/runbooks/recovery.md`
  - Abhängigkeiten: PTV-02, PTV-03
  - Verhalten: NUMERIC/timestamptz, FK/Unique, unveränderliche Referenzen, append-only Audit und DB-Rollen anlegen.
  - Akzeptanz: Migration/Backup/Restore erhalten Ledger; Duplikate und Auditmutation scheitern.
  - Tests: T-RUN-001/002/005, T-RSK-007
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 2.2 PTV-05 Jobleases und transaktionale Outbox; Evidenz: T-RUN-002/003/004.
  - Requirements: REQ-RUN-002, REQ-RUN-003, REQ-RUN-004
  - Zielpfade: `services/worker/jobs.py`, `packages/observability/outbox.py`, `tests/integration/test_jobs_outbox.py`, `docs/runbooks/jobs.md`
  - Abhängigkeiten: PTV-04
  - Verhalten: Lease/Heartbeat/Fencing, bounded Retry/Deadletter, at-least-once Outbox und geordneten Commitcursor implementieren.
  - Akzeptanz: Staler Worker kann nichts committen; Crash verliert kein bestätigtes Event.
  - Tests: T-RUN-002/003/004
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

## 3. Lokale Daten und Adapter

- [x] 3.1 PTV-06 Neutrale Marktevidenz aus Fixtures; Evidenz: T-MKT-001..008.
  - Requirements: REQ-MKT-001, REQ-MKT-002, REQ-MKT-003, REQ-MKT-004, REQ-MKT-005, REQ-MKT-006, REQ-MKT-007, REQ-MKT-008
  - Zielpfade: `services/worker/market_state.py`, `packages/decisions/neutral_evidence.py`, `config/markets/paper.yaml`, `tests/unit/test_market_state.py`
  - Abhängigkeiten: PTV-02, PTV-04
  - Verhalten: 15m closed-bars, asOf/availableAt, Sessionkalender/DST, quality/spread und neutrale Evidence versioniert normalisieren.
  - Akzeptanz: Offene/stale/lückenhafte/Zukunftsdaten blockieren; unzulässige Session erzeugt keinen Einstieg.
  - Tests: T-MKT-001..008
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 3.2 PTV-07 Mock und deterministische Baseline; Evidenz: T-DEC-001/002/004/006/008.
  - Requirements: REQ-DEC-001, REQ-DEC-002, REQ-DEC-004, REQ-DEC-006, REQ-DEC-008
  - Zielpfade: `packages/decisions/mock.py`, `packages/decisions/baseline.py`, `services/worker/planner.py`, `tests/unit/test_decision.py`
  - Abhängigkeiten: PTV-02, PTV-04, PTV-06
  - Verhalten: Versionierte immutable Input/Decision/Proposal und LONG/FLAT/ABSTAIN; keine Confidencegrößenlogik.
  - Akzeptanz: Wiederholung bytegleich; unvollständiger Input führt immer zu ABSTAIN.
  - Tests: T-DEC-001/002/004/006/008
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 3.3 PTV-08 FakeBroker zertifizieren; Evidenz: T-RSK-003/008/009/010, T-RUN-002.
  - Requirements: REQ-RSK-003, REQ-RSK-008, REQ-RSK-009, REQ-RSK-010, REQ-RUN-002
  - Zielpfade: `packages/brokers/fake.py`, `tests/contracts/test_fake_broker.py`, `docs/runbooks/fake-broker.md`
  - Abhängigkeiten: PTV-02
  - Verhalten: Capabilities und deterministische Faultinjection für Timeout, Rate Limit, partial fill, Cancelrennen, unknown und Reconnect liefern.
  - Akzeptanz: Alle Brokerport-Tests passieren; Fake hat keinen Netzwerkzugriff.
  - Tests: T-RSK-003/008/009/010, T-RUN-002
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

## 4. Risk Execution Recovery und Kontrollpfade

- [x] 4.1 PTV-09 Risk-Policy und atomare Reservierung; Evidenz: T-RSK-003..007.
  - Requirements: REQ-RSK-003, REQ-RSK-004, REQ-RSK-005, REQ-RSK-006, REQ-RSK-007
  - Zielpfade: `packages/risk/engine.py`, `packages/risk/reservations.py`, `config/risk/paper.yaml`, `services/control_api/migrations/versions/0003_risk_context.py`, `tests/property/test_risk.py`, `tests/integration/test_risk_races.py`, `tests/integration/risk_support.py`, `docs/evidence/ptv-09-risk.md`
  - Abhängigkeiten: PTV-04, PTV-06, PTV-07
  - Verhalten: Deterministische Limits/TTL, Accountlock und Exposure aus Positionen/Orders/Reservierungen; Policyversion auditiert.
  - Akzeptanz: Parallelität 100 konkurrierender Vorschläge verbraucht nie doppeltes Budget; expiry reject.
  - Tests: T-RSK-003..007
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 4.2 PTV-10 Execution-Submitgate und Paperguard; Evidenz: T-RSK-001/002/004/005/008, T-RUN-006.
  - Requirements: REQ-RSK-001, REQ-RSK-002, REQ-RSK-004, REQ-RSK-005, REQ-RSK-008, REQ-RUN-006
  - Zielpfade: `services/execution/gates.py`, `services/execution/intents.py`, `tests/security/test_execution_isolation.py`, `tests/recovery/test_submit_crash.py`, `tests/recovery/execution_support.py`, `tests/integration/risk_support.py`, `docs/evidence/ptv-10-execution.md`
  - Abhängigkeiten: PTV-05, PTV-08, PTV-09
  - Verhalten: Dauerhaftes SUBMITTING vor I/O, eindeutige clientOrderId, TTL/Version/Pause recheck, nur Execution-Paperport.
  - Akzeptanz: LIVE/REPLAY/SHADOW/agentischer Submit haben 0 Brokeraufrufe; Crash erzeugt keinen blinden Retry.
  - Tests: T-RSK-001/002/004/005/008, T-RUN-006
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 4.3 PTV-11 Reconciliation und Orderledger; Evidenz: T-RSK-009/010, T-RUN-001/002.
  - Requirements: REQ-RSK-009, REQ-RSK-010, REQ-RUN-001, REQ-RUN-002
  - Zielpfade: `services/execution/reconciliation.py`, `packages/brokers/reports.py`, `tests/recovery/test_reconciliation.py`, `tests/recovery/reconciliation_support.py`, `docs/runbooks/recovery.md`, `docs/evidence/ptv-11-reconciliation.md`
  - Abhängigkeiten: PTV-10
  - Verhalten: Alle Zustände/Fills/Cancelrennen deduplizieren, UNKNOWN hält Budget, authoritative Reconcile vor Retry.
  - Akzeptanz: Keine Doppelorder oder doppelte Fillbuchung in allen Crash-/Racefixtures; ungeklärte Abweichung sperrt neue Risiken.
  - Tests: T-RSK-009/010, T-RUN-001/002
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 4.4 PTV-12 Pause, Resume und unabhängiger Watchdog; Evidenz: T-RSK-001/002, T-CTL-005, T-RUN-005/007.
  - Requirements: REQ-RSK-001, REQ-RSK-002, REQ-CTL-005, REQ-RUN-005, REQ-RUN-007
  - Zielpfade: `services/watchdog/main.py`, `services/watchdog/__init__.py`, `services/control_api/control.py`, `services/control_api/migrations/versions/0004_safety_controls.py`, `services/execution/control_socket.py`, `services/execution/recovery_health.py`, `services/execution/gates.py`, `services/execution/intents.py`, `packages/brokers/fake.py`, `packages/domain/models.py`, `packages/observability/outbox.py`, `contracts/schemas/v1/safety-event.schema.json`, `contracts/schemas/v1/event-envelope.schema.json`, `contracts/examples/v1/safety-event.valid.json`, `contracts/examples/v1/safety-event.invalid.json`, `scripts/validate-contracts.py`, `scripts/validate-plan.py`, `tests/contracts/test_examples.py`, `tests/integration/test_pause_resume.py`, `tests/integration/control_support.py`, `tests/integration/risk_support.py`, `tests/recovery/execution_support.py`, `tests/recovery/reconciliation_support.py`, `docs/runbooks/incident.md`, `docs/evidence/ptv-12-safety-controls.md`
  - Abhängigkeiten: PTV-10, PTV-11
  - Verhalten: Persistente Safety-Latches, CLI-Kontrollsocket, Health/Reconcilegate für Resume und unabhängige Pause/Kill.
  - Akzeptanz: Jev-/UI-Ausfall blockiert keine Pause; DB-Ausfall oder Leaseverlust verhindert Submit.
  - Tests: T-RSK-001/002, T-CTL-005, T-RUN-005/007
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

## 5. Erster vollständig lokaler Meilenstein

- [x] 5.1 PTV-13 Vollständiger lokaler Fake-Trade; Evidenz: T-MKT-002, T-DEC-001, T-RSK-004/007/009/010, T-RUN-004/005, T-CTL-005.
  - Requirements: REQ-MKT-002, REQ-DEC-001, REQ-RSK-004, REQ-RSK-007, REQ-RSK-009, REQ-RSK-010, REQ-RUN-004, REQ-RUN-005, REQ-CTL-005
  - Zielpfade: `services/execution/intents.py`, `tests/integration/test_vertical_fake.py`, `tests/integration/vertical_support.py`, `tests/recovery/test_vertical_failures.py`, `docs/runbooks/local-operation.md`, `docs/evidence/ptv-13-vertical-fake.md`
  - Abhängigkeiten: PTV-05, PTV-06, PTV-07, PTV-08, PTV-11, PTV-12
  - Verhalten: Fixture → MarketState → Mock → Proposal → Risk → Fake fill → Portfolio/Audit ohne externe Dienste beweisen.
  - Akzeptanz: Vollständige Tracekette, 0 Doppelorders bei Crash vor/nach Submit; alle offenen UNKNOWNs sichtbar.
  - Tests: T-MKT-002, T-DEC-001, T-RSK-004/007/009/010, T-RUN-004/005, T-CTL-005
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 5.2 PTV-14 Deterministisches Replay und Vergleichsbericht; Evidenz: T-MKT-004, T-DEC-001/006/007.
  - Requirements: REQ-MKT-004, REQ-DEC-001, REQ-DEC-006, REQ-DEC-007
  - Zielpfade: `packages/decisions/replay.py`, `tests/integration/test_replay.py`, `docs/runbooks/replay.md`, `docs/evidence/ptv-14-replay.md`
  - Abhängigkeiten: PTV-13
  - Verhalten: Clock/IdGenerator, immutable Inputs und gecachte Providerantworten replayen; Baseline/Mockvergleich versionieren.
  - Akzeptanz: Zwei Replays gleiche fachliche Hashes und 0 Brokerzugriff; keine Zukunftsdaten.
  - Tests: T-MKT-004, T-DEC-001/006/007
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

## 6. Externe Adapter nach lokalem Nachweis

- [x] 6.1 PTV-15 Jev-Adapter und Shadowevaluation; Evidenz: T-DEC-002..006/008, T-RSK-001.
  - Requirements: REQ-DEC-002, REQ-DEC-003, REQ-DEC-004, REQ-DEC-005, REQ-DEC-006, REQ-DEC-008, REQ-RSK-001
  - Zielpfade: `packages/decisions/jev.py`, `packages/decisions/replay.py`, `packages/domain/models.py`, `packages/persistence/schema.py`, `services/control_api/migrations/versions/0005_decision_usage.py`, `config/agents/jev.yaml`, `contracts/http/control-api.openapi.yaml`, `contracts/schemas/v1/decision-result.schema.json`, `contracts/providers/jev/2026-10-14/request.schema.json`, `contracts/providers/jev/2026-10-14/response.schema.json`, `contracts/providers/jev/2026-10-14/request.valid.json`, `contracts/providers/jev/2026-10-14/response.valid.json`, `contracts/providers/jev/2026-10-14/response.invalid.json`, `apps/dashboard/src/app/api/generated/control-api/`, `tests/contracts/test_jev.py`, `tests/contracts/test_jev_wire.py`, `tests/integration/test_jev_usage.py`, `tests/integration/jev_usage_support.py`, `tests/integration/test_jev_shadow.py`, `tests/integration/risk_support.py`, `tests/integration/ledger_support.py`, `docs/development/jev-wire-contract.md`, `docs/runbooks/jev-evaluation.md`, `docs/evidence/ptv-15-jev-shadow.md`
  - Abhängigkeiten: PTV-14
  - Verhalten: Offiziell abgeleiteten versionierten Wirevertrag für Choice/Score/Noul, fallback und Shadow implementieren; kein Codex-API-Key.
  - Akzeptanz: 100 Cases: akzeptierte Antworten 100% schemakonform; Pflichtfehler 100% ABSTAIN; p95 <=5s; Confidence niemals Sizing.
  - Tests: T-DEC-002..006/008, T-RSK-001
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 6.2 PTV-16 Alpaca-MarketData und Fähigkeiten; Evidenz: T-MKT-001/002/003/005/006/008, T-RSK-003, T-RUN-006.
  - Requirements: REQ-MKT-001, REQ-MKT-002, REQ-MKT-003, REQ-MKT-005, REQ-MKT-006, REQ-MKT-008, REQ-RSK-003, REQ-RUN-006
  - Zielpfade: `packages/brokers/alpaca_market_data.py`, `services/execution/market_gateway.py`, `tests/contracts/test_alpaca_market.py`, `tests/security/test_market_gateway.py`, `docs/runbooks/alpaca-data.md`, `docs/evidence/ptv-16-alpaca-market.md`
  - Abhängigkeiten: PTV-14
  - Verhalten: Execution-seitiges credentialgebundenes Gateway, read-only Asset/Sessionabfrage und capability mapping; Worker DTOs ohne Secrets.
  - Akzeptanz: SPY/QQQ/BTC/USD nur bei aktuell bestätigter Berechtigung; rate limit/stale/DST mapped.
  - Tests: T-MKT-001/002/003/005/006/008, T-RSK-003, T-RUN-006
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 6.3 PTV-17 Alpaca-Paperadapter zertifizieren; Evidenz: T-RSK-003/008/009/010, T-RUN-002/006.
  - Requirements: REQ-RSK-003, REQ-RSK-008, REQ-RSK-009, REQ-RSK-010, REQ-RUN-002, REQ-RUN-006
  - Zielpfade: `packages/brokers/alpaca_paper.py`, `tests/contracts/test_alpaca_paper.py`, `tests/recovery/test_alpaca_fixtures.py`, `docs/runbooks/paper-smoke.md`, `docs/evidence/ptv-17-alpaca-paper.md`
  - Abhängigkeiten: PTV-15, PTV-16, PTV-11
  - Verhalten: Paper-only Host/Accountguard, client-ID/query mapping, Fake-Port-Zertifizierung mit offiziellen Vendorfixtures.
  - Akzeptanz: Happy Path allein unzureichend; Timeout/unknown/partial/cancel/rate/reconnect passieren offline.
  - Tests: T-RSK-003/008/009/010, T-RUN-002/006
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

## 7. Control API und Dashboard

- [x] 7.1 PTV-18 FastAPI-Kontrolladapter und Sessions; Evidenz: T-CTL-001/002/003/005/007.
  - Requirements: REQ-CTL-001, REQ-CTL-002, REQ-CTL-003, REQ-CTL-005, REQ-CTL-007
  - Zielpfade: `services/control_api/app.py`, `services/control_api/auth.py`, `services/control_api/backend.py`, `services/control_api/migrations/versions/0006_control_http.py`, `tests/contracts/test_control_api.py`, `tests/security/test_control_auth.py`, `tests/integration/control_http_support.py`, `tests/integration/test_control_http.py`, `tests/integration/control_support.py`, `packages/domain/models.py`, `packages/observability/outbox.py`, `contracts/schemas/v1/control-event.schema.json`, `contracts/schemas/v1/event-envelope.schema.json`, `contracts/schemas/v1/safety-event.schema.json`, `contracts/examples/v1/control-event.valid.json`, `contracts/examples/v1/control-event.invalid.json`, `tests/contracts/test_control_events.py`, `scripts/validate-contracts.py`, `scripts/validate-plan.py`, `openspec/changes/paper-trading-vertical-slice/test-strategy.md`, `openspec/changes/paper-trading-vertical-slice/traceability.md`, `openspec/changes/paper-trading-vertical-slice/traceability.json`, `contracts/http/control-api.openapi.yaml`, `apps/dashboard/src/app/api/generated/control-api`, `docs/runbooks/local-operation.md`, `docs/evidence/ptv-18-control-api.md`
  - Abhängigkeiten: PTV-03, PTV-12
  - Verhalten: Contract-first HTTPadapter, lokale Sessions/Origin/Hostguard, Problemcodes, Idempotenz und ETags implementieren.
  - Akzeptanz: Schema-negative/Fuzzfälle, Replaykeys und 412/428 prüfen; keine Order-submitroute.
  - Tests: T-CTL-001/002/003/005/007
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 7.2 PTV-19 Snapshot, Cursor und SSE; Evidenz: T-CTL-004, T-RUN-004/005.
  - Requirements: REQ-CTL-004, REQ-RUN-004, REQ-RUN-005
  - Zielpfade: `services/control_api/snapshot.py`, `services/control_api/events.py`, `services/control_api/cursors.py`, `services/control_api/streaming.py`, `services/control_api/projections.py`, `services/control_api/app.py`, `services/control_api/migrations/versions/0007_supervisor_cursors.py`, `tests/integration/test_sse_cursor.py`, `tests/integration/supervisor_support.py`, `tests/contracts/test_supervisor_events.py`, `tests/contracts/test_supervisor_projections.py`, `tests/contracts/test_supervisor_transport.py`, `docs/evidence/ptv-19-snapshot-sse.md`, `docs/runbooks/local-operation.md`, `openspec/changes/paper-trading-vertical-slice/test-strategy.md`, `openspec/changes/paper-trading-vertical-slice/traceability.md`, `openspec/changes/paper-trading-vertical-slice/traceability.json`
  - Abhängigkeiten: PTV-05, PTV-18
  - Verhalten: Commitgeordnete Events, konsistenter Snapshotcursor, heartbeat/Last-Event-ID/410/reset-required.
  - Akzeptanz: Reconnect verliert keine Events; abgelaufener Cursor explizit; parallel Commit ohne stille Lücke.
  - Tests: T-CTL-004, T-RUN-004/005
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 7.3 PTV-20 Angular-Material-Supervisor; Evidenz: T-CTL-001/004/005/006/007.
  - Requirements: REQ-CTL-001, REQ-CTL-004, REQ-CTL-005, REQ-CTL-006, REQ-CTL-007
  - Zielpfade: `apps/dashboard/src/app/supervisor/`, `apps/dashboard/src/app/state/`, `apps/dashboard/src/app/app.ts`, `apps/dashboard/src/app/app.html`, `apps/dashboard/src/app/app.css`, `apps/dashboard/src/main.ts`, `apps/dashboard/package.json`, `apps/dashboard/package-lock.json`, `tests/e2e/`, `playwright.config.ts`, `scripts/test-dashboard.ps1`, `.github/workflows/contracts.yml`, `docs/runbooks/dashboard.md`, `docs/adr/0003-dashboard-state.md`, `docs/evidence/ptv-20-material-supervisor.md`, `openspec/changes/paper-trading-vertical-slice/test-strategy.md`, `openspec/changes/paper-trading-vertical-slice/traceability.md`, `openspec/changes/paper-trading-vertical-slice/traceability.json`
  - Abhängigkeiten: PTV-18, PTV-19
  - Verhalten: Materialansichten/Dialogs, SignalStore geteilte DTOs/Cursor/Connection/Filter, lokaler Formularstatus; strukturierte Traceansicht.
  - Akzeptanz: Alle geforderten Anzeigen und pause/resume; Disconnect stale sichtbar; Browsersecret-/persistenztest.
  - Tests: T-CTL-001/004/005/006/007
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

## 8. Grenzen und abschließende Integration

- [x] 8.1 PTV-24 Dauerhafte Alpaca-Executionbindung; Evidenz: T-RSK-024, T-RUN-024 und bestehende Fake-Regressionen.
  - Requirements: REQ-RSK-003, REQ-RSK-004, REQ-RSK-005, REQ-RSK-007, REQ-RSK-008, REQ-RSK-009, REQ-RUN-001, REQ-RUN-002, REQ-RUN-005, REQ-RUN-006
  - Zielpfade: `services/execution/intents.py`, `services/execution/gates.py`, `services/execution/broker_binding.py`, `services/execution/broker_recovery.py`, `services/control_api/migrations/versions/0008_broker_binding.py`, `services/control_api/projections.py`, `packages/brokers/proofs.py`, `packages/brokers/alpaca_paper.py`, `packages/brokers/alpaca_market_data.py`, `packages/risk/engine.py`, `packages/risk/reservations.py`, `tests/integration/test_alpaca_execution.py`, `tests/integration/alpaca_execution_support.py`, `tests/unit/test_broker_binding.py`, `tests/contracts/test_supervisor_projections.py`, `docs/evidence/ptv-24-alpaca-execution.md`, `scripts/extend-integration-plan-gh.py`, `tests/unit/test_issue_plan_extension.py`, `docs/runbooks/paper-smoke.md`
  - Abhängigkeiten: PTV-16, PTV-17
  - Verhalten: Execution-eigene Konto-/Asset-/Session-/Capabilitybindung, auditierte Kontextveröffentlichung und SQL-SUBMITTING vor I/O; Wiederherstellung möglicher Versuche ohne erneuten POST; Markt-/Brokerbelege besitzen eine gemeinsame geprüfte Capabilityversion.
  - Akzeptanz: Konto-/Asset-/Versions-/Frischeabweichung sendet null POST; Timeout/Crash/unklarer 404 hält Reservierung und Pause; PostgreSQL-Restore sendet keinen zweiten POST; Fake-Verhalten bleibt valide; Alpaca-Aktivierung bleibt bis PTV-25 geschlossen.
  - Tests: T-RSK-024, T-RUN-024; Offline-MockTransport plus tatsächliche PostgreSQL-Rollen-, Crash-, Timeout-, Fencing- und Fake-Regressionstests, keine echten Brokerrequests.
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 8.2 PTV-25 Verbindliche Paper-Reconciliation und Gebührenbelege; Evidenz: T-RSK-025, T-RUN-025.
  - Requirements: REQ-RSK-009, REQ-RSK-010, REQ-RUN-001, REQ-RUN-002, REQ-RUN-005, REQ-RUN-007
  - Zielpfade: `packages/brokers/alpaca_paper.py`, `packages/brokers/alpaca_activities.py`, `services/execution/reconciliation.py`, `services/execution/recovery_health.py`, `services/control_api/migrations/versions/0009_paper_reconciliation.py`, `tests/integration/test_alpaca_reconciliation.py`, `tests/integration/alpaca_reconciliation_support.py`, `tests/contracts/test_alpaca_activities.py`, `docs/evidence/ptv-25-paper-reconciliation.md`, `services/execution/paper_reconciliation.py`, `services/execution/intents.py`, `services/execution/broker_binding.py`, `services/control_api/projections.py`, `tests/contracts/test_supervisor_projections.py`, `tests/integration/risk_support.py`, `docs/runbooks/recovery.md`, `docs/runbooks/paper-smoke.md`, `tests/unit/test_paper_reconciliation.py`, `tests/integration/alpaca_execution_support.py`
  - Abhängigkeiten: PTV-24
  - Verhalten: Vollständige paginierte FILL-/Order-/Position-/Kontobelege, persistente idempotente Teilfüll-/Cash-/Gebührenbuchung sowie konto- und capabilitygebundene Recovery; fehlende Gebührenautorität bleibt explizit unbekannt.
  - Akzeptanz: Keine erfundene Nullgebühr oder vorzeitige Reservierungsfreigabe; doppelte/ungeordnete Fills und Cancelrennen buchen genau ihre bestätigten Deltas; unvollständige oder widersprüchliche Activities/Fees halten Pause/Reconciliation; erfolgreiche vollständige Offlinebelege erlauben den geprüften Alpaca-Recoverypfad.
  - Tests: T-RSK-025, T-RUN-025; paginierte Offline-Adapterverträge und tatsächliche PostgreSQL-Neustart-/Fill-/Gebühren-/Mismatch-Szenarien.
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [x] 8.3 PTV-21 Deploymentgrenzen und Failure-Injection; Evidenz: T-RUN-003/005/006/007, T-RSK-001/008, T-CTL-005/007.
  - Requirements: REQ-RUN-003, REQ-RUN-005, REQ-RUN-006, REQ-RUN-007, REQ-RSK-001, REQ-RSK-008, REQ-CTL-005, REQ-CTL-007
  - Zielpfade: `.gitignore`, `pyproject.toml`, `uv.lock`, `apps/dashboard/angular.json`, `infra/compose/compose.yaml`, `infra/compose/compose.paper.yaml`, `infra/containers/Dockerfile`, `services/runtime/`, `services/control_api/app.py`, `services/control_api/auth.py`, `services/control_api/streaming.py`, `services/control_api/migrations/versions/0010_runtime_market_grants.py`, `packages/observability/telemetry.py`, `packages/observability/runtime.py`, `packages/brokers/alpaca_market_data.py`, `packages/brokers/alpaca_paper.py`, `packages/decisions/jev.py`, `services/execution/market_gateway.py`, `contracts/http/control-api.openapi.yaml`, `contracts/schemas/v1/runtime-health.schema.json`, `contracts/examples/v1/runtime-health.valid.json`, `scripts/validate-contracts.py`, `scripts/validate-plan.py`, `openspec/changes/paper-trading-vertical-slice/traceability.json`, `openspec/changes/paper-trading-vertical-slice/traceability.md`, `openspec/changes/paper-trading-vertical-slice/test-strategy.md`, `apps/dashboard/src/app/api/generated/`, `scripts/start-local-lab.ps1`, `tests/security/test_secrets.py`, `tests/security/test_host_transport.py`, `tests/unit/test_egress.py`, `tests/unit/test_runtime.py`, `tests/unit/test_runtime_proxy.py`, `tests/unit/test_runtime_metrics.py`, `tests/unit/test_runtime_telemetry.py`, `tests/contracts/test_runtime_health.py`, `tests/contracts/test_supervisor_transport.py`, `tests/contracts/test_alpaca_quote.py`, `tests/integration/test_failures.py`, `docs/runbooks/incident.md`, `docs/runbooks/local-operation.md`, `docs/development/technology-baseline.md`, `docs/development/contract-validation.md`, `docs/evidence/ptv-21-runtime-boundaries.md`
  - Abhängigkeiten: PTV-17, PTV-20, PTV-25
  - Verhalten: Execution-only mounts/Egress/Rollen, Redactioncanaries, pinned dependencies, OTel/Health und DB/Jev/Alpaca/Dataausfälle prüfen.
  - Akzeptanz: 0 synthetische Secrets außerhalb Execution; Jevdown lässt Pause/Reconcile laufen; keine LIVEroute.
  - Tests: T-RUN-003/005/006/007, T-RSK-001/008, T-CTL-005/007
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [ ] 8.4 PTV-22 Opt-in Paper-Smoke vorbereiten; Evidenz: T-RSK-008/009/010, T-RUN-005.
  - Requirements: REQ-RSK-008, REQ-RSK-009, REQ-RSK-010, REQ-RUN-005
  - Zielpfade: `tests/paper/test_smoke.py`, `docs/runbooks/paper-smoke.md`
  - Abhängigkeiten: PTV-17, PTV-21
  - Verhalten: Explizite menschliche Paperfreigabe, bestätigtes Paperkonto/Host und minimaler Order/Cancel/Fill-Auditpfad; normale CI offline.
  - Akzeptanz: Ohne ausdrückliches Paperflag Abbruch vor Kontakt; Agenten führen keine Orders aus; dokumentierter Operatorcheck.
  - Tests: T-RSK-008/009/010, T-RUN-005
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

- [ ] 8.5 PTV-23 Abschließende Integrationsverifikation; Evidenz: Vollständige Testmatrix + OpenSpec strict + github-issue-sync.
  - Requirements: REQ-MKT-001, REQ-DEC-007, REQ-RSK-007, REQ-RSK-009, REQ-RUN-006, REQ-CTL-006
  - Zielpfade: `tests/integration/test_acceptance.py`, `openspec/changes/paper-trading-vertical-slice/readiness.md`, `docs/development/github-issue-sync.json`
  - Abhängigkeiten: PTV-13, PTV-14, PTV-15, PTV-16, PTV-17, PTV-18, PTV-19, PTV-20, PTV-21, PTV-22
  - Verhalten: Paketübergreifende Akzeptanz, Contractdrift/Traceability/Recovery und Safety-Zertifizierung; Taskstatus/Issues abgleichen.
  - Akzeptanz: Alle Pflichtchecks samt Issue-Readback vollständig; menschlicher Papercheck separat ehrlich klassifiziert.
  - Tests: Vollständige Testmatrix + OpenSpec strict + github-issue-sync
  - Abschluss: gemeinsame Definition of Done erfüllt; Taskcheckbox und zugehöriges Issue im selben Stand; überprüfbare Resultate im Task-Audit.

## Workflow follow-up

- Nach Implementierungsreview Specs mit `$openspec-sync-specs` synchronisieren und Change mit `$openspec-archive-change` archivieren; erst nach ausdrücklichem Folgeauftrag.
