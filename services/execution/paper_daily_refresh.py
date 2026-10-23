"""Bounded read-only daily evidence refresh; never reinitializes the ledger."""

from datetime import timedelta
from time import monotonic

import httpx

from packages.domain.models import utc_time
from services.execution.paper_credentials import PrivatePaperCredentials
from services.execution.paper_daily_evidence import PaperPreparationCollector
from services.execution.paper_initial_evidence import _deny, _hash, _time


class PaperDailyRefreshCollector(PaperPreparationCollector):
    def _daily(self, get, reference):
        now = utc_time(self._clock.now())
        provider = get("/v2/clock")
        if not isinstance(provider, dict):
            _deny("DAILY_REFERENCE_UNPROVEN")
        until = _time(provider.get("timestamp"))
        if not -timedelta(seconds=5) <= now - until < timedelta(seconds=30):
            _deny("INITIAL_EVIDENCE_STALE")
        account = self._account(get("/v2/account"))
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        ref = dict(reference)
        for key in ("startsAt", "expiresAt", "accountCreatedAt"):
            if ref.get(key) is not None:
                ref[key] = _time(ref[key]).isoformat().replace("+00:00", "Z")
        try:
            start, expiry = _time(ref["startsAt"]), _time(ref["expiresAt"])
        except (KeyError, ValueError, TypeError):
            _deny("DAILY_REFERENCE_UNPROVEN")
        if now >= expiry:
            # A real provider boundary is required each new UTC day. No renewal
            # of FIRST_DAY, cash reset or reuse of yesterday's observation.
            opening, source = self._opening(get, midnight)
            ref = {
                "kind": "UTC_DAY", "startsAt": midnight.isoformat().replace("+00:00", "Z"),
                "expiresAt": (midnight + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                "openingLower": str(opening.lower), "openingUpper": str(opening.upper),
                "capitalLower": str(opening.lower), "referenceHash": source,
                "accountCreatedAt": None,
            }
            start, expiry = midnight, midnight + timedelta(days=1)
        if not start <= until < expiry or not start <= now < expiry:
            _deny("DAILY_REFERENCE_UNPROVEN")
        activities = self._activities(get, until)
        funding, flow_hash = self._current_flows(get, start, until, activities)
        if self._account(get("/v2/account")) != account:
            _deny("INITIAL_EVIDENCE_CHANGED")
        return ref, account[2], funding, flow_hash, until

    def collect(self, generation, reference):
        try:
            credentials = self._credentials.load(generation)
            if (type(credentials) is not PrivatePaperCredentials
                    or credentials.generation != generation
                    or credentials.expected_account_id != self._expected
                    or self._pinned != self._expected):
                _deny("ACCOUNT_IDENTITY_MISMATCH")
            started = utc_time(self._clock.now())
            deadline = monotonic() + 30
            with httpx.Client(
                transport=self._transport, trust_env=False, follow_redirects=False,
                proxy="http://paper-execution-egress:8080" if self._transport is None else None,
                timeout=httpx.Timeout(5.),
            ) as client:
                def get(route, **kwargs):
                    return self._get(client, route, credentials, deadline, **kwargs)

                first = self._daily(get, reference)
                second = self._daily(get, reference)
            # Provider clock can advance; all financial proofs must agree.
            if first[:4] != second[:4] or second[4] < first[4]:
                _deny("INITIAL_EVIDENCE_CHANGED")
            observed = utc_time(self._clock.now())
            if not timedelta() <= observed - started < timedelta(seconds=30):
                _deny("INITIAL_EVIDENCE_STALE")
            ref, equity, funding, flow_hash, until = second
            return ref | {
                "observedAt": observed.isoformat().replace("+00:00", "Z"),
                "currentEquity": str(equity),
                "externalFunding": str(funding),
                "observationHash": _hash([ref, equity, funding, flow_hash, until, observed]),
            }
        except Exception as failure:
            from services.execution.paper_initial_evidence import InitialEvidenceUnavailable

            if type(failure) is InitialEvidenceUnavailable:
                raise
            _deny("DAILY_REFERENCE_UNPROVEN")
