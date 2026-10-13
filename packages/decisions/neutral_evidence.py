"""Deterministic, neutral observations derived only from admitted market bars."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from decimal import Decimal, localcontext
from types import MappingProxyType

from packages.domain.models import EvidenceBundle, MarketBar, Quality, decimal_string, decimal_value


def neutral_evidence(
    bars: tuple[MarketBar, ...], *, source_id: str, config_version: str
) -> tuple[EvidenceBundle, Mapping[str, Decimal]]:
    """Return an immutable observation without direction, sizing, or broker actions."""
    if not bars or not source_id or not config_version:
        raise ValueError("bars, source and configuration version are required")
    if any(
        bar.instrument_id != bars[0].instrument_id or bar.quality is not Quality.VALID
        for bar in bars
    ):
        raise ValueError("neutral evidence requires valid bars for one instrument")
    def bar_identity(bar: MarketBar) -> str:
        prices = ",".join(
            decimal_string(getattr(bar, name))
            for name in ("open", "high", "low", "close", "volume")
        )
        return (
            f"{bar.id}:{bar.version}:{bar.start_at.isoformat()}:{bar.end_at.isoformat()}:"
            f"{bar.event_time.isoformat()}:{bar.received_at.isoformat()}:"
            f"{bar.available_at.isoformat()}:{prices}"
        )

    identity = "|".join(
        (
            config_version,
            source_id,
            *(bar_identity(bar) for bar in bars),
        )
    )
    evidence_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    bundle = EvidenceBundle(
        id=evidence_id,
        instrument_id=bars[0].instrument_id,
        sources=(source_id,),
        available_at=max(bar.available_at for bar in bars),
        conflicts=(),
        quality=Quality.VALID,
    )
    # Decimal's default 28-digit context would round a legal NUMERIC(38,18) sum.
    with localcontext() as context:
        context.prec = 80
        window_volume = sum((bar.volume for bar in bars), start=Decimal("0"))
    window_volume = decimal_value(window_volume)
    # The domain MarketState validates and freezes Decimal feature values.
    features = MappingProxyType(
        {
            "last_close": bars[-1].close,
            "previous_close": bars[-2].close if len(bars) > 1 else bars[-1].close,
            "window_volume": window_volume,
        }
    )
    return bundle, features
