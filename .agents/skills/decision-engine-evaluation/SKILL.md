---
name: decision-engine-evaluation
description: Evaluate changes to Jev, prompts, atomic questions, DecisionEngine adapters or decision logic using versioned replay and shadow comparisons.
---

# decision-engine-evaluation

## Scope and inputs
Apply to typed outputs, fallback/abstention thresholds and model/question/prompt versions. Exclude risk-only and presentation-only changes. Read immutable normalized input corpus, its hashes, schemas, versioned Jev configuration, MockDecisionEngine and deterministic baseline, requirements and evaluation thresholds.

## Workflow
1. Freeze corpus, clock, features and evidence availability times; reject future information leakage.
2. Replay identical inputs against Mock and deterministic baseline and each versioned Jev configuration. Replay has no BrokerAdapter submission access.
3. Compare schema compliance, abstention rate, confidence calibration against labeled decisions, latency percentiles, API/token costs when reported, repeated-run stability and input/output version coverage.
4. Inject timeout, invalid schema, missing required fields, stale input and low confidence. Require ABSTAIN/no new exposure and independent risk/reconciliation/pause behavior.
5. Evaluate Shadow separately from actual trading; Shadow never submits orders. Preserve response distributions, model/question/prompt versions, input hash, trace ID, latency, usage and expiry.

## Boundaries and evidence
Confidence is never profit probability or a direct sizing input. Short-term Paper P&L alone cannot authorize promotion. Missing official Jev schema/provider documentation is a blocker, not permission to guess. Return versioned corpus/config hashes, metrics and thresholds, schema/failure tests, comparison results and acceptance decision. No Jev credentials in prompts/logs and no broker calls.
