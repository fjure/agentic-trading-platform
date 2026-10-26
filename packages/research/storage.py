"""Append-only normalized research writes inside a caller-owned transaction.

Call only after provider I/O has finished. reserve_version serializes short SQL
mutations until commit; its database lock timeout is one second. Never commit here.
"""

import re
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.engine import Connection

from packages.research.models import (
    Adjustment,
    BarContent,
    BarIdentity,
    BarInterval,
    BarRevision,
    EvidenceType,
    Instrument,
    QualityStatus,
    ResearchEvidence,
    SymbolMapping,
    content_fingerprint,
)
from packages.research.ports import SourceError, SourceErrorCode

_NAMESPACE = UUID("365af704-dca0-43c8-9627-df38a1f5f102")
_NATURAL_KEY = """instrument_id=:instrument_id AND provider=:provider AND feed=:feed
 AND interval=:interval AND bar_start=:bar_start AND bar_end=:bar_end
 AND adjustment=:adjustment"""


def identity_parameters(identity: BarIdentity) -> dict:
    return {
        **asdict(identity),
        "interval": identity.interval.value,
        "adjustment": identity.adjustment.value,
    }


def prepare_bar_revision(bar: BarRevision, head: BarRevision | None) -> BarRevision:
    """Head repeats are idempotent; recurring content after a correction is a new occurrence."""
    if head is not None:
        if head.identity != bar.identity:
            raise ValueError("revision head must have the same natural identity")
        if head.fingerprint == bar.fingerprint:
            return head
    revision = head.local_revision + 1 if head is not None else 1
    predecessor = head.revision_id if head is not None else None
    occurrence = content_fingerprint(("bar-occurrence-v1", bar.fingerprint, revision, predecessor))
    return replace(
        bar,
        revision_id=uuid5(_NAMESPACE, occurrence),
        local_revision=revision,
        predecessor_id=predecessor,
    )


def bar_from_row(row: Mapping) -> BarRevision:
    """Rehydrate checked database values; first-observed availability is DB ingestion."""
    return BarRevision(
        revision_id=row["revision_id"],
        identity=BarIdentity(
            row["instrument_id"],
            row["provider"],
            row["feed"],
            BarInterval(row["interval"]),
            row["bar_start"],
            row["bar_end"],
            Adjustment(row["adjustment"]),
        ),
        open=row["open"],
        high=row["high"],
        low=row["low"],
        close=row["close"],
        volume=row["volume"],
        ingested_at=row["ingested_at"],
        available_at=row["ingested_at"],
        published_at=row["published_at"],
        local_revision=row["local_revision"],
        predecessor_id=row["predecessor_id"],
        provider_revision=row["provider_revision"],
        commit_version=row["commit_version"],
        quality=QualityStatus(row["quality"]),
    )


def evidence_for_bar(
    bar: BarRevision,
    source_reference: str | None = None,
    *,
    ingested_at: datetime | None = None,
    commit_version: int | None = None,
) -> ResearchEvidence:
    observed = ingested_at if ingested_at is not None else bar.ingested_at
    evidence = ResearchEvidence(
        evidence_id=UUID(int=0),
        evidence_type=EvidenceType.BAR,
        source=bar.identity.provider,
        source_reference=source_reference or f"bar:{bar.fingerprint}",
        instrument_id=bar.identity.instrument_id,
        market="US_EQUITY",
        event_time=bar.identity.bar_end,
        published_at=bar.published_at,
        available_at=observed,
        ingested_at=observed,
        content=BarContent(bar.revision_id, bar.fingerprint),
        quality=bar.quality,
        data_revision=bar.local_revision,
        commit_version=bar.commit_version if commit_version is None else commit_version,
    )
    return replace(evidence, evidence_id=evidence.deterministic_id)


class ResearchStore:
    @staticmethod
    def _reserve(connection: Connection) -> None:
        connection.execute(text("SELECT research.reserve_version()"))

    def add_instrument(self, connection: Connection, instrument: Instrument) -> None:
        self._reserve(connection)
        parameters = asdict(instrument)
        parameters["asset_class"] = instrument.asset_class.value
        previous = (
            connection.execute(
                text(
                    "SELECT instrument_id,asset_class,mic,currency,base_unit,quote_unit "
                    "FROM research.instruments WHERE instrument_id=:instrument_id"
                ),
                parameters,
            )
            .mappings()
            .first()
        )
        if previous is not None:
            if dict(previous) != parameters:
                raise ValueError("instrument identity conflicts with stored definition")
            return
        connection.execute(
            text("""
            INSERT INTO research.instruments
            (instrument_id,asset_class,mic,currency,base_unit,quote_unit)
            VALUES(:instrument_id,:asset_class,:mic,:currency,:base_unit,:quote_unit)
        """),
            parameters,
        )

    def add_mapping(self, connection: Connection, mapping: SymbolMapping) -> None:
        self._reserve(connection)
        parameters = asdict(mapping)
        parameters["mapping_id"] = uuid5(_NAMESPACE, content_fingerprint(mapping))
        if (
            connection.execute(
                text(
                    "SELECT mapping_id FROM research.symbol_mappings WHERE mapping_id=:mapping_id"
                ),
                parameters,
            ).first()
            is not None
        ):
            return
        connection.execute(
            text("""
            INSERT INTO research.symbol_mappings
            (mapping_id,instrument_id,provider,symbol,valid_from,valid_to)
            VALUES(:mapping_id,:instrument_id,:provider,:symbol,:valid_from,:valid_to)
        """),
            parameters,
        )

    def append_bar(self, connection: Connection, bar: BarRevision, *, symbol: str) -> BarRevision:
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9./-]{0,15}", symbol):
            raise ValueError("explicit valid provider symbol required")
        if bar.published_at is not None or bar.schema_version != 1:
            raise ValueError("only schema-v1 first-observed raw bars are certified")
        self._reserve(connection)
        parameters = identity_parameters(bar.identity)
        parameters["provider_symbol"] = symbol
        asset = connection.execute(
            text("SELECT asset_class FROM research.instruments WHERE instrument_id=:instrument_id"),
            parameters,
        ).scalar_one_or_none()
        if asset not in {"EQUITY", "ETF"}:
            raise ValueError("raw IEX research bars require a registered equity or ETF")
        mapping = connection.execute(
            text("""
            SELECT mapping_id FROM research.symbol_mappings
            WHERE instrument_id=:instrument_id AND provider=:provider AND symbol=:provider_symbol
              AND valid_from<=:bar_start AND (valid_to IS NULL OR valid_to>=:bar_end)
            LIMIT 1
        """),
            parameters,
        ).first()
        if mapping is None:
            raise ValueError("provider symbol mapping must cover the entire bar interval")
        parameters["fingerprint"] = bar.fingerprint
        parameters["provider_revision"] = bar.provider_revision
        previous = (
            connection.execute(
                text(
                    "SELECT * FROM research.bar_revisions WHERE "
                    + _NATURAL_KEY
                    + " ORDER BY local_revision DESC LIMIT 1"
                ),
                parameters,
            )
            .mappings()
            .first()
        )
        head = bar_from_row(previous) if previous is not None else None
        candidate = prepare_bar_revision(bar, head)
        if candidate is head:
            return head
        if bar.provider_revision is not None:
            conflict = connection.execute(
                text(
                    "SELECT revision_id FROM research.bar_revisions WHERE "
                    + _NATURAL_KEY
                    + " AND provider_revision=:provider_revision"
                    + " AND fingerprint<>:fingerprint LIMIT 1"
                ),
                parameters,
            ).first()
            if conflict is not None:
                raise SourceError(SourceErrorCode.INVALID_RESPONSE)
        parameters.update(
            {
                "revision_id": candidate.revision_id,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "provider_revision": bar.provider_revision,
                "quality": bar.quality.value,
                "local_revision": candidate.local_revision,
                "predecessor_id": candidate.predecessor_id,
            }
        )
        row = (
            connection.execute(
                text("""
            INSERT INTO research.bar_revisions
            (revision_id,instrument_id,provider,provider_symbol,feed,interval,bar_start,bar_end,adjustment,
             open,high,low,close,volume,fingerprint,provider_revision,quality,
             local_revision,predecessor_id,published_at)
            VALUES(:revision_id,:instrument_id,:provider,:provider_symbol,:feed,:interval,
             :bar_start,:bar_end,
             :adjustment,:open,:high,:low,:close,:volume,:fingerprint,:provider_revision,
             :quality,:local_revision,:predecessor_id,NULL) RETURNING *
        """),
                parameters,
            )
            .mappings()
            .one()
        )
        persisted = bar_from_row(row)
        evidence = evidence_for_bar(persisted)
        connection.execute(
            text("""
            INSERT INTO research.evidence(evidence_id,revision_id,fingerprint,source_reference)
            VALUES(:evidence_id,:revision_id,:fingerprint,:source_reference)
        """),
            {
                "evidence_id": evidence.evidence_id,
                "revision_id": persisted.revision_id,
                "fingerprint": evidence.fingerprint,
                "source_reference": evidence.source_reference,
            },
        )
        return persisted
