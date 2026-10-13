---
name: broker-adapter-certification
description: Certify new or changed BrokerAdapter implementations and capability contracts against FakeBroker, recovery and reconciliation scenarios.
---

# broker-adapter-certification

## Scope and inputs
Apply to adapter behavior, broker capabilities, order mapping and reconnect/recovery changes. Exclude market-data-only changes without broker impact. Read port contract, capability matrix, provider's official API/version, FakeBroker fixtures and requirement/test mapping.

## Workflow
1. Verify capabilities explicitly: order types, quantities, sessions, shorts, brackets and Paper availability. Unsupported/unconfirmed capabilities must reject deterministically.
2. Run the shared FakeBroker contract suite and adapter-specific mapping tests.
3. Exercise idempotency, timeout after possible submit, rate limit/backoff, partial fill, cancel/fill races, unknown status, duplicate/out-of-order events, reconnect and reconciliation after process death.
4. Confirm reservation retention for unknown outcomes and reconciliation by internal/client and broker IDs before any retry. Assert no blind resubmit or double order in simulated recovery.
5. Record API constraints and evidence per capability/scenario; approve only when mandatory negative and recovery cases pass.

## Boundaries and evidence
A happy path alone never certifies an adapter. Normal certification uses FakeBroker and local tests without credentials; Paper smoke is separate, explicit opt-in and hard-guarded to Paper endpoints. Report versions, matrix, commands, results, state-transition evidence and unresolved blockers. No LIVE tests or broker MCP.
