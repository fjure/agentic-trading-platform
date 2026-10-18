"""Public, immutable bounds for a human-authorized, single-intent Paper check."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from packages.domain.models import decimal_value

AUTH_TTL_SECONDS = 120
MAX_NOTIONAL = Decimal("1000")
CASH_CUSHION = Decimal("1.01")


@dataclass(frozen=True, slots=True)
class SmokeRequest:
    run_id: str
    account_id: str
    account_fingerprint: str
    instrument_id: str
    limit_price: Decimal
    max_notional: Decimal
    market_state_id: str

    def __post_init__(self):
        try:
            valid_uuid = str(UUID(self.run_id)) == self.run_id
        except (ValueError, AttributeError, TypeError):
            valid_uuid = False
        if (
            not valid_uuid
            or self.account_id != "paper"
            or not isinstance(self.account_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.account_fingerprint) is None
            or self.instrument_id not in {"SPY", "QQQ"}
            or not isinstance(self.market_state_id, str)
            or re.fullmatch(r"[A-Za-z0-9:_-]{1,256}", self.market_state_id) is None
        ):
            raise ValueError("INVALID_SMOKE_REQUEST")
        try:
            price = decimal_value(self.limit_price)
            budget = decimal_value(self.max_notional)
            if not (0 < price and 0 < budget <= MAX_NOTIONAL):
                raise ValueError
            # Quantity is fixed to one whole share; never round a supplied price.
            if price % Decimal("0.01") != 0 or price * CASH_CUSHION > budget:
                raise ValueError
        except (ValueError, ArithmeticError):
            raise ValueError("INVALID_SMOKE_BUDGET") from None
        object.__setattr__(self, "limit_price", price)
        object.__setattr__(self, "max_notional", budget)

    @property
    def entity_id(self):
        return "paper-smoke:" + self.run_id

    @property
    def trace_id(self):
        return UUID(self.run_id).hex

    @property
    def proposal_id(self):
        return hashlib.sha256(("paper-smoke-proposal:" + self.run_id).encode()).hexdigest()

    @property
    def decision_id(self):
        return hashlib.sha256(("paper-smoke-decision:" + self.run_id).encode()).hexdigest()

    def digest(self):
        # Equivalent decimal spellings have the same authorization identity.
        payload = {
            "version": "paper-smoke.v1",
            "run_id": self.run_id,
            "account_id": self.account_id,
            "account_fingerprint": self.account_fingerprint,
            "instrument_id": self.instrument_id,
            "market_state_id": self.market_state_id,
            "quantity": "1",
            "limit_price": format(self.limit_price.quantize(Decimal("0.01")), "f"),
            "max_notional": format(self.max_notional.normalize(), "f"),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
