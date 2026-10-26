"""Credential-free stored research projections; no provider or trading dependencies."""

from __future__ import annotations

import base64
import hmac
import json
import re
import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text

from packages.domain.models import decimal_string, utc_string
from packages.research.calendar import expected_windows
from packages.research.calendar_storage import read_calendar
from packages.research.context import build_market_context
from packages.research.jobs import JobPosition, ResearchJobError, ResearchJobs
from packages.research.models import (
    Adjustment,
    BarInterval,
    EvidenceType,
    PointInTimeQuery,
    QualityStatus,
)
from packages.research.ports import LicenseStatus, SourceCapability, SourceStatus
from packages.research.quality import assess_coverage
from packages.research.quality_storage import rejected_quality
from packages.research.query import BarPosition, ResearchQuery, filter_fingerprint
from packages.research.sources import research_source_catalog
from packages.research.storage import evidence_for_bar

_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")


def timestamp(value):
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        raise ValueError("UTC timestamp required")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def identity(value):
    result = UUID(value)
    if str(result) != value:
        raise ValueError("canonical UUID required")
    return result


def snapshot_public(query):
    return dict(
        valuationAt=utc_string(query.valuation_at),
        knowledgeAt=utc_string(query.knowledge_at),
        snapshotAt=utc_string(query.snapshot_at),
        snapshotVersion=query.snapshot_version,
        semantics="observed-as-of",
    )


def bar_public(bar):
    item = bar.identity
    return dict(
        barId=str(bar.revision_id),
        instrumentId=str(item.instrument_id),
        provider="ALPACA",
        feed=item.feed,
        interval=item.interval.value,
        adjustment=item.adjustment.value,
        currency="USD",
        start=utc_string(item.bar_start),
        end=utc_string(item.bar_end),
        **{
            name: decimal_string(getattr(bar, name))
            for name in ["open", "high", "low", "close", "volume"]
        },
        revision=bar.local_revision,
        predecessorId=str(bar.predecessor_id) if bar.predecessor_id else None,
        providerRevision=bar.provider_revision,
        eventTime=utc_string(item.bar_end),
        publishedAt=None,
        availableAt=utc_string(bar.available_at),
        ingestedAt=utc_string(bar.ingested_at),
        availabilityBasis="FIRST_OBSERVED",
        fingerprint=bar.fingerprint,
        evidenceId=str(evidence_for_bar(bar).evidence_id),
        quality=bar.quality.value,
        commitVersion=bar.commit_version,
    )


def evidence_public(evidence, predecessor=None):
    return dict(
        evidenceId=str(evidence.evidence_id),
        instrumentId=str(evidence.instrument_id) if evidence.instrument_id else None,
        market=evidence.market,
        sourceId=evidence.source,
        sourceReference=evidence.source_reference,
        eventTime=utc_string(evidence.event_time),
        publishedAt=None,
        availableAt=utc_string(evidence.available_at),
        ingestedAt=utc_string(evidence.ingested_at),
        availabilityBasis=evidence.availability_basis.value,
        schemaVersion="1",
        dataRevision=evidence.data_revision,
        predecessorId=predecessor,
        fingerprint=evidence.fingerprint,
        quality=evidence.quality.value,
        modelConfidence=None,
        content=dict(kind="BAR", barId=str(evidence.content.revision_id)),
        commitVersion=evidence.commit_version,
    )


class ResearchService:
    def __init__(self, engine, *, clock=None, bars_capability=None):
        self.engine = engine
        self.clock = clock or (lambda: datetime.now(UTC))
        self.query = ResearchQuery()
        self.jobs = ResearchJobs(engine)
        self._cursor_key = secrets.token_bytes(32)
        self.bars_capability = bars_capability or SourceCapability(
            "alpaca-iex-bars",
            EvidenceType.BAR,
            SourceStatus.DISABLED,
            LicenseStatus.UNKNOWN,
            ("iex",),
            (BarInterval.FIFTEEN_MINUTES, BarInterval.ONE_DAY),
            quality_limits=(
                "FIRST_OBSERVED_ONLY",
                "IEX_NOT_FULL_MARKET",
                "NO_HISTORICAL_PUBLICATION",
            ),
        )

    def _encode(self, body):
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        return (
            base64.urlsafe_b64encode(raw + hmac.digest(self._cursor_key, raw, "sha256"))
            .decode()
            .rstrip("=")
        )

    def _decode(self, token, resource):
        try:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", token):
                raise ValueError()
            raw = base64.urlsafe_b64decode(token + "=" * ((-len(token)) % 4))
            data, signature = raw[:-32], raw[-32:]
            if not hmac.compare_digest(signature, hmac.digest(self._cursor_key, data, "sha256")):
                raise ValueError()
            body = json.loads(data)
            if body["resource"] != resource:
                raise ValueError()
            return body
        except (ValueError, TypeError, KeyError):
            raise ResearchJobError(400, "CURSOR_INVALID") from None

    def read_sources(self):
        with self.engine.connect() as connection:
            status = (
                connection.execute(text("SELECT * FROM research.source_status WHERE singleton"))
                .mappings()
                .one()
            )
            rows = (
                connection.execute(
                    text("""SELECT i.* FROM research.instruments i
              WHERE EXISTS(SELECT 1 FROM research.symbol_mappings m
                WHERE m.instrument_id=i.instrument_id AND m.provider='alpaca'
                  AND m.symbol IN ('SPY','QQQ'))
              ORDER BY instrument_id LIMIT 2""")
                )
                .mappings()
                .all()
            )
        instruments = [
            dict(
                instrumentId=str(r["instrument_id"]),
                assetClass=r["asset_class"],
                venue=r["mic"],
                currency=r["currency"],
                baseUnit=r["base_unit"],
                quoteUnit=r["quote_unit"],
            )
            for r in rows
        ]
        items = []
        capability = SourceCapability(
            self.bars_capability.source,
            EvidenceType.BAR,
            SourceStatus(status["state"]),
            LicenseStatus(status["license_status"]),
            ("iex",),
            (BarInterval.FIFTEEN_MINUTES, BarInterval.ONE_DAY),
            quality_limits=self.bars_capability.quality_limits,
        )
        for source in research_source_catalog(capability):
            bars = source.evidence_type == EvidenceType.BAR
            limitations = (
                [
                    "FIRST_OBSERVED_ONLY",
                    "IEX_NOT_FULL_MARKET",
                    "NO_HISTORICAL_PUBLICATION",
                ]
                if bars
                else ["NO_ADAPTER"]
            )
            if source.license_status != LicenseStatus.CONFIRMED:
                limitations.append("RIGHTS_UNVERIFIED")
            items.append(
                dict(
                    sourceId=source.name,
                    kind=source.evidence_type.value if source.evidence_type else "REGIME",
                    state=source.status.value,
                    licenseStatus=source.license_status.value,
                    policyVersion=status["policy_version"] if bars else None,
                    providerVerification=status["provider_verification"] if bars else "UNVERIFIED",
                    instruments=instruments if bars else [],
                    intervals=["15Min", "1Day"] if bars else [],
                    feed="iex" if bars else None,
                    adjustment="raw" if bars else None,
                    historyStart=None,
                    historyEnd=None,
                    limitations=limitations,
                )
            )
        return dict(items=items, observedAt=utc_string(self.clock()), incomplete=True)

    def _pit(self, connection, values, resource):
        cursor = self._decode(values["cursor"], resource) if values.get("cursor") else None
        now = self.clock()
        valuation = (
            timestamp(values["valuationAt"])
            if "valuationAt" in values
            else (timestamp(cursor["snapshot"]["valuationAt"]) if cursor else now)
        )
        knowledge = (
            timestamp(values["knowledgeAt"])
            if "knowledgeAt" in values
            else (timestamp(cursor["snapshot"]["knowledgeAt"]) if cursor else valuation)
        )
        if cursor:
            snap = cursor["snapshot"]
            query = PointInTimeQuery(
                valuation, knowledge, timestamp(snap["snapshotAt"]), snap["snapshotVersion"]
            )
        else:
            query = self.query.snapshot(connection, valuation, knowledge)
        return query, cursor

    def _bars(self, connection, values, resource):
        instrument, start, end, interval = (
            identity(values["instrumentId"]),
            timestamp(values["start"]),
            timestamp(values["end"]),
            BarInterval(values["interval"]),
        )
        if values.get("feed", "iex") != "iex" or values.get("adjustment", "raw") != "raw":
            raise ValueError("unsupported feed or adjustment")
        if not connection.execute(
            text("SELECT 1 FROM research.instruments WHERE instrument_id=:id"), dict(id=instrument)
        ).first():
            raise ResearchJobError(404, "NOT_FOUND")
        query, cursor = self._pit(connection, values, resource)
        bound = filter_fingerprint(instrument, start, end, interval, query)
        after = None
        if cursor:
            if cursor["filter"] != bound:
                raise ResearchJobError(409, "SNAPSHOT_CONFLICT")
            position = cursor["position"]
            after = BarPosition(
                timestamp(position[0]),
                timestamp(position[1]),
                position[2],
                identity(position[3]),
                bound,
            )
        page = self.query.list_bars(
            connection,
            instrument,
            start,
            end,
            interval,
            query,
            limit=int(values.get("limit", "100")),
            after=after,
        )
        coverage_start = start if after is None else after.bar_end
        coverage_end = (
            page.items[-1].identity.bar_end
            if page.next_position is not None and page.items
            else end
        )
        calendar = read_calendar(connection, coverage_start, coverage_end, query)
        coverage = assess_coverage(
            instrument_id=instrument,
            feed="iex",
            interval=interval,
            adjustment=Adjustment.RAW,
            start=coverage_start,
            end=coverage_end,
            valuation_at=query.valuation_at,
            calendar=calendar,
            bars=page.items,
        )
        rejected = rejected_quality(connection, instrument, start, end, interval, query)
        coverage_status = (
            rejected
            if rejected in {QualityStatus.INVALID, QualityStatus.CONFLICT}
            else coverage.quality
        )
        result = dict(
            instrumentId=str(instrument),
            provider="ALPACA",
            feed="iex",
            interval=interval.value,
            adjustment="raw",
            start=utc_string(coverage_start),
            end=utc_string(coverage_end),
            qualityRuleVersion=coverage.rule_version,
            calendarVersion=coverage.calendar_version,
            status=coverage_status.value,
            expected=coverage.expected,
            observed=coverage.observed,
            missing=coverage.missing,
            unchecked=len(coverage.unchecked),
        )
        next_cursor = None
        if page.next_position:
            p = page.next_position
            next_cursor = self._encode(
                dict(
                    resource=resource,
                    filter=bound,
                    snapshot=snapshot_public(query),
                    position=[
                        utc_string(p.bar_start),
                        utc_string(p.bar_end),
                        p.provider,
                        str(p.revision_id),
                    ],
                )
            )
        return instrument, start, end, interval, query, page, calendar, result, next_cursor

    def read_bars(self, values):
        with self.engine.connect() as connection:
            _, _, _, _, query, page, _, coverage, cursor = self._bars(connection, values, "bars")
        return dict(
            items=[bar_public(b) for b in page.items],
            nextCursor=cursor,
            snapshot=snapshot_public(query),
            coverage=[coverage],
            incomplete=coverage["status"] != "VALID" or cursor is not None,
            dataVersion=query.snapshot_version,
        )

    def read_evidence(self, evidence_id, values):
        with self.engine.connect() as connection:
            query, _ = self._pit(connection, values, "evidence")
            item = self.query.get_evidence(connection, identity(evidence_id), query)
            if item is None:
                raise ResearchJobError(404, "NOT_FOUND")
            predecessor = connection.execute(
                text("""SELECT e.evidence_id FROM research.bar_revisions b
              JOIN research.evidence e ON e.revision_id=b.predecessor_id
              WHERE b.revision_id=:id"""),
                dict(id=item.content.revision_id),
            ).scalar_one_or_none()
        return dict(
            evidence=evidence_public(item, str(predecessor) if predecessor else None),
            snapshot=snapshot_public(query),
            incomplete=True,
            dataVersion=query.snapshot_version,
        )

    def read_context(self, values):
        with self.engine.connect() as connection:
            instrument, start, end, interval, query, page, calendar, coverage, cursor = self._bars(
                connection, values, "context"
            )
            evidence = []
            for bar in page.items:
                item = self.query.get_evidence(connection, evidence_for_bar(bar).evidence_id, query)
                if item:
                    evidence.append(item)
            windows = expected_windows(calendar, interval, start, end, query.valuation_at)
            context = build_market_context(
                instrument,
                start,
                end,
                interval,
                query,
                page.items,
                tuple(evidence),
                complete_window=coverage["status"] == "VALID" and cursor is None,
                expected_windows=windows.windows,
            )

        def metric(value):
            valid = value.value is not None
            return dict(
                status=value.status.value,
                value=decimal_string(value.value) if valid else None,
                algorithmVersion=context.calculation_version,
                evidenceIds=[str(e.evidence_id) for e in evidence] if valid else [],
                reason=None
                if valid
                else "INSUFFICIENT_BARS"
                if len(page.items) < 3
                else "INCOMPLETE_WINDOW",
            )

        return dict(
            instrumentId=str(instrument),
            start=utc_string(start),
            end=utc_string(end),
            interval=interval.value,
            snapshot=snapshot_public(query),
            dataVersion=context.data_version,
            quality=(
                coverage["status"]
                if coverage["status"] != QualityStatus.VALID.value
                else context.quality.value
            ),
            coverage=[coverage],
            evidenceIds=[str(e) for e in context.evidence_ids],
            priceChange=metric(context.price_change),
            volatility=metric(context.volatility),
            missingSources=["NEWS", "FUNDAMENTALS", "MACRO", "REGIME"]
            + (["BARS"] if EvidenceType.BAR in context.missing_sources else []),
            incomplete=True,
        )

    def create_job(self, payload, actor, key, trace):
        return self.jobs.enqueue(payload, actor, key, trace)

    def resume_job(self, payload, actor, key, trace):
        if actor != "local-operator":
            raise ResearchJobError(403, "FORBIDDEN")
        return self.jobs.resume(identity(payload["jobId"]), actor, key, trace)

    def read_job(self, job_id):
        result = self.jobs.read_job(identity(job_id))
        if result is None:
            raise ResearchJobError(404, "NOT_FOUND")
        return result

    def read_jobs(self, values):
        cursor = self._decode(values["cursor"], "jobs") if values.get("cursor") else None
        after = JobPosition(timestamp(cursor["at"]), identity(cursor["id"])) if cursor else None
        page = self.jobs.list_jobs(int(values.get("limit", "100")), after=after)
        next_cursor = (
            self._encode(
                dict(
                    resource="jobs",
                    at=utc_string(page.next_position.accepted_at),
                    id=str(page.next_position.job_id),
                )
            )
            if page.next_position
            else None
        )
        with self.engine.connect() as connection:
            now = self.clock()
            snapshot = self.query.snapshot(connection, now, now)
        return dict(
            items=list(page.items),
            nextCursor=next_cursor,
            snapshotAt=utc_string(snapshot.snapshot_at),
            snapshotVersion=snapshot.snapshot_version,
        )
