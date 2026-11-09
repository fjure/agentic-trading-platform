"""Durable attempt reservation and one-shot admission before provider I/O."""

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, RowMapping

from .budget import (
    BudgetAmount,
    BudgetDenied,
    BudgetWindow,
    ModelOutcome,
    ModelReservation,
    TokenPrices,
    Tokens,
    reconcile_model,
    require_capacity,
    reserve_model,
)
from .registry import ModelBinding, require_agent
from .runs import Claim, RunConflict, _agent_binding

_HASH = re.compile(r"[0-9a-f]{64}")
_REFERENCE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def _scope(kind: str, *binding: str | int) -> str:
    digest = sha256(json.dumps(binding, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"{kind}:{digest}"


def model_budget_scopes(
    agent_id: str, agent_version: int, kind: str, model: ModelBinding, input_ref: str,
) -> tuple[str, ...]:
    """Return policy keys. A scheduled run's input reference identifies its schedule."""
    scopes = (
        _scope("global"), _scope("agent", agent_id),
        _scope("agent_version", agent_id, agent_version),
        _scope("work", kind), _scope("provider", model.provider),
        _scope("model", model.provider, model.model_id),
    )
    if kind == "SCHEDULED":
        scopes += (_scope("schedule", input_ref),)
    return tuple(sorted(scopes))


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: UUID
    status: str
    result_ref: str | None
    step_id: str | None = None


def _attempt(row: RowMapping) -> Attempt:
    return Attempt(row["attempt_id"], row["status"], row["result_ref"], row["step_id"])


def _lock_run(connection: Connection, claim: Claim) -> RowMapping:
    if connection.get_isolation_level() != "READ COMMITTED":
        raise RunConflict("AGENT_POLICY_DENIED")
    row = connection.execute(text("""
        SELECT *, (created_at AT TIME ZONE 'UTC')::date AS budget_period,
            (clock_timestamp() AT TIME ZONE 'UTC')::date AS current_period
        FROM agent_research.runs
        WHERE run_id=:id AND owner=:owner AND fence=:fence AND version=:version
          AND state='RUNNING' AND lease_until>clock_timestamp() AND deadline_at>clock_timestamp()
        FOR UPDATE
    """), {"id": claim.run_id, "owner": claim.owner, "fence": claim.fence,
           "version": claim.version}).mappings().one_or_none()
    if row is None:
        raise RunConflict("LEASE_LOST")
    return row


def _lock_attempt(connection: Connection, claim: Claim, attempt_id: UUID) -> RowMapping:
    row = connection.execute(text("""
        SELECT * FROM agent_research.model_attempts
        WHERE run_id=:run AND attempt_id=:id FOR UPDATE
    """), {"run": claim.run_id, "id": attempt_id}).mappings().one_or_none()
    if row is None:
        raise RunConflict("RESOURCE_NOT_FOUND")
    return row


def _require_current_agent(binding: dict) -> None:
    current = require_agent(binding["agent_id"], binding["version"], binding["policy_version"])
    if json.loads(_agent_binding(current)) != binding:
        raise RunConflict("AGENT_POLICY_DENIED")


def _require_current_prices(
    connection: Connection, model_binding: dict, prices: TokenPrices,
) -> None:
    row = connection.execute(text("""
        SELECT version,currency,input_price,output_price,bounds_unconfirmed_cost
        FROM agent_research.model_prices WHERE model_binding=CAST(:model AS jsonb) FOR SHARE
    """), {"model": json.dumps(model_binding)}).one_or_none()
    if row is None or TokenPrices(*row) != prices:
        raise BudgetDenied("BUDGET_POLICY_UNAVAILABLE")


def _lock_policies(connection: Connection, scopes: tuple[str, ...]) -> list[RowMapping]:
    rows = list(connection.execute(text("""
        SELECT * FROM agent_research.model_budget_policies
        WHERE scope=ANY(CAST(:scopes AS text[])) ORDER BY scope FOR UPDATE
    """), {"scopes": list(scopes)}).mappings())
    if tuple(row["scope"] for row in rows) != scopes:
        raise BudgetDenied("BUDGET_POLICY_UNAVAILABLE")
    return rows


def _audit(connection: Connection, attempt_id: UUID) -> None:
    connection.execute(text("""
        INSERT INTO agent_research.model_attempt_events (attempt_id,status,exceeded_bound)
        SELECT attempt_id,status,exceeded_bound FROM agent_research.model_attempts
        WHERE attempt_id=:id
    """), {"id": attempt_id})


def _window(
    connection: Connection, limit: BudgetAmount, *, run_id: UUID | None = None,
    scope: str | None = None, period: date | None = None,
) -> BudgetWindow:
    carry = scope is not None and scope.startswith(("global:", "provider:", "model:"))
    if run_id is not None:
        condition = "run_id=:run"
    else:
        condition = "scopes @> ARRAY[CAST(:scope AS text)] AND (period=:period OR :carry)"
    totals = connection.execute(text(f"""
        SELECT coalesce(sum(confirmed_cost) FILTER (WHERE :all_periods OR period=:period
                    OR (:carry AND (created_at AT TIME ZONE 'UTC')::date=:period)),0)
            AS confirmed_cost,
          coalesce(sum(reported_input) FILTER (WHERE :all_periods OR period=:period
                    OR (:carry AND (created_at AT TIME ZONE 'UTC')::date=:period)),0) AS input,
          coalesce(sum(reported_output) FILTER (WHERE :all_periods OR period=:period
                    OR (:carry AND (created_at AT TIME ZONE 'UTC')::date=:period)),0) AS output,
          count(*) FILTER (WHERE :all_periods OR period=:period
                    OR (:carry AND (created_at AT TIME ZONE 'UTC')::date=:period)) AS calls,
          coalesce(sum(held_cost),0) AS held_cost, coalesce(sum(held_input),0) AS held_input,
          coalesce(sum(held_output),0) AS held_output,
          bool_or(currency<>:currency) AS currency_changed, bool_or(exceeded_bound) AS overrun
        FROM agent_research.model_attempts WHERE {condition}
    """), {
        "run": run_id, "scope": scope, "period": period, "carry": carry,
        "all_periods": run_id is not None,
        "currency": limit.currency,
    }).one()
    if totals.currency_changed:
        raise BudgetDenied("BUDGET_CURRENCY_MISMATCH")
    if totals.overrun:
        raise BudgetDenied("BUDGET_EXHAUSTED")
    return BudgetWindow(
        limit,
        BudgetAmount(limit.currency, totals.confirmed_cost, int(totals.input),
                     int(totals.output), totals.calls),
        BudgetAmount(limit.currency, totals.held_cost, int(totals.held_input),
                     int(totals.held_output)),
    )


def _windows(
    connection: Connection, run: RowMapping, policies: list[RowMapping], period: date,
) -> list[BudgetWindow]:
    limits = run["agent_binding"]["limits"]
    max_calls = limits["max_model_calls"]
    if run["kind"] == "SCHEDULED":
        max_calls = min(max_calls, 1)
    run_limit = BudgetAmount(
        limits["currency"], Decimal(limits["max_cost"]), limits["max_input_tokens"],
        limits["max_output_tokens"], max_calls,
    )
    windows = [_window(connection, run_limit, run_id=run["run_id"])]
    for policy in policies:
        limit = BudgetAmount(
            policy["currency"], policy["max_cost"], policy["max_input_tokens"],
            policy["max_output_tokens"], policy["max_calls"],
        )
        windows.append(_window(connection, limit, scope=policy["scope"], period=period))
    return windows


def _current_period_windows(connection, run, policies, original_period):
    if run["current_period"] == original_period:
        return []
    active = [
        policy for policy in policies
        if policy["scope"].startswith(("global:", "provider:", "model:"))
    ]
    return _windows(connection, run, active, run["current_period"])


class ModelAttemptStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    def recover(self, claim: Claim) -> tuple[Attempt, ...]:
        """Close stale attempts before the replacement worker takes another step."""
        with self.engine.begin() as connection:
            _lock_run(connection, claim)
            stale = connection.execute(text("""
                SELECT attempt_id,scopes FROM agent_research.model_attempts
                WHERE run_id=:run AND fence<:fence
                  AND status IN ('RESERVED','DISPATCH_AUTHORIZED')
                ORDER BY attempt_id FOR UPDATE
            """), {"run": claim.run_id, "fence": claim.fence}).all()
            scopes = sorted({scope for row in stale for scope in row.scopes})
            if scopes:
                connection.execute(text("""
                    SELECT scope FROM agent_research.model_budget_policies
                    WHERE scope=ANY(CAST(:scopes AS text[])) ORDER BY scope FOR UPDATE
                """), {"scopes": scopes}).all()
            _lock_run(connection, claim)
            recovered = []
            for row in stale:
                changed = connection.execute(text("""
                    UPDATE agent_research.model_attempts SET
                        status=CASE WHEN status='RESERVED' THEN 'UNSENT' ELSE 'OUTCOME_UNKNOWN' END,
                        held_cost=CASE WHEN status='RESERVED' THEN 0 ELSE held_cost END,
                        held_input=CASE WHEN status='RESERVED' THEN 0 ELSE held_input END,
                        held_output=CASE WHEN status='RESERVED' THEN 0 ELSE held_output END,
                        confirmed_cost=CASE WHEN status='RESERVED' THEN 0 ELSE confirmed_cost END
                    WHERE attempt_id=:id RETURNING *
                """), {"id": row.attempt_id}).mappings().one()
                _audit(connection, row.attempt_id)
                if changed["status"] == "UNSENT":
                    recovered.append(_attempt(changed))
            durable = connection.execute(text("""
                SELECT * FROM agent_research.model_attempts
                WHERE run_id=:run AND fence<:fence AND status IN ('OUTCOME_UNKNOWN','COMPLETED')
                ORDER BY attempt_id
            """), {"run": claim.run_id, "fence": claim.fence}).mappings()
            recovered.extend(_attempt(row) for row in durable)
            _lock_run(connection, claim)
            return tuple(recovered)

    def reserve(
        self, claim: Claim, *, attempt_id: UUID, step_id: str, request_hash: str,
        model: ModelBinding, prices: TokenPrices, tokens: Tokens, context_window: int,
        timeout_seconds: int,
    ) -> Attempt:
        if (not _REFERENCE.fullmatch(step_id) or not _HASH.fullmatch(request_hash)
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600):
            raise RunConflict("INVALID_REQUEST")
        reservation = reserve_model(prices, tokens, context_window)
        bound = reservation.bound
        with self.engine.begin() as connection:
            run = _lock_run(connection, claim)
            agent = run["agent_binding"]
            _require_current_agent(agent)
            if agent["model_binding"] != asdict(model):
                raise RunConflict("AGENT_POLICY_DENIED")
            existing = connection.execute(text("""
                SELECT * FROM agent_research.model_attempts WHERE attempt_id=:id
            """), {"id": attempt_id}).mappings().one_or_none()
            if existing is not None:
                if (
                    existing["run_id"], existing["step_id"], existing["request_hash"],
                    existing["pricing_version"], existing["input_bound"], existing["output_bound"],
                    existing["input_price"], existing["output_price"], existing["currency"],
                    existing["price_bounds_cost"],
                ) != (
                    claim.run_id, step_id, request_hash, prices.version,
                    tokens.input, tokens.output,
                    prices.input_per_million, prices.output_per_million, prices.currency,
                    prices.bounds_unconfirmed_cost,
                ):
                    raise RunConflict("IDEMPOTENCY_CONFLICT")
                return _attempt(existing)
            if connection.execute(text("""
                SELECT EXISTS (SELECT FROM agent_research.model_attempts
                    WHERE run_id=:run AND step_id=:step AND status<>'UNSENT')
            """), {"run": claim.run_id, "step": step_id}).scalar_one():
                raise RunConflict("PROVIDER_OUTCOME_UNKNOWN")
            scopes = model_budget_scopes(
                agent["agent_id"], agent["version"], run["kind"], model, run["input_ref"],
            )
            policies = _lock_policies(connection, scopes)
            if any(not policy["enabled"] for policy in policies):
                raise BudgetDenied("BUDGET_POLICY_UNAVAILABLE")
            _require_current_prices(connection, agent["model_binding"], prices)
            require_capacity(_windows(connection, run, policies, run["budget_period"]), bound)
            current = _current_period_windows(connection, run, policies, run["budget_period"])
            if current:
                require_capacity(current, bound)
            row = connection.execute(text("""
                INSERT INTO agent_research.model_attempts (
                    attempt_id,run_id,step_id,request_hash,fence,run_version,deadline_at,period,
                    scopes,policy_versions,pricing_version,currency,input_price,output_price,
                    price_bounds_cost,input_bound,output_bound,cost_bound,held_cost,held_input,
                    held_output,status
                )
                SELECT :id,r.run_id,:step,:hash,r.fence,r.version,
                    least(r.deadline_at,clock_timestamp()+:timeout*interval '1 second'),:period,
                    CAST(:scopes AS text[]),CAST(:versions AS bigint[]),:pricing,:currency,:in_rate,
                    :out_rate,:bounds_cost,:input,:output,:cost,:cost,:input,:output,'RESERVED'
                FROM agent_research.runs r
                WHERE r.run_id=:run AND r.lease_until>clock_timestamp()
                  AND r.deadline_at>clock_timestamp()
                ON CONFLICT (attempt_id) DO NOTHING RETURNING *
            """), {
                "id": attempt_id, "run": claim.run_id, "step": step_id, "hash": request_hash,
                "timeout": timeout_seconds, "period": run["budget_period"], "scopes": list(scopes),
                "versions": [policy["version"] for policy in policies], "pricing": prices.version,
                "currency": prices.currency, "in_rate": prices.input_per_million,
                "out_rate": prices.output_per_million,
                "bounds_cost": prices.bounds_unconfirmed_cost,
                "input": tokens.input, "output": tokens.output, "cost": bound.cost,
            }).mappings().one_or_none()
            if row is None:
                _lock_run(connection, claim)
                raise RunConflict("IDEMPOTENCY_CONFLICT")
            _audit(connection, attempt_id)
            return _attempt(row)

    @staticmethod
    def authorize_in_transaction(
        connection: Connection, claim: Claim, attempt_id: UUID, request_hash: str,
        *, dialogue_authorized: bool = False,
    ) -> Attempt:
        """Consume dispatch once, optionally within the bound dialogue consent transaction."""
        run = _lock_run(connection, claim)
        _require_current_agent(run["agent_binding"])
        if connection.execute(text("""
            SELECT EXISTS (SELECT FROM agent_research.model_attempts
                WHERE run_id=:run AND attempt_id<>:id
                  AND status IN ('DISPATCH_AUTHORIZED','OUTCOME_UNKNOWN'))
        """), {"run": claim.run_id, "id": attempt_id}).scalar_one():
            raise RunConflict("PROVIDER_OUTCOME_UNKNOWN")
        if run["kind"] == "DIALOGUE" and not dialogue_authorized:
            raise RunConflict("PROCESSING_GRANT_REQUIRED")
        row = _lock_attempt(connection, claim, attempt_id)
        if row["status"] != "RESERVED" or row["request_hash"] != request_hash:
            raise RunConflict("AGENT_POLICY_DENIED")
        policies = _lock_policies(connection, tuple(row["scopes"]))
        if (
            any(not policy["enabled"] for policy in policies)
            or [policy["version"] for policy in policies] != row["policy_versions"]
        ):
            raise BudgetDenied("BUDGET_POLICY_UNAVAILABLE")
        _require_current_prices(connection, run["agent_binding"]["model_binding"], TokenPrices(
            row["pricing_version"], row["currency"], row["input_price"], row["output_price"],
            row["price_bounds_cost"],
        ))
        no_increment = BudgetAmount(row["currency"])
        require_capacity(_windows(connection, run, policies, row["period"]), no_increment)
        current = _current_period_windows(connection, run, policies, row["period"])
        if current:
            require_capacity(current, no_increment)
        _lock_run(connection, claim)
        updated = connection.execute(text("""
            UPDATE agent_research.model_attempts SET status='DISPATCH_AUTHORIZED'
            WHERE attempt_id=:id AND fence=:fence AND run_version=:version
              AND deadline_at>clock_timestamp() RETURNING *
        """), {"id": attempt_id, "fence": claim.fence, "version": claim.version}
        ).mappings().one_or_none()
        if updated is None:
            raise RunConflict("LEASE_LOST")
        _audit(connection, attempt_id)
        return _attempt(updated)

    def reconcile_confirmed_cost(
        self, attempt_id: UUID, evidence_ref: str, confirmed_cost: Decimal,
    ) -> None:
        """Book a verified provider charge without granting run or publication authority.

        The private caller resolves the immutable billing evidence and its
        attempt identity before entry. No provider payload enters this store.
        """
        if not _REFERENCE.fullmatch(evidence_ref):
            raise RunConflict("INVALID_REQUEST")
        with self.engine.begin() as connection:
            if connection.get_isolation_level() != "READ COMMITTED":
                raise RunConflict("AGENT_POLICY_DENIED")
            row = connection.execute(text("""
                SELECT a.run_id,r.fence,r.state
                FROM agent_research.model_attempts a JOIN agent_research.runs r USING (run_id)
                WHERE a.attempt_id=:id FOR UPDATE OF r
            """), {"id": attempt_id}).one_or_none()
            if row is None:
                raise RunConflict("RESOURCE_NOT_FOUND")
            attempt = connection.execute(text("""
                SELECT *,deadline_at<=clock_timestamp() AS expired
                FROM agent_research.model_attempts WHERE attempt_id=:id FOR UPDATE
            """), {"id": attempt_id}).mappings().one()
            _lock_policies(connection, tuple(attempt["scopes"]))
            value = BudgetAmount(attempt["currency"], confirmed_cost).cost
            previous = connection.execute(text("""
                SELECT evidence_ref,confirmed_cost FROM agent_research.model_accounting_receipts
                WHERE attempt_id=:id
            """), {"id": attempt_id}).one_or_none()
            if previous is not None:
                if tuple(previous) != (evidence_ref, value):
                    raise RunConflict("IDEMPOTENCY_CONFLICT")
                return
            if attempt["status"] not in {
                "COMPLETED", "OUTCOME_UNKNOWN", "RESULT_UNAVAILABLE", "DISPATCH_AUTHORIZED",
            }:
                raise RunConflict("AGENT_POLICY_DENIED")
            if (
                attempt["status"] == "DISPATCH_AUTHORIZED" and row.state == "RUNNING"
                and row.fence == attempt["fence"] and not attempt["expired"]
            ):
                raise RunConflict("AGENT_POLICY_DENIED")
            if attempt["confirmed_cost"] is not None and attempt["confirmed_cost"] != value:
                raise RunConflict("IDEMPOTENCY_CONFLICT")
            status = attempt["status"]
            if status == "DISPATCH_AUTHORIZED":
                status = "OUTCOME_UNKNOWN"
            params = {
                "id": attempt_id, "evidence": evidence_ref, "cost": value, "status": status,
                "overrun": attempt["exceeded_bound"] or value > attempt["cost_bound"],
            }
            connection.execute(text("""
                UPDATE agent_research.model_attempts SET confirmed_cost=:cost,held_cost=0,
                    status=:status,exceeded_bound=:overrun
                WHERE attempt_id=:id
            """), params)
            connection.execute(text("""
                INSERT INTO agent_research.model_accounting_receipts
                  (attempt_id,evidence_ref,confirmed_cost)
                VALUES (:id,:evidence,:cost)
            """), params)
            if status != attempt["status"]:
                _audit(connection, attempt_id)

    def authorize(self, claim: Claim, attempt_id: UUID, request_hash: str) -> Attempt:
        with self.engine.begin() as connection:
            return self.authorize_in_transaction(connection, claim, attempt_id, request_hash)

    def settle(
        self, claim: Claim, attempt_id: UUID, outcome: ModelOutcome, *,
        result_ref: str | None = None, reported_tokens: Tokens | None = None,
        confirmed_cost: Decimal | None = None, unsent_evidence_ref: str | None = None,
    ) -> Attempt:
        with self.engine.begin() as connection:
            return self.settle_in_transaction(
                connection, claim, attempt_id, outcome, result_ref=result_ref,
                reported_tokens=reported_tokens, confirmed_cost=confirmed_cost,
                unsent_evidence_ref=unsent_evidence_ref,
            )

    @staticmethod
    def settle_in_transaction(
        connection: Connection, claim: Claim, attempt_id: UUID, outcome: ModelOutcome, *,
        result_ref: str | None = None, reported_tokens: Tokens | None = None,
        confirmed_cost: Decimal | None = None, unsent_evidence_ref: str | None = None,
        dialogue_authorized: bool = False,
    ) -> Attempt:
        """Accept a result only while the attempt's original fence remains valid."""
        if (
            not isinstance(outcome, ModelOutcome)
            or (outcome is ModelOutcome.COMPLETED) != (result_ref is not None)
            or (result_ref is not None and not _REFERENCE.fullmatch(result_ref))
            or (unsent_evidence_ref is not None and (
                outcome is not ModelOutcome.UNSENT or not _REFERENCE.fullmatch(unsent_evidence_ref)
            ))
        ):
            raise RunConflict("INVALID_REQUEST")
        run = _lock_run(connection, claim)
        if outcome is ModelOutcome.COMPLETED:
            _require_current_agent(run["agent_binding"])
        if (
            run["kind"] == "DIALOGUE" and outcome is ModelOutcome.COMPLETED
            and not dialogue_authorized
        ):
            raise RunConflict("PROCESSING_GRANT_REQUIRED")
        row = _lock_attempt(connection, claim, attempt_id)
        _lock_policies(connection, tuple(row["scopes"]))
        _lock_run(connection, claim)
        if outcome is ModelOutcome.COMPLETED and (
            row["fence"], row["run_version"]
        ) != (claim.fence, claim.version):
            raise RunConflict("LEASE_LOST")
        if row["status"] == outcome.value:
            if (
                row["result_ref"], row["reported_input"], row["reported_output"],
                row["confirmed_cost"], row["unsent_evidence_ref"],
            ) != (
                result_ref, reported_tokens.input if reported_tokens else None,
                reported_tokens.output if reported_tokens else None,
                confirmed_cost if outcome is not ModelOutcome.UNSENT else 0, unsent_evidence_ref,
            ):
                raise RunConflict("IDEMPOTENCY_CONFLICT")
            return _attempt(row)
        if row["status"] not in {"RESERVED", "DISPATCH_AUTHORIZED"}:
            raise RunConflict("AGENT_POLICY_DENIED")
        if row["status"] == "RESERVED" and outcome is not ModelOutcome.UNSENT:
            raise RunConflict("AGENT_POLICY_DENIED")
        if row["status"] == "DISPATCH_AUTHORIZED" and (
            outcome is ModelOutcome.UNSENT and unsent_evidence_ref is None
        ):
            raise RunConflict("AGENT_POLICY_DENIED")
        prices = TokenPrices(
            row["pricing_version"], row["currency"], row["input_price"], row["output_price"],
            row["price_bounds_cost"],
        )
        reservation = ModelReservation(prices, BudgetAmount(
            prices.currency, row["cost_bound"], row["input_bound"], row["output_bound"], 1,
        ))
        settlement = reconcile_model(reservation, outcome, reported_tokens, confirmed_cost)
        updated = connection.execute(text("""
            UPDATE agent_research.model_attempts SET status=:status,result_ref=:result,
                unsent_evidence_ref=:unsent_evidence_ref,
                held_cost=:held_cost,held_input=:held_input,held_output=:held_output,
                reported_input=:reported_input,reported_output=:reported_output,
                estimated_cost=:estimate,confirmed_cost=:confirmed,exceeded_bound=:overrun
            WHERE attempt_id=:id
              AND (CAST(:result AS text) IS NULL OR deadline_at>clock_timestamp())
            RETURNING *
        """), {
            "status": outcome.value, "result": result_ref, "id": attempt_id,
            "unsent_evidence_ref": unsent_evidence_ref,
            "held_cost": settlement.reserved.cost,
            "held_input": settlement.reserved.input_tokens,
            "held_output": settlement.reserved.output_tokens,
            "estimate": settlement.estimated_cost,
            "reported_input": reported_tokens.input if reported_tokens else None,
            "reported_output": reported_tokens.output if reported_tokens else None,
            "confirmed": settlement.confirmed_cost, "overrun": settlement.exceeded_bound,
        }).mappings().one_or_none()
        if updated is None:
            raise RunConflict("LEASE_LOST")
        _audit(connection, attempt_id)
        return _attempt(updated)
