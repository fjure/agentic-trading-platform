"""Versioned conservative Paper period return; no broker, SQL or legacy Risk changes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Literal

from packages.domain.models import decimal_value, utc_time

PROFILE = "paper-utc-equity-risk.v1"
CENT = Decimal("0.01")


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("PAPER_RISK_EVIDENCE_INVALID")


@dataclass(frozen=True, slots=True)
class EquityInterval:
    lower: Decimal
    upper: Decimal

    def __post_init__(self):
        for name in ("lower", "upper"):
            object.__setattr__(self, name, decimal_value(getattr(self, name)))
        if self.lower > self.upper:
            raise ValueError("PAPER_RISK_EVIDENCE_INVALID")

    @classmethod
    def reported_cents(cls, value: Decimal):
        value = decimal_value(value)
        with localcontext() as arithmetic:
            arithmetic.prec = 80
            if value != value.quantize(CENT):
                raise ValueError("PAPER_RISK_EVIDENCE_INVALID")
            return cls(value - CENT, value + CENT)


@dataclass(frozen=True, slots=True)
class DailyRiskReference:
    kind: Literal["UTC_DAY", "FIRST_DAY"]
    starts_at: datetime
    expires_at: datetime
    opening_equity: EquityInterval
    capital_lower: Decimal
    evidence_hash: str
    account_created_at: datetime | None = None
    profile: str = PROFILE

    def __post_init__(self):
        _digest(self.evidence_hash)
        for name in ("starts_at", "expires_at"):
            object.__setattr__(self, name, utc_time(getattr(self, name)))
        object.__setattr__(self, "capital_lower", decimal_value(self.capital_lower))
        midnight = self.starts_at.replace(hour=0, minute=0, second=0, microsecond=0)
        if (self.profile != PROFILE or type(self.opening_equity) is not EquityInterval
                or self.kind not in {"UTC_DAY", "FIRST_DAY"}
                or self.expires_at != midnight + timedelta(days=1)
                or self.opening_equity.lower <= 0 or self.capital_lower <= 0
                or self.capital_lower > self.opening_equity.lower):
            raise ValueError("PAPER_RISK_EVIDENCE_INVALID")
        if self.kind == "UTC_DAY":
            if self.starts_at != midnight or self.account_created_at is not None:
                raise ValueError("PAPER_RISK_EVIDENCE_INVALID")
        else:
            if self.account_created_at is None:
                raise ValueError("PAPER_RISK_EVIDENCE_INVALID")
            created = utc_time(self.account_created_at)
            object.__setattr__(self, "account_created_at", created)
            if not midnight < created <= self.starts_at < self.expires_at:
                raise ValueError("PAPER_RISK_EVIDENCE_INVALID")


@dataclass(frozen=True, slots=True)
class DailyRiskObservation:
    reference_hash: str
    observed_at: datetime
    current_equity: EquityInterval
    net_external_funding: EquityInterval
    funding_complete: bool
    evidence_hash: str

    def __post_init__(self):
        _digest(self.reference_hash)
        _digest(self.evidence_hash)
        object.__setattr__(self, "observed_at", utc_time(self.observed_at))
        if (type(self.current_equity) is not EquityInterval
                or type(self.net_external_funding) is not EquityInterval
                or type(self.funding_complete) is not bool
                or self.current_equity.lower < 0):
            raise ValueError("PAPER_RISK_EVIDENCE_INVALID")


@dataclass(frozen=True, slots=True)
class DailyLossAssessment:
    reasons: tuple[str, ...]
    loss_upper: Decimal | None = None
    cap_lower: Decimal | None = None


def assess_daily_loss(reference, observation, *, now, max_loss_fraction):
    """Strict threshold, frozen denominator, worst-case loss and bounded evidence age."""
    now, fraction = utc_time(now), decimal_value(max_loss_fraction)
    if not Decimal(0) < fraction <= Decimal("0.01"):
        raise ValueError("PAPER_RISK_POLICY_INVALID")
    if type(reference) is not DailyRiskReference or type(observation) is not DailyRiskObservation:
        return DailyLossAssessment(("PAPER_DAILY_REFERENCE_MISSING",))
    if (not reference.starts_at <= observation.observed_at <= now < reference.expires_at
            or now - observation.observed_at >= timedelta(seconds=120)
            or observation.reference_hash != reference.evidence_hash
            or not observation.funding_complete):
        return DailyLossAssessment(("PAPER_DAILY_EVIDENCE_UNPROVEN",))
    with localcontext() as arithmetic:
        arithmetic.prec = 80
        loss = max(Decimal(0), reference.opening_equity.upper
                   + observation.net_external_funding.upper - observation.current_equity.lower)
        cap = reference.capital_lower * fraction
    return DailyLossAssessment(("DAILY_LOSS_LIMIT",) if loss >= cap else (), loss, cap)
