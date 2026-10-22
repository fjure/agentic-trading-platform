"""Initial financial evidence with a twice-certified private daily risk basis."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from packages.domain.models import decimal_value, utc_time
from packages.risk.paper_daily import DailyRiskObservation, DailyRiskReference, EquityInterval
from services.execution.paper_initial_evidence import (
    InitialPaperEvidence,
    InitialPaperEvidenceCollector,
    _deny,
    _hash,
    _time,
)

# Documented distributions, taxes, charges and interest remain period returns.
# Transfers/journals are deliberately absent: their funding effect needs proof.
_RETURN_KINDS = frozenset({
    "FILL", "DIV", "CGD", "DIVCGL", "DIVCGS", "DIVFEE", "DIVFT", "DIVNRA",
    "DIVROC", "DIVTW", "DIVTXEX", "FEE", "CFEE", "INT", "INTNRA", "INTTW",
    "PTC", "PTR",
})


@dataclass(frozen=True, slots=True, repr=False)
class PreparedPaperEvidence:
    initial: InitialPaperEvidence
    daily_reference: DailyRiskReference
    daily_observation: DailyRiskObservation


@dataclass(frozen=True, slots=True)
class _Proof:
    kind: str
    opening: EquityInterval
    funding: Decimal
    created_at: datetime | None
    source_hash: str


class PaperPreparationCollector(InitialPaperEvidenceCollector):
    """No I/O beyond the inherited fixed GET routes, deadline, size and page bounds."""

    def _prior_activity_proof(self, get, midnight, until, full, current, uncertain):
        from services.execution.paper_initial_evidence import _ACTIVITY_PAGE, _MAX_PAGES

        # Only the API's creation-time filter can establish prior membership
        # when created_at is absent. Settlement and execution dates cannot.
        prior, tokens, token = {}, set(), None
        for _ in range(_MAX_PAGES):
            params = {"until": midnight.isoformat(), "direction": "asc",
                      "page_size": str(_ACTIVITY_PAGE)}
            if token is not None:
                params["page_token"] = token
            rows = get("/v2/account/activities", params=params)
            if not isinstance(rows, list) or len(rows) > _ACTIVITY_PAGE:
                _deny("EXTERNAL_FLOW_UNPROVEN")
            for raw in rows:
                anchor = self._activity(raw, until)
                original = full.get(anchor.activity_id)
                if (original is None or original.evidence_hash != anchor.evidence_hash
                        or anchor.activity_id in prior or anchor.activity_id in current
                        or anchor.created_at is not None and anchor.created_at >= midnight):
                    _deny("EXTERNAL_FLOW_UNPROVEN")
                prior[anchor.activity_id] = anchor.evidence_hash
            if len(rows) < _ACTIVITY_PAGE:
                if not uncertain.issubset(prior):
                    _deny("EXTERNAL_FLOW_UNPROVEN")
                return _hash([midnight, sorted(prior.items())])
            token = rows[-1]["id"]
            if token in tokens:
                _deny("EXTERNAL_FLOW_UNPROVEN")
            tokens.add(token)
        _deny("EXTERNAL_FLOW_UNPROVEN")

    def _current_flows(self, get, midnight, until, full_activities):
        # Filtering is by API creation time, not NTA settlement date. Exhaust the
        # complete current period even when a returned NTA has an older date.
        from services.execution.paper_initial_evidence import _ACTIVITY_PAGE, _MAX_PAGES

        full = {anchor.activity_id: anchor for anchor in full_activities}
        required = {
            anchor.activity_id for anchor in full_activities
            if anchor.created_at is not None and midnight <= anchor.created_at <= until
        }
        total, anchors, tokens, token = Decimal(0), {}, set(), None
        for _ in range(_MAX_PAGES):
            params = {
                "after": midnight.isoformat(),
                "until": until.isoformat(),
                "direction": "asc",
                "page_size": str(_ACTIVITY_PAGE),
            }
            if token is not None:
                params["page_token"] = token
            rows = get("/v2/account/activities", params=params)
            if not isinstance(rows, list) or len(rows) > _ACTIVITY_PAGE:
                _deny("EXTERNAL_FLOW_UNPROVEN")
            for raw in rows:
                anchor = self._activity(raw, until)
                if anchor.activity_id in anchors:
                    _deny("EXTERNAL_FLOW_UNPROVEN")
                prior = full.get(anchor.activity_id)
                if prior is None or prior.evidence_hash != anchor.evidence_hash:
                    _deny("EXTERNAL_FLOW_UNPROVEN")
                if anchor.created_at is not None and anchor.created_at < midnight:
                    _deny("EXTERNAL_FLOW_UNPROVEN")
                amount = Decimal(0)
                if anchor.kind in {"CSD", "CSW"}:
                    if not isinstance(raw.get("net_amount"), str):
                        _deny("EXTERNAL_FLOW_UNPROVEN")
                    amount = decimal_value(raw["net_amount"])
                    if anchor.kind == "CSD" and amount <= 0 or anchor.kind == "CSW" and amount >= 0:
                        _deny("EXTERNAL_FLOW_UNPROVEN")
                elif anchor.kind not in _RETURN_KINDS:
                    # Journals/securities transfers/unknown kinds need their own
                    # authority; never guess that their economic contribution is zero.
                    _deny("EXTERNAL_FLOW_UNPROVEN")
                with localcontext() as arithmetic:
                    arithmetic.prec = 80
                    total += amount
                anchors[anchor.activity_id] = (anchor.evidence_hash, amount)
            if len(rows) < _ACTIVITY_PAGE:
                if not required.issubset(anchors):
                    _deny("EXTERNAL_FLOW_UNPROVEN")
                uncertain = {anchor.activity_id for anchor in full_activities
                             if anchor.created_at is None and anchor.activity_id not in anchors}
                prior_hash = self._prior_activity_proof(
                    get, midnight, until, full, anchors, uncertain,
                ) if uncertain else None
                return total, _hash([sorted(anchors.items()), prior_hash])
            token = rows[-1]["id"]
            if token in tokens:
                _deny("EXTERNAL_FLOW_UNPROVEN")
            tokens.add(token)
        _deny("EXTERNAL_FLOW_UNPROVEN")

    @staticmethod
    def _opening(get, midnight):
        label = midnight - timedelta(minutes=1)
        params = {
            "start": label.isoformat(),
            "end": midnight.isoformat(),
            "timeframe": "1Min",
            "intraday_reporting": "continuous",
            "pnl_reset": "no_reset",
            "cashflow_types": "ALL",
        }
        raw = get("/v2/account/portfolio/history", params=params)
        if (
            not isinstance(raw, dict)
            or raw.get("timeframe") != "1Min"
            or not isinstance(raw.get("timestamp"), list)
            or len(raw["timestamp"]) != 1
            or not isinstance(raw.get("equity"), list)
            or len(raw["equity"]) != 1
        ):
            _deny("DAILY_REFERENCE_UNPROVEN")
        epoch, value = raw["timestamp"][0], raw["equity"][0]
        if (
            isinstance(epoch, bool)
            or not isinstance(epoch, (int, Decimal))
            or epoch != int(epoch)
            or isinstance(value, bool)
            or not isinstance(value, (int, Decimal))
        ):
            _deny("DAILY_REFERENCE_UNPROVEN")
        if datetime.fromtimestamp(int(epoch), UTC) != label:
            _deny("DAILY_REFERENCE_UNPROVEN")
        opening = EquityInterval.reported_cents(decimal_value(Decimal(value)))
        if opening.lower <= 0:
            _deny("DAILY_REFERENCE_UNPROVEN")
        # Preserve only authoritative normalized fields, not arbitrary provider text.
        return opening, _hash([params, int(epoch), Decimal(value), "window-end", "cent+-0.01"])

    def _snapshot(self, client, credentials, deadline):
        snapshot = super()._snapshot(client, credentials, deadline)

        def get(route, **kwargs):
            return self._get(client, route, credentials, deadline, **kwargs)

        account = get("/v2/account")
        if self._account(account) != snapshot.account:
            _deny("INITIAL_EVIDENCE_CHANGED")
        midnight = snapshot.observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
        created = _time(account["created_at"]) if account.get("created_at") is not None else None
        if created is not None and created > snapshot.provider_time:
            _deny("DAILY_REFERENCE_UNPROVEN")
        if (
            created is not None
            and midnight < created <= snapshot.provider_time
            and not snapshot.positions
            and all(order.untraded for order in snapshot.orders)
            and all(a.kind in {"CSD", "CSW"} for a in snapshot.activities)
        ):
            # Both full snapshots must certify no trade/return history and the
            # same funded cash. The reference itself is bound at final observation.
            opening = EquityInterval(snapshot.account[1], snapshot.account[1])
            proof = _Proof(
                "FIRST_DAY",
                opening,
                Decimal(0),
                created,
                _hash(["FIRST_DAY", created, snapshot.digest]),
            )
        else:
            if created is not None and created > midnight:
                # The account did not exist at this UTC boundary. A synthetic
                # positive history point cannot legitimize its already traded day.
                _deny("DAILY_REFERENCE_UNPROVEN")
            opening, source = self._opening(get, midnight)
            funding, flow_hash = self._current_flows(
                get, midnight, snapshot.provider_time, snapshot.activities,
            )
            proof = _Proof("UTC_DAY", opening, funding, None, _hash([source, flow_hash, funding]))
        self._proofs.append(proof)
        observed = utc_time(self._clock.now())
        if observed < snapshot.observed_at or not -timedelta(
            seconds=5
        ) <= observed - snapshot.provider_time < timedelta(seconds=30):
            _deny("INITIAL_EVIDENCE_STALE")
        return replace(
            snapshot, observed_at=observed, digest=_hash([snapshot.digest, proof.source_hash])
        )

    def collect(self, generation, identity_proof):
        self._proofs = []
        initial = super().collect(generation, identity_proof)
        if len(self._proofs) != 2 or self._proofs[0] != self._proofs[1]:
            _deny("INITIAL_EVIDENCE_CHANGED")
        proof = self._proofs[1]
        midnight = initial.confirmed_at.replace(hour=0, minute=0, second=0, microsecond=0)
        if initial.first_read_at < midnight:
            _deny("DAILY_REFERENCE_UNPROVEN")
        reference = DailyRiskReference(
            kind=proof.kind,
            starts_at=initial.confirmed_at if proof.kind == "FIRST_DAY" else midnight,
            expires_at=midnight + timedelta(days=1),
            opening_equity=proof.opening,
            capital_lower=proof.opening.lower,
            evidence_hash=_hash([initial.evidence_hash, proof.source_hash]),
            account_created_at=proof.created_at,
        )
        observation = DailyRiskObservation(
            reference.evidence_hash,
            initial.confirmed_at,
            EquityInterval(initial.equity, initial.equity),
            EquityInterval(proof.funding, proof.funding),
            True,
            initial.evidence_hash,
        )
        return PreparedPaperEvidence(initial, reference, observation)
