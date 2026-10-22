"""Pure immutable fingerprints for history predating an imported Paper basis.

Hashes preserve the initial collector's original JSON wire representation. They
bind only allowlisted economic fields, never client IDs or provider descriptions.
Callers separately verify account binding and baseline chronology.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from packages.domain.models import utc_time

_ORDER_FIELDS = (
    "symbol", "asset_class", "side", "type", "time_in_force", "qty", "notional",
    "filled_qty", "filled_avg_price", "limit_price", "stop_price", "order_class",
    "replaces", "replaced_by",
)
_ACTIVITY_FIELDS = (
    "symbol", "order_id", "side", "type", "qty", "price", "cum_qty", "leaves_qty",
    "net_amount", "per_share_amount",
)
_TERMINAL = frozenset({"filled", "canceled", "expired", "rejected", "replaced"})


def _invalid():
    raise ValueError("INITIAL_HISTORY_INVALID") from None


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            _invalid()
    except (ValueError, TypeError, AttributeError):
        _invalid()
    return value


def _activity_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or "::" not in value:
        _invalid()
    return value


def _timestamp(value, *, required=False):
    if value is None and not required:
        return None
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 40:
            _invalid()
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            _invalid()
        return stamp
    except (ValueError, TypeError, OverflowError):
        _invalid()


def _terms(raw, names):
    result = {name: raw[name] for name in names if name in raw}
    if any(value is not None and (not isinstance(value, str) or len(value) > 256)
           for value in result.values()):
        _invalid()
    return result


def _fingerprint(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def order_fingerprint(raw: dict) -> str:
    if not isinstance(raw, dict):
        _invalid()
    identity = _uuid(raw.get("id"))
    if not isinstance(raw.get("status"), str) or raw["status"] not in _TERMINAL:
        _invalid()
    submitted = _timestamp(raw.get("submitted_at"))
    updated = _timestamp(raw.get("updated_at"))
    created = _timestamp(raw.get("created_at"))
    if submitted is None and created is None or (
        submitted is not None and updated is not None and submitted > updated
    ):
        _invalid()
    return _fingerprint([
        identity, raw["status"], raw.get("submitted_at"), raw.get("updated_at"),
        raw.get("created_at"), _terms(raw, _ORDER_FIELDS),
    ])


def activity_fingerprint(raw: dict) -> str:
    if not isinstance(raw, dict):
        _invalid()
    identity, kind = _activity_id(raw.get("id")), raw.get("activity_type")
    if not isinstance(kind, str) or re.fullmatch(r"[A-Z]{1,32}", kind) is None:
        _invalid()
    _timestamp(raw.get("transaction_time"), required=kind == "FILL")
    _timestamp(raw.get("created_at"))
    raw_day = raw.get("date")
    if kind != "FILL":
        if isinstance(raw_day, str) and len(raw_day) == 10:
            try:
                date.fromisoformat(raw_day)
            except ValueError:
                _invalid()
        else:
            _timestamp(raw_day, required=True)
    elif raw_day is not None:
        # A date attached to a FILL is still hashed exactly, but must be bounded.
        if not isinstance(raw_day, str) or len(raw_day) > 40:
            _invalid()
    return _fingerprint([
        identity, kind, raw.get("transaction_time"), raw_day, raw.get("created_at"),
        _terms(raw, _ACTIVITY_FIELDS),
    ])


def _anchors(value, *, maximum, orders):
    if type(value) is not tuple or len(value) > maximum:
        _invalid()
    seen = set()
    for pair in value:
        if type(pair) is not tuple or len(pair) != 2:
            _invalid()
        identity, digest = pair
        (_uuid if orders else _activity_id)(identity)
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            _invalid()
        if identity in seen:
            _invalid()
        seen.add(identity)


@dataclass(frozen=True, slots=True, repr=False)
class InitialHistoryProof:
    provider_account_id: str
    baseline_at: datetime
    orders: tuple[tuple[str, str], ...]
    activities: tuple[tuple[str, str], ...]

    def __post_init__(self):
        _uuid(self.provider_account_id)
        try:
            object.__setattr__(self, "baseline_at", utc_time(self.baseline_at))
        except (ValueError, TypeError, AttributeError):
            _invalid()
        _anchors(self.orders, maximum=10000, orders=True)
        _anchors(self.activities, maximum=2000, orders=False)
