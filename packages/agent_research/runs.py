"""Transactional PostgreSQL run queue, lease fencing and redacted audit."""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .budget import BudgetDenied
from .registry import AgentPolicyDenied, AgentVersion, require_agent
from .run_state import RunState, require_transition
from .work_budget import charge_work

_CLAIM_LOCK = 878702731
_LEASE_SECONDS = 30
_REFERENCE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_REQUEST_KEY = re.compile(r"[A-Za-z0-9._:-]{16,128}")
_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_ACTIVE_MS = """least(active_limit_ms, active_ms + coalesce(greatest(0, ceil(extract(epoch FROM
    (least(clock_timestamp(),lease_until)-claimed_at))*1000)::integer),0))"""


class RunKind(StrEnum):
    DIALOGUE = "DIALOGUE"
    SCHEDULED = "SCHEDULED"
    REEVALUATION = "REEVALUATION"
    PROVIDER_PROBE = "PROVIDER_PROBE"


class RunConflict(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Claim:
    run_id: UUID
    owner: UUID
    fence: int
    version: int


@dataclass(frozen=True, slots=True)
class ClaimedInput:
    kind: RunKind
    input_ref: str
    checkpoint_ref: str | None
    dispatch_deadline: datetime | None = None


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run_id: UUID
    kind: RunKind
    state: RunState
    version: int
    trace_id: str
    agent_id: str
    agent_version: int
    policy_version: int
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime
    steps: int
    tool_calls: int
    active_ms: int


def _audit(connection: Connection, run_id: UUID) -> None:
    connection.execute(text("""
        INSERT INTO agent_research.run_events (run_id, version, fence, state, occurred_at)
        SELECT run_id, version, fence, state, updated_at
        FROM agent_research.runs WHERE run_id=:id
    """), {"id": run_id})


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _agent_binding(agent: AgentVersion) -> str:
    binding = asdict(agent)
    binding["limits"]["max_cost"] = str(agent.limits.max_cost)
    return json.dumps(binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class RunStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    def snapshot(self, run_id: UUID, *, actor: str) -> RunSnapshot | None:
        """Read the run summary without exposing worker claims or request material."""
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT run_id,kind,state,version,trace_id,agent_binding,
                    created_at,updated_at,deadline_at,steps,tool_calls,active_ms
                FROM agent_research.runs WHERE run_id=:id AND actor=:actor
            """), {"id": run_id, "actor": actor}).one_or_none()
        if row is None:
            return None
        binding = row.agent_binding
        return RunSnapshot(
            row.run_id, RunKind(row.kind), RunState(row.state), row.version, row.trace_id,
            binding["agent_id"], binding["version"], binding["policy_version"],
            row.created_at, row.updated_at, row.deadline_at,
            row.steps, row.tool_calls, row.active_ms,
        )

    def enqueue(
        self, *, actor: str, key: str, kind: RunKind, input_ref: str, trace_id: str,
        agent: AgentVersion,
    ) -> UUID:
        with self.engine.begin() as connection:
            return self.enqueue_in_transaction(
                connection, actor=actor, key=key, kind=kind, input_ref=input_ref,
                trace_id=trace_id, agent=agent,
            )

    @staticmethod
    def enqueue_in_transaction(
        connection: Connection, *, actor: str, key: str, kind: RunKind, input_ref: str,
        trace_id: str, agent: AgentVersion,
    ) -> UUID:
        if not all((
            _REFERENCE.fullmatch(actor), _REQUEST_KEY.fullmatch(key),
            _REFERENCE.fullmatch(input_ref), _TRACE_ID.fullmatch(trace_id),
        )):
            raise ValueError("invalid research run binding")
        kind = RunKind(kind)
        if require_agent(agent.agent_id, agent.version, agent.policy_version) != agent:
            raise AgentPolicyDenied(AgentPolicyDenied.code)
        binding = _agent_binding(agent)
        params = {
            "id": uuid4(), "actor": actor, "key": _digest(key), "kind": kind.value,
            "hash": _digest(json.dumps([kind.value, input_ref, binding], separators=(",", ":"))),
            "ref": input_ref, "trace": trace_id, "agent": binding,
            "deadline": agent.limits.max_wall_seconds, "steps": agent.limits.max_steps,
            "tools": agent.limits.max_tool_calls,
            "active_ms": agent.limits.max_active_seconds * 1000,
        }
        inserted = connection.execute(text("""
                INSERT INTO agent_research.runs (
                    run_id, actor, request_key_hash, request_hash, kind, input_ref, trace_id,
                    agent_binding, state, created_at, updated_at, deadline_at, step_limit,
                    tool_limit, active_limit_ms
                ) VALUES (
                    :id, :actor, :key, :hash, :kind, :ref, :trace, CAST(:agent AS jsonb),
                    'QUEUED', statement_timestamp(), statement_timestamp(),
                    statement_timestamp() + :deadline * interval '1 second', :steps, :tools,
                    :active_ms
                ) ON CONFLICT (actor,kind,request_key_hash) DO NOTHING RETURNING run_id
        """), params).scalar_one_or_none()
        if inserted is not None:
            _audit(connection, inserted)
            return inserted
        original = connection.execute(text("""
                SELECT run_id,request_hash FROM agent_research.runs
                WHERE actor=:actor AND kind=:kind AND request_key_hash=:key
        """), params).one()
        if original.request_hash != params["hash"]:
            raise RunConflict("IDEMPOTENCY_CONFLICT")
        return original.run_id

    def claim(self, owner: UUID, *, discovery: bool = False) -> Claim | None:
        scheduled_filter = """
          AND (kind<>'SCHEDULED' OR EXISTS(
            SELECT FROM agent_research.discovery_occurrences o
            JOIN agent_research.discovery_schedules s USING(schedule_id)
            WHERE o.run_id=agent_research.runs.run_id AND s.state='ACTIVE'
              AND s.epoch=o.schedule_epoch))
        """ if discovery else "AND kind<>'SCHEDULED'"
        with self.engine.begin() as connection:
            if not connection.execute(
                text("SELECT pg_try_advisory_xact_lock(:lock)"), {"lock": _CLAIM_LOCK}
            ).scalar_one():
                return None
            self._reap(connection)
            row = connection.execute(text(f"""
                WITH ready AS (
                    SELECT run_id, {_ACTIVE_MS} AS elapsed FROM agent_research.runs
                    WHERE deadline_at>clock_timestamp() AND steps<step_limit
                      AND {_ACTIVE_MS}<active_limit_ms
                      AND (state='QUEUED' OR (state='RUNNING' AND lease_until<=clock_timestamp()))
                      {scheduled_filter}
                      AND NOT EXISTS (
                          SELECT FROM agent_research.runs active
                          WHERE active.state='RUNNING' AND active.lease_until>clock_timestamp()
                      )
                    ORDER BY created_at,run_id FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE agent_research.runs r SET state='RUNNING', owner=:owner,
                    fence=r.fence+1, version=r.version+1, steps=r.steps+1,
                    active_ms=ready.elapsed, claimed_at=statement_timestamp(),
                    lease_until=least(r.deadline_at,
                        statement_timestamp()+:lease*interval '1 second',
                        statement_timestamp()+(r.active_limit_ms-ready.elapsed)*interval '1 ms'),
                    updated_at=clock_timestamp()
                FROM ready WHERE r.run_id=ready.run_id
                RETURNING r.run_id,r.fence,r.version
            """), {"owner": owner, "lease": _LEASE_SECONDS}).one_or_none()
            if row is None:
                return None
            try:
                charge_work(connection, row.run_id, owner, row.fence, row.version, "steps")
            except BudgetDenied as error:
                terminal = (
                    RunState.BUDGET_EXHAUSTED
                    if error.code == "BUDGET_EXHAUSTED" else RunState.BLOCKED
                )
                connection.execute(text("""
                    UPDATE agent_research.runs SET state=:state,steps=steps-1,
                        owner=NULL,claimed_at=NULL,lease_until=NULL,updated_at=clock_timestamp()
                    WHERE run_id=:id
                """), {"id": row.run_id, "state": terminal.value})
                _audit(connection, row.run_id)
                return None
            _audit(connection, row.run_id)
            return Claim(row.run_id, owner, row.fence, row.version)

    def load_input(self, claim: Claim) -> ClaimedInput:
        params = {
            "id": claim.run_id, "owner": claim.owner, "fence": claim.fence,
            "version": claim.version,
        }
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT kind,input_ref,checkpoint_ref,agent_binding,
                    least(deadline_at,lease_until) AS dispatch_deadline FROM agent_research.runs
                WHERE run_id=:id AND owner=:owner AND fence=:fence AND version=:version
                  AND state='RUNNING' AND lease_until>clock_timestamp()
            """), params).one_or_none()
        if row is None:
            raise RunConflict("LEASE_LOST")
        binding = row.agent_binding
        agent = require_agent(binding["agent_id"], binding["version"], binding["policy_version"])
        if json.loads(_agent_binding(agent)) != binding:
            raise AgentPolicyDenied(AgentPolicyDenied.code)
        return ClaimedInput(
            RunKind(row.kind), row.input_ref, row.checkpoint_ref, row.dispatch_deadline,
        )

    @staticmethod
    def _reap(connection: Connection) -> None:
        rows = connection.execute(text(f"""
            WITH expired AS (
                SELECT run_id FROM agent_research.runs
                WHERE state IN ('QUEUED','RUNNING','WAITING_FOR_INPUT',
                    'WAITING_FOR_PROCESSING_DECISION','WAITING_FOR_APPROVAL','WAITING_FOR_BATCH')
                  AND (deadline_at<=clock_timestamp() OR
                       ((steps=step_limit OR {_ACTIVE_MS}>=active_limit_ms) AND (state='QUEUED' OR
                           (state='RUNNING' AND lease_until<=clock_timestamp()))))
                ORDER BY deadline_at,run_id FOR UPDATE SKIP LOCKED LIMIT 100
            )
            UPDATE agent_research.runs r SET
                state=CASE WHEN r.deadline_at<=clock_timestamp() THEN 'TIMED_OUT'
                           ELSE 'BUDGET_EXHAUSTED' END,
                owner=NULL, claimed_at=NULL, lease_until=NULL,
                active_ms={_ACTIVE_MS}, version=r.version+1, fence=r.fence+1,
                updated_at=clock_timestamp()
            FROM expired WHERE r.run_id=expired.run_id RETURNING r.run_id
        """))
        for run_id in rows.scalars():
            _audit(connection, run_id)

    def transition(
        self, claim: Claim, target: RunState, *, checkpoint_ref: str | None = None,
    ) -> None:
        target = RunState(target)
        require_transition(RunState.RUNNING, target)
        if checkpoint_ref is not None and not _REFERENCE.fullmatch(checkpoint_ref):
            raise ValueError("invalid checkpoint reference")
        params = {
            "id": claim.run_id, "owner": claim.owner, "fence": claim.fence,
            "version": claim.version, "target": target.value, "checkpoint": checkpoint_ref,
        }
        with self.engine.begin() as connection:
            changed = connection.execute(text(f"""
                UPDATE agent_research.runs SET state=:target, version=version+1,
                    owner=NULL, claimed_at=NULL, lease_until=NULL, active_ms={_ACTIVE_MS},
                    updated_at=clock_timestamp(),
                    checkpoint_ref=coalesce(:checkpoint,checkpoint_ref)
                WHERE run_id=:id AND owner=:owner AND fence=:fence AND version=:version
                  AND state='RUNNING' AND lease_until>clock_timestamp()
                  AND deadline_at>clock_timestamp()
                RETURNING run_id
            """), params).scalar_one_or_none()
            if changed is None:
                raise RunConflict("LEASE_LOST")
            _audit(connection, changed)

    def renew(self, claim: Claim) -> None:
        """Extend a live lease within both the run deadline and active time cap."""
        params = {
            "id": claim.run_id, "owner": claim.owner, "fence": claim.fence,
            "version": claim.version, "lease": _LEASE_SECONDS,
        }
        with self.engine.begin() as connection:
            renewed = connection.execute(text("""
                UPDATE agent_research.runs SET lease_until=least(deadline_at,
                    clock_timestamp()+:lease*interval '1 second',
                    claimed_at+(active_limit_ms-active_ms)*interval '1 ms')
                WHERE run_id=:id AND owner=:owner AND fence=:fence AND version=:version
                  AND state='RUNNING' AND lease_until>clock_timestamp()
                RETURNING run_id
            """), params).scalar_one_or_none()
            if renewed is None:
                raise RunConflict("LEASE_LOST")

    def resume_input(self, run_id: UUID, expected_version: int) -> None:
        """Requeue after a separately authenticated, already bound local input."""
        with self.engine.begin() as connection:
            changed = connection.execute(text("""
                UPDATE agent_research.runs SET state='QUEUED', version=version+1,
                    updated_at=clock_timestamp()
                WHERE run_id=:id AND version=:version AND state='WAITING_FOR_INPUT'
                  AND deadline_at>clock_timestamp()
                RETURNING run_id
            """), {"id": run_id, "version": expected_version}).scalar_one_or_none()
            if changed is None:
                raise RunConflict("VERSION_CONFLICT")
            _audit(connection, changed)

    def cancel(self, run_id: UUID, expected_version: int) -> None:
        with self.engine.begin() as connection:
            params = {"id": run_id, "version": expected_version}
            state = connection.execute(text("""
                SELECT state FROM agent_research.runs
                WHERE run_id=:id AND version=:version FOR UPDATE
            """), params).scalar_one_or_none()
            if state is None:
                raise RunConflict("VERSION_CONFLICT")
            require_transition(RunState(state), RunState.CANCELLED)
            connection.execute(text(f"""
                UPDATE agent_research.runs SET state='CANCELLED', version=version+1,
                    fence=fence+1, owner=NULL, claimed_at=NULL, lease_until=NULL,
                    active_ms={_ACTIVE_MS}, updated_at=clock_timestamp()
                WHERE run_id=:id
            """), params)
            _audit(connection, run_id)
