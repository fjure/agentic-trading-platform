"""Project persisted metrics and interpret them through a closed proposal format."""

import json
from dataclasses import dataclass
from decimal import Decimal

from packages.backtesting.experiment_protocol import exact_decimal
from packages.research.models import content_fingerprint

PHASES = {
    "training": ("inSample", "IN_SAMPLE"),
    "validation": ("validation", "VALIDATION"),
    "test": ("walkForwardOos", "WALK_FORWARD_OOS"),
    "holdout": ("holdout", "HOLDOUT"),
}
_METRICS = {
    "netReturn": ("Net return", "RATIO"),
    "maxDrawdown": ("Maximum drawdown", "RATIO"),
    "fees": ("Fees", "MONEY"),
    "slippage": ("Slippage", "MONEY"),
    "totalCosts": ("Total costs", "MONEY"),
    "sharpe": ("Sharpe ratio", "RATIO"),
    "annualizedVolatility": ("Annualized volatility", "RATIO"),
    "activity/fillCount": ("Fills", "COUNT"),
    "exposure/investedBarRatio": ("Invested bar ratio", "RATIO"),
}
_DIRECTIONS = {"POSITIVE": "positive", "NEGATIVE": "negative", "ZERO": "zero"}
_COMPARISONS = {"ABOVE": "higher than", "BELOW": "lower than", "EQUAL": "equal to"}


class InterpretationDenied(ValueError):
    """Contains a stable reason only, independent of the rejected content."""


@dataclass(frozen=True, slots=True)
class MetricClaim:
    reference: dict
    value: str
    label: str
    method_version: str | None
    frequency: str | None
    fold: int | None

    @property
    def claim_id(self) -> str:
        return content_fingerprint(self.reference)

    def document(self) -> dict:
        return {"claimId": self.claim_id, "reference": self.reference,
                "value": self.value, "label": self.label}


def metric_claims(row: dict, manifest_hash: str) -> tuple[MetricClaim, ...]:
    if row["evaluationStatus"] != "COMPLETE" or row["metrics"] is None:
        return ()
    if row["manifestHash"] != manifest_hash:
        raise ValueError("REPORT_RESULT_BINDING_MISMATCH")
    _, phase = PHASES[row["phase"]]
    subject = "CANDIDATE" if row["role"] == "SEARCH_CANDIDATE" else "BENCHMARK"
    claims = []
    for path, (label, unit) in _METRICS.items():
        metric = row["metrics"]
        for part in path.split("/"):
            metric = metric.get(part, {})
        if metric.get("status") != "VALID" or type(metric.get("value")) not in (str, int):
            continue
        try:
            number = exact_decimal(Decimal(metric["value"]))
        except (ArithmeticError, ValueError):
            raise ValueError("REPORT_RESULT_INVALID_METRIC") from None
        reference = {
            "batchId": row["batchId"], "manifestHash": manifest_hash,
            "fieldPath": f"/metrics/{path}/value",
            "phase": phase, "subject": subject, "subjectId": row["runId"], "unit": unit,
        }
        claims.append(MetricClaim(
            reference, number, label, metric.get("methodVersion"), metric.get("frequency"),
            row["fold"],
        ))
    return tuple(claims)


def _unique(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise InterpretationDenied("REPORT_OUTPUT_SHAPE_INVALID")
    return result


def _comparison_key(claim: MetricClaim) -> tuple:
    ref = claim.reference
    return (
        ref["batchId"], ref["manifestHash"], ref["fieldPath"], ref["phase"], ref["unit"],
        claim.method_version, claim.frequency, claim.fold,
    )


def interpret(raw: bytes, claims: tuple[MetricClaim, ...]) -> list[dict]:
    """Accept only references and closed predicates. All report prose is server-owned."""
    if type(raw) is not bytes or len(raw) > 16_384:
        raise InterpretationDenied("REPORT_OUTPUT_SHAPE_INVALID")
    try:
        proposals = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (ValueError, RecursionError):
        raise InterpretationDenied("REPORT_OUTPUT_SHAPE_INVALID") from None
    if type(proposals) is not list or not 1 <= len(proposals) <= 32:
        raise InterpretationDenied("REPORT_OUTPUT_SHAPE_INVALID")
    by_id = {claim.claim_id: claim for claim in claims}
    accepted = {}
    for proposal in proposals:
        if type(proposal) is not dict or set(proposal) not in (
            {"claimId", "predicate"}, {"claimId", "predicate", "benchmarkClaimId"},
        ) or any(type(value) is not str for value in proposal.values()):
            raise InterpretationDenied("REPORT_OUTPUT_SHAPE_INVALID")
        claim = by_id.get(proposal["claimId"])
        if claim is None or claim.reference["subject"] != "CANDIDATE":
            raise InterpretationDenied("REPORT_OUTPUT_REFERENCE_INVALID")
        predicate = proposal["predicate"]
        value = Decimal(claim.value)
        supporting = [claim.claim_id]
        if "benchmarkClaimId" in proposal:
            benchmark = by_id.get(proposal["benchmarkClaimId"])
            if (benchmark is None or benchmark.reference["subject"] != "BENCHMARK"
                    or claim.method_version is None or claim.frequency is None
                    or _comparison_key(claim) != _comparison_key(benchmark)):
                raise InterpretationDenied("REPORT_OUTPUT_NOT_COMPARABLE")
            baseline = Decimal(benchmark.value)
            expected = "ABOVE" if value > baseline else "BELOW" if value < baseline else "EQUAL"
            phrase = _COMPARISONS[expected] + " the bound benchmark"
            supporting.append(benchmark.claim_id)
        else:
            expected = "POSITIVE" if value > 0 else "NEGATIVE" if value < 0 else "ZERO"
            phrase = _DIRECTIONS[expected]
        if predicate != expected:
            raise InterpretationDenied("REPORT_OUTPUT_PREDICATE_MISMATCH")
        identity = content_fingerprint((predicate, supporting))
        accepted[identity] = {
            "claimId": identity, "statement": f"{claim.label} was {phrase} in the bound phase.",
            "supportingClaimIds": supporting,
        }
    return list(accepted.values())
