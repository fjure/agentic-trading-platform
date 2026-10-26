"""Versioned, research-only identities; registration never activates trading.

Mappings certify the ticker spelling, not historical provider availability. QQQ
starts at the documented QQQQ -> QQQ change; earlier spellings remain unsupported.
MIC identifies the registered listing and is not a claim about historical venues.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from packages.research.models import AssetClass, Instrument, SymbolMapping
from packages.research.storage import ResearchStore

_NAMESPACE = UUID("884eb123-d695-4d05-99c9-f2c01c23b299")
REGISTRY_VERSION = "us-etf-research-v1"
_DEFINITIONS = (
    ("SPY", "ARCX", datetime(1993, 1, 29, tzinfo=UTC)),
    ("QQQ", "XNAS", datetime(2011, 3, 23, tzinfo=UTC)),
)


def definitions():
    return tuple(
        (
            Instrument(uuid5(_NAMESPACE, symbol), AssetClass.ETF, mic, "USD", symbol, "USD"),
            SymbolMapping(uuid5(_NAMESPACE, symbol), "alpaca", symbol, start),
        )
        for symbol, mic, start in _DEFINITIONS
    )


def register(connection):
    """Append idempotently under the caller's short transaction; conflicts fail closed."""
    store = ResearchStore()
    for instrument, mapping in definitions():
        store.add_instrument(connection, instrument)
        store.add_mapping(connection, mapping)
