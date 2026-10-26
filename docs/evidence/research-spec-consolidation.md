# Recherchebericht: OpenSpec-Spezifikationskonsolidierung

Stand: 25.10.2026. Diese Notiz hält die additive Übernahme der drei abgeschlossenen Changes in `openspec/specs/` fest. Die fachlichen Requirement- und Scenario-Blöcke wurden unverändert aus den ausgewählten Delta-Dateien übernommen. Die drei Changes bleiben aktiv; es wurde nichts archiviert. Die `Purpose`-Abschnitte stammen jeweils wortgetreu aus dem Delta.

## Übernommene Requirements und Quelldateien

| Main capability | Quelle | SHA-256 der Delta-Datei | Requirements / Scenarios |
|---|---|---|---:|
| `decision-replay` | `openspec/changes/paper-trading-vertical-slice/specs/decision-replay/spec.md` | `1c1ef902418c60a78cf47a564f108f64e80b0f012a5d77f0f514c861c075c938` | 8 / 8 |
| `durable-runtime` | `openspec/changes/paper-trading-vertical-slice/specs/durable-runtime/spec.md` | `01b5e90983832281942c8fad8c3d3c460491e590a8e750f39e7359a70466ca65` | 7 / 9 |
| `market-evidence` | `openspec/changes/paper-trading-vertical-slice/specs/market-evidence/spec.md` | `aa7921f619cb64dc689fcdbae3ee60aa363259314085bc6bac79c5c81bfce9f1` | 8 / 8 |
| `risk-execution` | `openspec/changes/paper-trading-vertical-slice/specs/risk-execution/spec.md` | `97aa6daf8c6a7788f3ef3ebe166bb4a8ff1950c018d8cc6785b8035617f42a00` | 10 / 12 |
| `supervisor-control` | `openspec/changes/paper-trading-vertical-slice/specs/supervisor-control/spec.md` | `edcd3731ef8b318c41d13a6856d29e443c35fbcaecbcfae2f0e5e8489ab6ed7a` | 7 / 7 |
| `dashboard-demo` | `openspec/changes/dashboard-trading-demo/specs/dashboard-demo/spec.md` | `4a3e28a217321fd75c80c0bdeb5a02b6bb1f8ec5231013388e725e2447cf79a8` | 5 / 7 |
| `provider-setup` | `openspec/changes/dashboard-trading-demo/specs/provider-setup/spec.md` | `9578e71c24b5383806c59187ab0589627aa9610e2eec64dfc2afef62fdc06423` | 5 / 7 |
| `alpaca-paper-workflow` | `openspec/changes/alpaca-paper-workflow/specs/alpaca-paper-workflow/spec.md` | `b79814ea57672972271f0033a63bcfed48ddb9f7bfd5f64a000ef825ee93ce8e` | 11 / 24 |

Gesamt: 61 Requirements und 82 Scenarios in acht Main-Specs. Hashvergleiche der vollständigen Requirement-Blöcke gegen ihre Quelldeltas ergaben für alle acht Dateien `exactRequirementBlocks=true`. Die Delta-Dateien enthalten nur `ADDED Requirements`; damit gab es keine bestehenden Main-Requirements zu modifizieren oder zu entfernen. Requirements bleiben nach Capability sortiert und ihre bisherigen IDs bleiben erhalten.

## Umsetzungskorroboration

Diese Verweise dokumentieren vorhandenen Code und historische Prüfevidenz. Die Konsolidierung selbst hat keine Implementierungstests erneut ausgeführt.

| Capability | Code und vorhandene Tests | Vorhandene Evidenz |
|---|---|---|
| `market-evidence` | `services/worker/market_state.py`, `packages/decisions/neutral_evidence.py`, `tests/unit/test_market_state.py`, `tests/contracts/test_alpaca_market.py` | `docs/evidence/ptv-06-market-fixtures.md`, `ptv-16-alpaca-market.md` |
| `decision-replay` | `packages/decisions/mock.py`, `baseline.py`, `jev.py`, `replay.py`, `tests/unit/test_decision.py`, `tests/integration/test_replay.py` | `docs/evidence/ptv-07-decision-baseline.md`, `ptv-14-replay.md`, `ptv-15-jev-shadow.md` |
| `risk-execution` | `packages/risk/engine.py`, `reservations.py`, `services/execution/gates.py`, `intents.py`, `reconciliation.py`, `packages/brokers/alpaca_paper.py`; `tests/property/test_risk.py`, `tests/recovery/test_reconciliation.py`, `tests/contracts/test_alpaca_paper.py` | `docs/evidence/ptv-09-risk.md`–`ptv-12-safety-controls.md`, `ptv-17-alpaca-paper.md`, `ptv-24-alpaca-execution.md`, `ptv-25-paper-reconciliation.md` |
| `durable-runtime` | `packages/persistence/`, `services/worker/jobs.py`, `packages/observability/outbox.py`; `tests/integration/test_ledger.py`, `test_jobs_outbox.py`, recovery suites | `docs/evidence/ptv-21-runtime-boundaries.md`, `ptv-23-final-acceptance.md`, `ptv-25-paper-reconciliation.md` |
| `supervisor-control` | `services/control_api/`, `apps/dashboard/src/app/supervisor/`, `tests/contracts/test_control_api.py`, `tests/e2e/supervisor.spec.ts` | `docs/evidence/ptv-18-control-api.md`–`ptv-20-material-supervisor.md` |
| `dashboard-demo` | `services/demo/`, `services/runtime/provision_demo.py`, `apps/dashboard/src/app/demo-setup/`, `tests/unit/test_demo_protocol.py`, `tests/security/test_demo_deployment.py`, `tests/e2e/demo-real.spec.ts`, `tests/e2e/demo-setup.spec.ts` | `docs/evidence/demo-01-contracts.md`–`demo-04-browser.md`, `dashboard-trading-demo.md` |
| `provider-setup` | `services/setup/`, `tests/security/test_provider_setup.py`, `test_setup_api.py`, `apps/dashboard/src/app/demo-setup/` | `docs/evidence/demo-05-setup.md`, `demo-06-budget.md` |
| `alpaca-paper-workflow` | `services/runtime/paper_api.py`, `paper.py`, `paper_coordinator.py`, `services/execution/`, `services/worker/paper_*.py`, `packages/domain/paper_workflow.py`, `apps/dashboard/src/app/paper-workflow/`; paper integration, recovery, security, contract and E2E suites | `docs/evidence/apw-01.md`–`apw-10.md`, `apw-10-verification.md`, `alpaca-paper-workflow.md` |

The APW verification record describes actual Browser→API→Execution/Worker→local Alpaca HTTP-double→PostgreSQL coverage and explicitly distinguishes current final results from earlier historical runs. PTV's final acceptance record identifies its local PostgreSQL/API chain and separate browser stages. Demo's rollup records a real Chromium→HTTP→PostgreSQL run. These are saved reports, not fresh test executions from this synchronization.

## Grenzen und Validierung

No real Alpaca or Jev credentials, external provider requests, or real orders were used for these code/test claims. The actual Alpaca provider capability remains unverified until a private human run; LIVE remains excluded. Offline doubles establish local behavior and safety boundaries, not external provider availability, account entitlements, or profitability. In particular, `REQ-APW-011` requires that distinction to remain explicit.

After each chronological group—PTV, demo, APW—`./scripts/openspec.ps1 validate --specs --strict` passed. Final result: 8 specs passed, 0 failed. This validates OpenSpec structure and requirements; it does not rerun application tests, confirm GitHub issue state, or replace the source changes' verification reports.
