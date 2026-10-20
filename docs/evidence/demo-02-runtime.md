# DEMO-02 runtime evidence

Date: 2026-10-18. Scope: `services/demo/` and its synthetic unit/PostgreSQL tests.

The deployed entry point is `python -m services.demo`, internal port 8010. The
private, fixed JSON protocol is POST `/internal/demo`; only the Control API
should reach this port. Startup reads `/run/secrets/demo_database`, requires the
fixed `atl_demo_provisioner_login`, host `postgres`, and metadata database
`lab_demo`. Provider/proxy configuration is rejected. No runtime dependency
imports tests, calls a provider, reads provider credentials, or launches a process.

Each immutable budget/strategy request receives an internally generated UUIDv4.
Only that canonical UUID can produce `lab_demo_<32 lowercase hex digits>`.
The owner closes PUBLIC CONNECT before creating financial tables. Existing
versioned ledger migrations are used without weakening their runtime grants.
Worker, Execution and Control operations use their existing PostgreSQL roles;
initial seed and private journal remain owner operations. The deployment must
give this private login CREATEDB, NOINHERIT, no CREATEROLE/superuser/bypass, and
SET-only membership in existing application groups, while denying normal DB access.
Control API receives no database password, DDL operation, or owner connection.

The finite version `demo-synthetic-session.v1` provides two closed bars at USD
4.95/5.00, inside an explicitly local synthetic session. Initial financial seed
remains DISABLED/PAUSE_NEW with stale/pending portfolio. A run-specific explicit
activation request goes through the existing recovery health proof before any
decision or reservation. `mock-neutral` and `neutral-baseline` select the existing
versioned decision engines. Planner quantity is one; normal deterministic Risk
and Execution gates can refuse small budgets. No finished trade is inserted.

Actual FakeBroker acceptance and cumulative partial/final fills are journaled
with stable event IDs and timestamps. Startup replays the journal into the
actual FakeBroker implementation, claims a fresh fenced Execution owner,
reconciles existing orders and pauses. It never retries a submission. A hard
interruption after durable acceptance leaves the reservation in place; startup
recovers the accepted order without a second acceptance. User pause/kill first
commits a persistent metadata intent even before a financial database exists.
Only initialization and activation serialize on a per-run advisory lock; decision
and submit stages leave controls independent. Worker checkpoints/finalization
deliver pending financial latches, startup reapplies them, and completion cannot
overwrite user PAUSED status. A row lock serializes local Fake acceptance/fill
with metadata control requests, so a committed control intent cannot precede
another synthetic event. A new independent database does not
change an earlier run's latch. Metadata uses a unique persistent request key and
a unique partial index for at most one RUNNING request.

Public snapshots and bounded historical pages use the existing SnapshotReader,
CursorStore and allowlisted projections. They expose cash, positions, decision,
proposal, risk, order and audit DTOs, not broker IDs, raw journal or credentials.

## Commands and observed results

- `.venv/Scripts/python.exe -m pytest tests/unit/test_demo_configuration.py tests/unit/test_demo_protocol.py -q`:
  **10 passed**, 1.11s. Invalid Decimal budget syntax/range, strategy and database
  namespaces, provider-environment refusal, fixed operation/argument allowlist,
  bounded request and redacted failure response. One upstream TestClient warning.
  Windows TestClient requires execution outside the sandbox because asyncio's
  local socketpair otherwise stalls during event-loop creation.
- `.venv/Scripts/python.exe -m pytest tests/integration/test_dashboard_demo.py -q --tb=short`:
  Combined with the two unit modules above: **11 passed**, 67.61s, Docker
  PostgreSQL with temporary synthetic role/metadata and eight independent
  migrated run databases; all test resources removed.
  Covers USD 100/mock and USD 200/baseline: quantity one, cash 95/195,
  partial/final reports exactly once, reservation zero, complete public trace
  histories; idempotent retry and changed-payload conflict; retained user kill
  across an independent run; real startup after injected acceptance interruption,
  exactly one accepted broker order and held reservation; reload; USD 0.01
  deterministic risk refusal with zero Fake acceptance. Concurrent duplicate
  starts share one durable run; a different active request is refused. Runtime
  groups have no CONNECT to run databases. Public snapshot cash/positions match
  the ledger. Pause and kill before provisioning persist successfully without
  creating a database, and a later pause cannot downgrade kill. Pause and kill
  immediately before submit block Fake acceptance through the real gate. Pause
  immediately before a synthetic fill leaves exactly one accepted order, no
  fill, zero position, and a held reservation.
- `ruff check` on the owned runtime/tests: **passed**.

This runtime evidence does not certify external API session/origin enforcement,
launcher credential ACLs, deployed Compose network/mount isolation, the normal
laboratory financial before/after checksum, generated-client integrity, or a
real browser flow. Those require the separate integration/acceptance evidence.

Integrierte Ergänzung20.10.2026: gezielter erneuter PostgreSQL-Demodurchlauf **passed,66,59s** einschließlich Kontrollrennen, Crashrecovery und unabhängiger Budgets.21lokale Konfigurations-/Protokoll-/Provisionierungs-/Topologietests bestanden; drei damals ausgesparte Deploymentfälle wurden anschließend durch tatsächliche aktuelle Deploymentprobes ergänzt (**3passed,30,66s**: sechs gehärtete Container, gezielt projizierte Metadaten ohne Prozessumgebungen, private Volumezuordnung/Dateimodi, interne Erreichbarkeit und verweigerte normale DB-Verbindung). Fake-Lab erfolgreich vollständig gebaut und gestartet, öffentliche `/ready`200. Der getrennte echte Browsertest hat die fehlenden UI-/Normalledger-Nachweise zusätzlich erbracht; siehe demo-04-browser.md. Keine Providerrequests oder echte Credentials verwendet.
