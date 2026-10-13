---
name: trading-safety-review
description: Review changes to risk, execution, brokers, operating modes, secrets, orders, reconciliation or deployment for trading safety invariants.
---

# trading-safety-review

## Scope and inputs
Apply to the named safety boundaries, including specifications. Exclude unrelated presentation-only edits. Read AGENTS.md, relevant requirements, design decisions, diffs, tests, process permissions and deployment configuration.

## Workflow
Produce a checklist with finding, concrete file/test evidence and blocking deviation for each of: agent isolation from BrokerAdapter submit; deterministic RiskDecision and Execution gates; LIVE unreachable; DISABLED/fail-closed defaults and unsafe restart pause; Decimal and UTC; independent kill-switch/PAUSE_NEW; Execution-only credentials and redaction; immutable proposal/risk references and full audit trace.
Review atomic budget reservation across positions/open orders/reservations, expiry recheck before submit, unknown-status reservation retention, timeout reconciliation before resubmit, partial fills and cancel/fill races. Check that Jev failure cannot disable reconciliation or pause.
Map every money/security invariant to a meaningful test, inspect its result and classify pass, fail or unverified. Document-only review cannot certify absent runtime behavior.

## Boundaries and evidence
Never use broker credentials or real trading endpoints during review. Do not repair safety failures by bypassing gates. Return checklist, requirement-to-test links, commands/results, residual risks and blockers. Any failed or unverified mandatory invariant prevents implementation certification.
