"""Pure, immutable validation of a short-lived isolated Paper activation."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from packages.domain.models import decimal_value, utc_time


@dataclass(frozen=True)
class PaperActivation:
    activation_id: str
    epoch: int
    credential_generation: int
    account_generation: int
    policy_version: int
    profile_version: int
    budget_version: int
    limit_policy_version: int
    risk_budget: Decimal
    strategy: str
    symbols: tuple[str, ...]
    owner_id: str
    fencing_token: int
    issued_at: datetime
    expires_at: datetime


_KEYS = {
    "activationId", "epoch", "credentialGeneration", "accountGeneration", "policyVersion",
    "profileVersion", "budgetVersion", "limitPolicyVersion", "riskBudget", "strategy",
    "symbols", "ownerId", "fencingToken", "issuedAt", "expiresAt", "operatingState",
    "mode", "safetyState", "proposalLinked",
}


def validate_activation(raw, *, now, symbol=None, owner_id=None, fencing_token=None,
                        require_proposal=True):
    """Return authority only from an exact current SQL projection; never extend its TTL."""
    try:
        now = utc_time(now)
        if type(raw) is not dict or set(raw) != _KEYS:
            return None
        if (raw["mode"], raw["safetyState"], raw["operatingState"]) != (
            "PAPER", "RUNNING", "RUNNING"
        ) or type(raw["proposalLinked"]) is not bool:
            return None
        if require_proposal and raw["proposalLinked"] is not True:
            return None
        if any(type(raw[key]) is not int or raw[key] < 1 for key in (
            "epoch", "credentialGeneration", "accountGeneration", "policyVersion",
            "profileVersion", "budgetVersion", "limitPolicyVersion", "fencingToken",
        )):
            return None
        if (not isinstance(raw["activationId"], str)
                or str(UUID(raw["activationId"])) != raw["activationId"]):
            return None
        symbols = raw["symbols"]
        if (type(symbols) is not list or not symbols or any(type(s) is not str or s not in
            {"SPY", "QQQ"} for s in symbols) or len(set(symbols)) != len(symbols)):
            return None
        if symbol is not None and symbol not in symbols:
            return None
        if raw["strategy"] not in {"mock-neutral", "neutral-baseline", "jev"}:
            return None
        if type(raw["ownerId"]) is not str or not raw["ownerId"].strip():
            return None
        if owner_id is not None and raw["ownerId"] != owner_id:
            return None
        if fencing_token is not None and (type(fencing_token) is not int or
                                         raw["fencingToken"] != fencing_token):
            return None
        if any(type(raw[k]) is not str for k in ("issuedAt", "expiresAt", "riskBudget")):
            return None
        issued = utc_time(datetime.fromisoformat(raw["issuedAt"]))
        expires = utc_time(datetime.fromisoformat(raw["expiresAt"]))
        budget = decimal_value(raw["riskBudget"])
        if not issued <= now < expires <= issued + timedelta(seconds=30) or budget <= 0:
            return None
        return PaperActivation(
            raw["activationId"], raw["epoch"], raw["credentialGeneration"],
            raw["accountGeneration"], raw["policyVersion"], raw["profileVersion"],
            raw["budgetVersion"], raw["limitPolicyVersion"], budget, raw["strategy"],
            tuple(symbols), raw["ownerId"], raw["fencingToken"], issued, expires,
        )
    except (ValueError, TypeError, OverflowError):
        return None
