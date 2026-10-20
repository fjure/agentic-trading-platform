"""Allowlisted public projections of durable supervisor records.

No broker identifiers, provider payloads, prompts, free-form explanations, or
operator identities cross this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum

from packages.domain.models import decimal_string

_REASONS = frozenset(
    {
        "OK",
        "ABSTAIN_MISSING_EVIDENCE",
        "ABSTAIN_INVALID_RESPONSE",
        "ABSTAIN_UNAVAILABLE",
        "RISK_STALE_MARKET",
        "RISK_SESSION_CLOSED",
        "RISK_POLICY_INCOMPLETE",
        "RISK_LIMIT_EXCEEDED",
        "RISK_CAPABILITY_UNCONFIRMED",
        "RISK_PROPOSAL_EXPIRED",
        "RISK_RECONCILIATION_REQUIRED",
        "MODE_NOT_PAPER",
        "PAUSED_NEW",
        "KILL_SWITCH_ACTIVE",
        "HEALTH_CHECK_FAILED",
        "BROKER_STATUS_UNKNOWN",
        "TEMPORARILY_UNAVAILABLE",
        "VERSION_CONFLICT",
        "PRECONDITION_REQUIRED",
        "IDEMPOTENCY_CONFLICT",
        "CURSOR_EXPIRED",
        "INVALID_REQUEST",
    }
)
_REASON_ALIASES = {
    "APPROVED": "OK",
    "GATE_APPROVED": "OK",
    "FILL_BOOKED": "OK",
    "BASELINE_SIGNAL": "OK",
    "JEV_SHADOW": "OK",
    "ABSTAIN_LOW_CONFIDENCE": "ABSTAIN_UNAVAILABLE",
    "ABSTAIN_LOW_EVIDENCE_QUALITY": "ABSTAIN_MISSING_EVIDENCE",
    "ABSTAIN_UNCERTAIN_REGIME": "ABSTAIN_UNAVAILABLE",
    "ABSTAIN_SIGNAL_CONFLICT": "ABSTAIN_UNAVAILABLE",
    "MARKET_STALE": "RISK_STALE_MARKET",
    "PORTFOLIO_OR_SOURCE_STALE": "RISK_STALE_MARKET",
    "POSITION_MARK_STALE": "RISK_STALE_MARKET",
    "SESSION_CLOSED": "RISK_SESSION_CLOSED",
    "SOURCE_MISSING": "ABSTAIN_MISSING_EVIDENCE",
    "PROPOSAL_EXPIRED": "RISK_PROPOSAL_EXPIRED",
    "RECONCILIATION": "RISK_RECONCILIATION_REQUIRED",
    "RECONCILE_REQUIRED": "RISK_RECONCILIATION_REQUIRED",
    "BROKER_CAPABILITY": "RISK_CAPABILITY_UNCONFIRMED",
    "CAPABILITY_MISSING": "RISK_CAPABILITY_UNCONFIRMED",
    "MODE_OR_SAFETY": "MODE_NOT_PAPER",
    "PAUSE_NEW": "PAUSED_NEW",
    "LIMIT": "RISK_LIMIT_EXCEEDED",
    "CASH_LIMIT": "RISK_LIMIT_EXCEEDED",
    "DAILY_LOSS_LIMIT": "RISK_LIMIT_EXCEEDED",
    "TOTAL_POSITION_LIMIT": "RISK_LIMIT_EXCEEDED",
    "INSTRUMENT_POSITION_LIMIT": "RISK_LIMIT_EXCEEDED",
    "POSITION_NOTIONAL_LIMIT": "RISK_LIMIT_EXCEEDED",
    "CAPABILITY_VERSION": "RISK_CAPABILITY_UNCONFIRMED",
    "RISK_CONTEXT_MISSING": "RISK_POLICY_INCOMPLETE",
    "RISK_CONTEXT_STALE": "RISK_STALE_MARKET",
    "RISK_CONFIG_VERSION": "VERSION_CONFLICT",
    "PORTFOLIO_VERSION": "VERSION_CONFLICT",
    "POLICY_LIMIT_MISMATCH": "RISK_POLICY_INCOMPLETE",
    "DAY_START_STALE": "RISK_STALE_MARKET",
    "CURRENCY_MISMATCH": "RISK_POLICY_INCOMPLETE",
    "QUANTITY_STEP": "RISK_CAPABILITY_UNCONFIRMED",
    "PRICE_TICK": "RISK_CAPABILITY_UNCONFIRMED",
    "TARGET_UNSUPPORTED": "RISK_CAPABILITY_UNCONFIRMED",
    "PROPOSAL_TTL": "RISK_PROPOSAL_EXPIRED",
    "SPREAD": "RISK_STALE_MARKET",
    "PROVIDER_PROOF_CONFIRMED": "OK",
    "BROKER_RECONCILED": "OK",
    "ACKNOWLEDGED": "OK",
    "SIMULATION_BOOTSTRAP": "MODE_NOT_PAPER",
}
for _code in {
    "BINDING_CAPABILITY_INVALID",
    "BINDING_CONTEXT_CHANGED",
    "BINDING_INTENT_INVALID",
    "BINDING_PIN_MISSING",
    "BINDING_PROOF_CHANGED",
    "BINDING_PROOF_INVALID",
    "BINDING_PROOF_MISMATCH",
    "BINDING_PROOF_MISSING",
    "BINDING_PROOF_STALE",
    "BINDING_PROOF_UNAVAILABLE",
    "BINDING_TIME_INVALID",
    "BROKER_ADAPTER_UNSUPPORTED",
    "BROKER_BOUNDARY",
    "PROVIDER_ACCOUNT_CHANGED",
    "PROVIDER_ASSET_CHANGED",
}:
    _REASON_ALIASES[_code] = "RISK_CAPABILITY_UNCONFIRMED"
_FEATURES = frozenset({"last_close", "previous_close", "window_volume", "return", "momentum"})
for _code in {
    "BROKER_UNAVAILABLE_OR_MISMATCH",
    "BROKER_ORDER_MISSING_OR_AMBIGUOUS",
    "BROKER_ID_MISMATCH",
    "BROKER_ORDER_MISMATCH",
    "STATUS_REGRESSION_OR_MISMATCH",
    "BROKER_REPORT_MISSING",
    "BROKER_EVENT_CONFLICT",
    "CUMULATIVE_COST_CONFLICT",
    "FILL_PROOF_MISSING",
    "TERMINAL_PROOF_MISSING",
    "FILLED_QUANTITY_MISMATCH",
    "STATUS_FILL_MISMATCH",
    "POSITION_AMBIGUOUS_OR_MISSING",
    "POSITION_CURRENCY_MISMATCH",
    "BOOKED_COST_BASIS_MISSING",
    "FILL_COST_INVALID",
    "POSITION_COST_BASIS_MISSING",
    "SELL_BASIS_OR_QUANTITY_INVALID",
    "RESERVATION_INVALID",
    "PORTFOLIO_BASIS_MISMATCH",
    "RECONCILIATION_MISMATCH",
    "BROKER_EVIDENCE",
    "OWNERSHIP_OR_IDENTITY_MISMATCH",
    "ORPHAN_INTENT",
    "FEE_UNVERIFIED",
}:
    _REASON_ALIASES[_code] = "RISK_RECONCILIATION_REQUIRED"
for _code in {
    "ACCOUNT_CONTEXT_MISSING",
    "EXECUTION_HEARTBEAT_STALE",
    "EXECUTION_OWNER_FENCED",
    "INVALID_PRE_IO_PROOF",
    "LOCAL_EXECUTION_BLOCKED",
    "UNSAFE_RESTART",
}:
    _REASON_ALIASES[_code] = "HEALTH_CHECK_FAILED"
_REASON_ALIASES["SUBMIT_UNCERTAIN"] = "BROKER_STATUS_UNKNOWN"
for _code in {
    "PAPER_BASELINE_MISSING",
    "PAPER_ACCOUNT_MISMATCH",
    "PAPER_RECEIPT_MISMATCH",
    "PAPER_EVIDENCE_STALE_OR_INCOMPLETE",
    "PAPER_EVIDENCE_UNAVAILABLE",
    "PAPER_FEE_AUTHORITY_UNKNOWN",
    "PAPER_FEE_PROFILE_CHANGED",
    "PAPER_ORDER_ADAPTER_MISMATCH",
    "BROKER_ACTIVITY_HISTORY_INCOMPLETE",
    "BROKER_ACTIVITY_IDENTITY_DRIFT",
    "BROKER_ORDER_AMBIGUOUS",
    "EXTERNAL_ORDER_MISMATCH",
    "EXTERNAL_FILL_MISMATCH",
    "EXTERNAL_POSITION_MISMATCH",
    "FILL_IDENTITY_MISMATCH",
    "FILL_HISTORY_INCOMPLETE",
    "FILL_LIMIT_PRICE_MISMATCH",
    "RESERVATION_ORIGINAL_BASIS_MISSING",
    "RESERVATION_ORIGINAL_BASIS_MISMATCH",
    "CRYPTO_FEE_EVIDENCE_INVALID",
    "CRYPTO_FEE_QUANTITY_MISMATCH",
    "FEE_EVIDENCE_INVALID",
    "UNSUPPORTED_ACCOUNT_ACTIVITY",
    "PORTFOLIO_LEDGER_DRIFT",
    "POSITION_LEDGER_DRIFT",
    "POSITION_BASELINE_MISMATCH",
    "POSITION_ACCOUNT_MISMATCH",
    "POSITION_COST_BASIS_MISMATCH",
    "CASH_ACCOUNT_MISMATCH",
    "EQUITY_ACCOUNT_MISMATCH",
    "TERMINAL_STATUS_CORRECTION",
    "RECONCILIATION_LINK_MISSING",
}:
    _REASON_ALIASES[_code] = "RISK_RECONCILIATION_REQUIRED"
for _code in {
    "PAPER_BASELINE_FROZEN",
    "PAPER_ORIGINAL_BASIS_FROZEN",
    "PAPER_ACTIVITY_BOOKED",
    "PAPER_RECONCILED",
}:
    _REASON_ALIASES[_code] = "OK"
_ACTIONS = frozenset(
    {
        "runtime.initialize",
        "risk.policy.initialize",
        "decision.persist",
        "proposal.persist",
        "risk.decide",
        "risk.reserve",
        "intent.create",
        "binding.account",
        "binding.asset",
        "binding.proof",
        "binding.context",
        "safety.latch",
        "safety.resume_requested",
        "safety.paper_activation_requested",
        "safety.resume_denied",
        "safety.portfolio_refresh",
        "safety.account_refresh",
        "safety.activate_paper",
        "safety.resume",
        "control.mode_set",
        "control.risk_policy_updated",
        "control.trading_profile_updated",
        "execution.unsafe_restart",
        "execution.paper_baseline",
        "execution.paper_receipt",
        "execution.paper_position",
        "execution.paper_portfolio",
        "execution.paper_account",
        "execution.paper_activity",
        "execution.paper_order",
        "execution.paper_order_basis",
        "execution.paper_report",
        "execution.paper_reservation",
        "execution.submit_result",
        "execution.reservation_result",
        "execution.pause_unknown",
        "execution.report",
        "execution.submitting",
        "execution.submitting_report",
        "execution.reconciliation_pause",
        "execution.reconciliation_failure",
        "execution.reconciliation_unknown",
        "execution.reconciliation_hold",
        "execution.portfolio_fill",
        "execution.account_fill",
        "execution.position_fill",
        "execution.reconciliation_report",
        "execution.reconciliation_order",
        "execution.reconciliation_reservation",
    }
)


def _get(value: object, name: str):
    return value[name] if isinstance(value, Mapping) else getattr(value, name)


def _optional(value: object, name: str):
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _text(value: object) -> str:
    return str(value.value if isinstance(value, Enum) else value)


def _decimal(value: object) -> str:
    return decimal_string(value)


def _time(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("UTC timestamp required")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _reason(value: object) -> str:
    code = _text(value)
    code = _REASON_ALIASES.get(code, code)
    if code not in _REASONS:
        raise LookupError("reason code unavailable")
    return code


def _answer(value: object) -> dict:
    result = {
        "kind": _text(_get(value, "kind")),
        "questionVersion": _get(value, "question_version"),
    }
    kind = result["kind"]
    if kind == "CHOICE":
        result.update(
            choice=_text(_get(value, "choice")),
            confidence=_decimal(_get(value, "confidence")),
            distribution={k: _decimal(v) for k, v in _get(value, "distribution").items()},
        )
    elif kind == "SCORE":
        result.update(
            score=_decimal(_get(value, "score")),
            legend=dict(_get(value, "legend")),
            distribution={k: _decimal(v) for k, v in _get(value, "distribution").items()},
            confidence=_decimal(_get(value, "confidence")),
        )
    elif kind == "NOUL":
        result["noul"] = _decimal(_get(value, "noul"))
    else:
        raise ValueError("unsupported answer kind")
    return result


def _market_state(value: object) -> dict:
    bar = _get(value, "bar")
    evidence = _get(value, "evidence")
    portfolio = _get(value, "portfolio")
    position = _get(value, "position")
    features = _get(value, "features")
    if not set(features) <= _FEATURES:
        raise LookupError("unreviewed market feature")
    projected = {
        "id": _get(value, "id"),
        "instrument": _get(value, "instrument_id"),
        "market": _text(_get(value, "market")),
        "interval": _get(bar, "interval"),
        "bar": {
            "startAt": _time(_get(bar, "start_at")),
            "endAt": _time(_get(bar, "end_at")),
            **{k: _decimal(_get(bar, k)) for k in ("open", "high", "low", "close", "volume")},
            "closed": _get(bar, "closed"),
        },
        "eventTime": _time(_get(value, "event_time")),
        "receivedAt": _time(_get(value, "received_at")),
        "availableAt": _time(_get(value, "available_at")),
        "asOf": _time(_get(value, "as_of")),
        "features": {k: _decimal(v) for k, v in features.items()},
        "evidence": {
            "neutral": _get(evidence, "neutral"),
            "sources": ["market-data" for _ in _get(evidence, "sources")],
            "availableAt": _time(_get(evidence, "available_at")),
            "conflicts": ["CONFLICT" for _ in _get(evidence, "conflicts")],
            "quality": _text(_get(evidence, "quality")),
        },
        "spread": _decimal(_get(value, "spread")),
        "quality": _text(_get(value, "quality")),
        "portfolio": {
            "equity": _decimal(_get(portfolio, "equity")),
            "cash": _decimal(_get(portfolio, "cash")),
        },
        "position": {"quantity": _decimal(_get(position, "quantity"))},
        "traceId": _get(value, "trace_id"),
    }
    if _optional(position, "average_price") is not None:
        projected["position"]["averagePrice"] = _decimal(_get(position, "average_price"))
    return projected


def _decision(value: object) -> dict:
    typed = _optional(value, "typed_answers")
    answers = None
    if typed is not None:
        answers = {
            "regime": _answer(_get(typed, "regime")),
            "evidenceQuality": _answer(_get(typed, "evidence_quality")),
            "signalConflict": _answer(_get(typed, "signal_conflict")),
        }
        if _optional(typed, "shadow_direction") is not None:
            answers["shadowDirection"] = _answer(_get(typed, "shadow_direction"))
    result = {
        "id": _get(value, "id"),
        "marketStateId": _get(value, "market_state_id"),
        "choice": _text(_get(value, "choice")),
        "typedAnswers": answers,
        "confidence": None
        if _optional(value, "confidence") is None
        else _decimal(_get(value, "confidence")),
        "distribution": None
        if _optional(value, "distribution") is None
        else {k: _decimal(v) for k, v in _get(value, "distribution").items()},
        "regime": _optional(value, "regime"),
        "evidenceQuality": None
        if _optional(value, "evidence_quality") is None
        else _decimal(_get(value, "evidence_quality")),
        "signalConflict": None
        if _optional(value, "signal_conflict") is None
        else _decimal(_get(value, "signal_conflict")),
        "modelId": _optional(value, "model_id"),
        "promptVersion": _get(value, "prompt_version"),
        "questionVersion": _get(value, "question_version"),
        "configVersion": _get(value, "config_version"),
        "inputHash": _get(value, "input_hash"),
        "latencyMs": _get(value, "latency_ms"),
        "apiUnits": None
        if _optional(value, "api_units") is None
        else _decimal(_get(value, "api_units")),
        "estimatedCost": None
        if _optional(value, "estimated_cost") is None
        else _decimal(_get(value, "estimated_cost")),
        "validUntil": _time(_get(value, "valid_until")),
        "traceId": _get(value, "trace_id"),
        "reasonCode": _reason(_get(value, "reason_code")),
    }
    if _optional(value, "shadow_choice") is not None:
        result["shadowChoice"] = _text(_get(value, "shadow_choice"))
    if (
        _optional(value, "input_tokens") is not None
        and _optional(value, "output_tokens") is not None
    ):
        result["inputTokens"] = _get(value, "input_tokens")
        result["outputTokens"] = _get(value, "output_tokens")
    return result


def _proposal(value: object) -> dict:
    raw_reasons = tuple(_get(value, "rationale_codes"))
    # Planner persists its mandatory semantic reason first, followed by private
    # configuration/provenance identifiers. Never publish those identifiers or
    # invent a successful reason for an unrecognized primary reason.
    reasons = [_reason(raw_reasons[0])] if raw_reasons else []
    for raw in raw_reasons[1:]:
        code = _text(raw)
        code = _REASON_ALIASES.get(code, code)
        if code in _REASONS and code not in reasons:
            reasons.append(code)
    return {
        "id": _get(value, "id"),
        "decisionId": _get(value, "decision_id"),
        "marketStateId": _get(value, "market_state_id"),
        "instrument": _get(value, "instrument_id"),
        "target": _text(_get(value, "target")),
        "quantity": _decimal(_get(value, "quantity")),
        "rationaleCodes": reasons,
        "createdAt": _time(_get(value, "created_at")),
        "validUntil": _time(_get(value, "valid_until")),
        "traceId": _get(value, "trace_id"),
    }


def _risk_decision(value: object) -> dict:
    exposure = _get(value, "exposure_snapshot")
    result = {
        "id": _get(value, "id"),
        "proposalId": _get(value, "proposal_id"),
        "approved": _get(value, "approved"),
        "reasonCode": _reason(_get(value, "reason_code")),
        "reasonCodes": [_reason(x) for x in _get(value, "reason_codes")],
        "policyVersion": _get(value, "policy_version"),
        "exposureSnapshot": {
            "equity": _decimal(_get(exposure, "equity")),
            "grossNotional": _decimal(_get(exposure, "gross_notional")),
            "reservedNotional": _decimal(_get(exposure, "reserved_notional")),
        },
        "decidedAt": _time(_get(value, "decided_at")),
        "validUntil": _time(_get(value, "valid_until")),
        "traceId": _get(value, "trace_id"),
    }
    if _optional(value, "reservation_id") is not None:
        result["reservationId"] = _get(value, "reservation_id")
    return result


def _order(value: object) -> dict:
    result = {
        "id": _get(value, "id"),
        "proposalId": _get(value, "proposal_id"),
        "riskDecisionId": _get(value, "risk_decision_id"),
        "instrument": _get(value, "instrument_id"),
        "status": _text(_get(value, "status")),
        "requestedQuantity": _decimal(_get(value, "requested_quantity")),
        "filledQuantity": _decimal(_get(value, "filled_quantity")),
        "reconciliation": _text(_get(value, "reconciliation")),
        "updatedAt": _time(_get(value, "updated_at")),
        "traceId": _get(value, "trace_id"),
    }
    if _optional(value, "average_fill_price") is not None:
        result["averageFillPrice"] = _decimal(_get(value, "average_fill_price"))
    return result


def _audit(value: object) -> dict:
    action = _get(value, "action")
    if action not in _ACTIONS:
        raise LookupError("audit action unavailable")
    actor = _text(_get(value, "actor_id"))
    # The ledger actor may be an identifier; expose only known service roles.
    actor = {
        "worker-planner": "worker",
        "execution-risk": "execution",
        "execution-submit": "execution",
        "execution-reconciliation": "execution",
        "execution-recovery": "execution",
        "execution-broker-binding": "execution",
        "operator": "local-operator",
    }.get(
        actor,
        actor
        if actor in {"worker", "execution", "watchdog", "control-api", "local-operator"}
        else "redacted-actor",
    )
    raw_outcome = _optional(value, "outcome")
    if action.startswith("binding.") and raw_outcome == "CONFIRMED":
        raw_outcome = "OBSERVED"
    outcomes = {
        "ACCEPTED": "ACCEPTED",
        "APPROVED": "ACCEPTED",
        "ACTIVE": "ACCEPTED",
        "CREATED": "ACCEPTED",
        "SUBMITTING": "ACCEPTED",
        "RESERVED": "ACCEPTED",
        "UPDATED": "ACCEPTED",
        "REQUESTED": "ACCEPTED",
        "RUNNING": "ACCEPTED",
        "DISABLED": "ACCEPTED",
        "REPLAY": "ACCEPTED",
        "SHADOW": "ACCEPTED",
        "PAPER": "ACCEPTED",
        "PAUSE_NEW": "ACCEPTED",
        "HALTED": "ACCEPTED",
        "REFUSED": "REFUSED",
        "DENIED": "REFUSED",
        "PRE_IO_REJECTED": "REFUSED",
        "REJECTED": "REFUSED",
        "OBSERVED": "OBSERVED",
        "ACKNOWLEDGED": "OBSERVED",
        "PARTIALLY_FILLED": "OBSERVED",
        "FILLED": "OBSERVED",
        "UNKNOWN": "OBSERVED",
        "CANCEL_PENDING": "OBSERVED",
        "CANCELED": "OBSERVED",
        "CURRENT": "OBSERVED",
        "PENDING": "OBSERVED",
        "MISMATCH": "OBSERVED",
        "REPORT": "OBSERVED",
        "SUBMITTING_REPORT": "OBSERVED",
        "SUBMIT_RESULT": "OBSERVED",
        "RESERVATION_RESULT": "OBSERVED",
        "PAUSE_UNKNOWN": "OBSERVED",
    }
    if raw_outcome is None and action in {
        "decision.persist", "proposal.persist", "runtime.initialize", "risk.policy.initialize"
    }:
        raw_outcome = "OBSERVED"
    if (
        raw_outcome not in outcomes
        and raw_outcome in (_REASONS | _REASON_ALIASES.keys())
        and action.startswith("execution.")
    ):
        raw_outcome = "OBSERVED"
    if raw_outcome not in outcomes:
        raise LookupError("audit outcome unavailable")
    outcome = outcomes[raw_outcome]
    reasons = _get(value, "reason_codes")
    result = {
        "id": _get(value, "id"),
        "occurredAt": _time(_get(value, "occurred_at")),
        "actor": actor,
        "action": action,
        "outcome": outcome,
        "reasonCode": _reason(reasons[0] if reasons else "OK"),
        "traceId": _get(value, "trace_id"),
    }
    if _optional(value, "policy_version") is not None:
        result["policyVersion"] = _get(value, "policy_version")
    return result


def _run(value: object) -> dict:
    role = _optional(value, "role")
    started = _optional(value, "started_at")
    if role is None or started is None:
        raise LookupError("run provenance unavailable")
    if role not in {"MARKET_INTELLIGENCE", "QUANT_RESEARCH", "PORTFOLIO_PLANNER", "WORKER"}:
        raise LookupError("run role unavailable")
    status = _get(value, "status")
    if status not in {"RUNNING", "SUCCEEDED", "FAILED", "DEAD_LETTER"}:
        raise LookupError("run status unavailable")
    heartbeat = _get(value, "heartbeat_at")
    return {
        "id": _get(value, "id"),
        "role": role,
        "status": status,
        "startedAt": _time(started),
        "heartbeatAt": _time(heartbeat),
        "traceId": _get(value, "trace_id"),
    }


def _cost(value: object) -> dict:
    amount = _optional(value, "amount")
    provider = _optional(value, "provider")
    currency = _optional(value, "currency")
    if provider is None or currency is None:
        raise LookupError("cost provenance unavailable")
    return {
        "id": _get(value, "id"),
        "provider": provider,
        "observedAt": _time(_get(value, "observed_at")),
        "currency": currency,
        "amount": None if amount is None else _decimal(amount),
        "traceId": _get(value, "trace_id"),
    }


_PROJECTORS = {
    "market-states": _market_state,
    "decisions": _decision,
    "proposals": _proposal,
    "risk-decisions": _risk_decision,
    "orders": _order,
    "audit": _audit,
    "runs": _run,
    "costs": _cost,
}


def project_resource(resource: str, value: object) -> dict:
    """Return exactly the canonical fields for one public history resource."""
    projector = _PROJECTORS.get(resource)
    if projector is None:
        raise ValueError("unknown supervisor resource")
    return projector(value)
