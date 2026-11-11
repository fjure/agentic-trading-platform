"""Durable scan, dispatch and publication fences for proactive research."""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.engine import Engine

from packages.agent_research.budget import ModelOutcome, Tokens
from packages.agent_research.model_attempts import ModelAttemptStore, _lock_run
from packages.agent_research.runs import Claim, RunConflict
from packages.persistence.transaction import ReadOnlyScopedConnection, ScopedConnection
from packages.research.models import content_fingerprint

from .evidence import (
    EvaluationBinding,
    EvidenceAccess,
    EvidenceGroup,
    EvidenceKind,
    EvidencePosition,
    EvidenceRevision,
    TriggerPolicy,
    evaluate_evidence,
    evaluation_fingerprint,
)


@dataclass(frozen=True, slots=True)
class ScanResult:
    scanned: int
    more: bool
    backpressure: bool


def _evidence(row) -> EvidenceRevision:
    return EvidenceRevision(
        EvidencePosition(row["position"], EvidenceKind(row["kind"]), str(row["source_id"])),
        row["revision"], str(row["instrument_id"]), row["observed_at"],
        row["window_start"], row["window_end"], row["fingerprint"], row["material_change"],
    )


def _event(connection, claim: Claim, position: int, reason: str) -> None:
    connection.execute(text("""
        INSERT INTO agent_research.discovery_events(run_id,source_position,reason)
        VALUES(:run,:position,:reason) ON CONFLICT DO NOTHING
    """), {"run": claim.run_id, "position": position, "reason": reason})


def _allowed(connection, position: int) -> bool:
    return connection.scalar(
        text("SELECT agent_research.discovery_source_allowed(:position)"), {"position": position},
    ) is True


def _policy_hash(policy: TriggerPolicy) -> str:
    return content_fingerprint((
        policy.rule_version, policy.allowlist_version, sorted(policy.instruments),
        sorted(kind.value for kind in policy.kinds), policy.max_age // timedelta(microseconds=1),
    ))


class DiscoveryStore:
    def __init__(
        self, engine: Engine, *,
        require_active: Callable[[object, str, int], None],
    ):
        self.engine = engine
        self._require_active = require_active

    def _lock_scan(self, connection, claim: Claim):
        run = _lock_run(connection, claim)
        scan = connection.execute(text("""
            SELECT * FROM agent_research.discovery_scans WHERE run_id=:run
        """), {"run": claim.run_id}).mappings().one_or_none()
        if scan is None or run["kind"] != "SCHEDULED":
            raise RunConflict("AGENT_POLICY_DENIED")
        self._require_active(connection, scan["schedule_id"], scan["schedule_epoch"])
        return scan

    def open_scan(
        self, claim: Claim, *, schedule_id: str, epoch: int, policy: TriggerPolicy,
    ) -> int:
        binding = _policy_hash(policy)
        with self.engine.begin() as connection:
            run = _lock_run(connection, claim)
            if run["kind"] != "SCHEDULED" or run["input_ref"] != schedule_id:
                raise RunConflict("AGENT_POLICY_DENIED")
            self._require_active(connection, schedule_id, epoch)
            occurrence = connection.scalar(text("""
                SELECT EXISTS(SELECT FROM agent_research.discovery_occurrences
                  WHERE run_id=:run AND schedule_id=:schedule AND schedule_epoch=:epoch)
            """), {"run": claim.run_id, "schedule": schedule_id, "epoch": epoch})
            if not occurrence:
                raise RunConflict("AGENT_POLICY_DENIED")
            connection.execute(text("""
                INSERT INTO agent_research.discovery_scans
                  (run_id,schedule_id,schedule_epoch,upper_position,rule_binding)
                SELECT :run,:schedule,:epoch,position,:rule
                FROM agent_research.discovery_source_clock WHERE singleton
                ON CONFLICT (run_id) DO NOTHING
            """), {"run": claim.run_id, "schedule": schedule_id, "epoch": epoch, "rule": binding})
            scan = self._lock_scan(connection, claim)
            if (scan["schedule_id"], scan["schedule_epoch"], scan["rule_binding"]) != (
                schedule_id, epoch, binding,
            ):
                raise RunConflict("AGENT_POLICY_DENIED")
            return scan["upper_position"]

    def scan(
        self, claim: Claim, policy: TriggerPolicy, evaluation: EvaluationBinding, *,
        access: Callable[[ReadOnlyScopedConnection, EvidenceRevision], EvidenceAccess],
        limit: int, pending_limit: int, ttl: timedelta,
    ) -> ScanResult:
        if not 1 <= limit <= 100 or not 1 <= pending_limit <= 10000 or not (
            timedelta() < ttl <= timedelta(days=30)
        ) or evaluation.reanalysis_id is not None:
            raise ValueError("invalid discovery scan budget")
        with self.engine.begin() as connection:
            scan = self._lock_scan(connection, claim)
            if scan["rule_binding"] != _policy_hash(policy):
                raise RunConflict("AGENT_POLICY_DENIED")
            schedule = scan["schedule_id"]
            cursor = self._cursor(connection, schedule)
            now = connection.scalar(text("SELECT clock_timestamp()"))
            stranded = connection.execute(text("""
                UPDATE agent_research.discovery_work w
                SET state='OUTCOME_UNKNOWN',reason='OUTCOME_UNKNOWN'
                FROM agent_research.runs r
                WHERE r.run_id=w.run_id AND w.schedule_id=:schedule
                  AND w.state='DISPATCHING'
                  AND (r.state IN ('TIMED_OUT','CANCELLED','BLOCKED','FAILED',
                    'COMPLETED','BUDGET_EXHAUSTED') OR r.deadline_at<=:now)
                RETURNING w.source_position
            """), {"schedule": schedule, "now": now}).scalars().all()
            for position in stranded:
                _event(connection, claim, position, "OUTCOME_UNKNOWN")
            connection.execute(text("""
                UPDATE agent_research.discovery_work SET state='BLOCKED',reason='PENDING_EXPIRED'
                WHERE schedule_id=:schedule AND state='PENDING' AND expires_at<=:now
            """), {"schedule": schedule, "now": now})
            pending = connection.scalar(text("""
                SELECT count(*) FROM agent_research.discovery_work
                WHERE schedule_id=:schedule AND state='PENDING'
            """), {"schedule": schedule})
            rows = connection.execute(text("""
                SELECT * FROM agent_research.discovery_sources
                WHERE position>:position AND position<=:upper
                ORDER BY position LIMIT :limit
            """), {"position": cursor, "upper": scan["upper_position"], "limit": limit + 1}
            ).mappings().all()
            backpressure = False
            count = 0
            for row in rows[:limit]:
                item = _evidence(row)
                if _allowed(connection, row["position"]):
                    verdict = evaluate_evidence(
                        item, access(ReadOnlyScopedConnection(connection), item), policy, now=now,
                    )
                    reason = verdict.reason.value
                    if verdict.eligible:
                        group = EvidenceGroup((item,))
                        exists = connection.scalar(text("""
                            SELECT EXISTS(SELECT FROM agent_research.discovery_work
                              WHERE evidence_hash=:hash AND reanalysis_key='')
                        """), {"hash": group.fingerprint})
                        if exists:
                            reason = "UNCHANGED_EVIDENCE"
                        elif pending >= pending_limit:
                            _event(connection, claim, row["position"], "PENDING_BACKPRESSURE")
                            backpressure = True
                            break
                        elif self._enqueue(connection, schedule, row, group, evaluation, now + ttl):
                            pending += 1
                else:
                    reason = "SOURCE_NOT_ALLOWED"
                _event(connection, claim, row["position"], reason)
                count += 1
                cursor = row["position"]
            connection.execute(text("""
                UPDATE agent_research.discovery_cursors SET position=:position
                WHERE schedule_id=:schedule
            """), {"position": cursor, "schedule": schedule})
            _lock_run(connection, claim)
            return ScanResult(count, backpressure or len(rows) > limit, backpressure)

    @staticmethod
    def _cursor(connection, schedule: str) -> int:
        connection.execute(text("""
            INSERT INTO agent_research.discovery_cursors(schedule_id) VALUES(:schedule)
            ON CONFLICT DO NOTHING
        """), {"schedule": schedule})
        return connection.scalar(text("""
            SELECT position FROM agent_research.discovery_cursors WHERE schedule_id=:schedule
            FOR UPDATE
        """), {"schedule": schedule})

    @staticmethod
    def _enqueue(connection, schedule, row, group, evaluation, expires) -> bool:
        digest = evaluation_fingerprint(group, evaluation)
        return connection.execute(text("""
            INSERT INTO agent_research.discovery_work
              (evaluation_id,schedule_id,source_position,evidence_hash,evaluation_hash,
               model_binding_hash,evaluation_binding,reanalysis_key,state,reason,expires_at)
            VALUES(:id,:schedule,:source,:evidence,:evaluation,:model,CAST(:binding AS jsonb),
              :reanalysis,'PENDING','QUEUED',:expires)
            ON CONFLICT DO NOTHING RETURNING evaluation_id
        """), {
            "id": uuid5(NAMESPACE_URL, "agent-discovery:" + digest), "schedule": schedule,
            "source": row["position"], "evidence": group.fingerprint, "evaluation": digest,
            "model": content_fingerprint(asdict(evaluation)), "expires": expires,
            "binding": json.dumps(asdict(evaluation)), "reanalysis": evaluation.reanalysis_id or "",
        }).scalar_one_or_none() is not None

    def request_reanalysis(
        self, *, schedule_id: str, epoch: int, actor: str, previous_id: UUID,
        evaluation: EvaluationBinding, pending_limit: int,
        permits: Callable[[ReadOnlyScopedConnection, str, UUID, EvaluationBinding], bool],
    ) -> UUID:
        with self.engine.begin() as connection:
            self._require_active(connection, schedule_id, epoch)
            old = connection.execute(text("""
                SELECT s.*,w.evaluation_binding FROM agent_research.discovery_work w
                JOIN agent_research.discovery_sources s ON s.position=w.source_position
                WHERE w.evaluation_id=:id AND w.schedule_id=:schedule
                  AND w.state IN ('COMPLETED','BLOCKED','OUTCOME_UNKNOWN')
            """), {"id": previous_id, "schedule": schedule_id}).mappings().one_or_none()
            if (
                old is None or not evaluation.permits_reanalysis(
                    EvaluationBinding(**old["evaluation_binding"])
                )
                or permits(ReadOnlyScopedConnection(connection), actor, previous_id, evaluation)
                   is not True
                or not _allowed(connection, old["position"])
            ):
                raise RunConflict("AGENT_POLICY_DENIED")
            group = EvidenceGroup((_evidence(old),))
            identifier = uuid5(
                NAMESPACE_URL, "agent-discovery:" + evaluation_fingerprint(group, evaluation),
            )
            prior = connection.execute(text("""
                SELECT evaluation_id,previous_id,actor FROM agent_research.discovery_reanalyses
                WHERE approval_id=:approval
            """), {"approval": evaluation.reanalysis_id}).one_or_none()
            if prior is not None:
                if tuple(prior) != (identifier, previous_id, actor):
                    raise RunConflict("IDEMPOTENCY_CONFLICT")
                return identifier
            pending = connection.scalar(text("""
                SELECT count(*) FROM agent_research.discovery_work
                WHERE schedule_id=:schedule AND state='PENDING'
            """), {"schedule": schedule_id})
            if not 1 <= pending_limit <= 10000 or pending >= pending_limit:
                raise RunConflict("BUDGET_EXHAUSTED")
            now = connection.scalar(text("SELECT clock_timestamp()"))
            if not self._enqueue(
                connection, schedule_id, old, group, evaluation, now + timedelta(days=7),
            ):
                raise RunConflict("IDEMPOTENCY_CONFLICT")
            connection.execute(text("""
                INSERT INTO agent_research.discovery_reanalyses
                  (evaluation_id,previous_id,approval_id,actor)
                VALUES (:id,:previous,:approval,:actor)
            """), {"id": identifier, "previous": previous_id,
                    "approval": evaluation.reanalysis_id, "actor": actor})
            return identifier

    def next_pending(self, claim: Claim):
        with self.engine.begin() as connection:
            scan = self._lock_scan(connection, claim)
            return connection.execute(text("""
                SELECT s.*,w.evaluation_id,w.evaluation_hash,w.model_binding_hash,w.reanalysis_key
                FROM agent_research.discovery_work w
                JOIN agent_research.discovery_sources s ON s.position=w.source_position
                WHERE w.schedule_id=:schedule AND w.state='PENDING'
                  AND w.expires_at>clock_timestamp()
                ORDER BY w.source_position LIMIT 1
            """), {"schedule": scan["schedule_id"]}).mappings().one_or_none()

    def occurrence_epoch(self, claim: Claim, schedule: str) -> int:
        with self.engine.begin() as connection:
            _lock_run(connection, claim)
            epoch = connection.scalar(text("""
                SELECT schedule_epoch FROM agent_research.discovery_occurrences
                WHERE run_id=:run AND schedule_id=:schedule
            """), {"run": claim.run_id, "schedule": schedule})
            if epoch is None:
                raise RunConflict("AGENT_POLICY_DENIED")
            return epoch

    def totals(self, claim: Claim) -> tuple[int, int, int, object, int, int]:
        with self.engine.begin() as connection:
            self._lock_scan(connection, claim)
            row = connection.execute(text("""
                SELECT count(*),coalesce(sum(input_bound),0),coalesce(sum(output_bound),0),
                  coalesce(sum(cost_bound),0)
                FROM agent_research.model_attempts WHERE run_id=:run AND status<>'UNSENT'
            """), {"run": claim.run_id}).one()
            scanned = connection.scalar(text("""
                SELECT count(DISTINCT source_position) FROM agent_research.discovery_events
                WHERE run_id=:run AND reason<>'PENDING_BACKPRESSURE'
            """), {"run": claim.run_id})
            published = connection.scalar(text("""
                SELECT count(*) FROM agent_research.discovery_publications WHERE run_id=:run
            """), {"run": claim.run_id})
            return (*row, scanned, published)

    def block_pending(self, claim: Claim, evaluation_id: UUID, reason: str) -> None:
        with self.engine.begin() as connection:
            scan = self._lock_scan(connection, claim)
            position = connection.scalar(text("""
                UPDATE agent_research.discovery_work SET state='BLOCKED',reason=:reason
                WHERE evaluation_id=:id AND schedule_id=:schedule AND state='PENDING'
                RETURNING source_position
            """), {"id": evaluation_id, "schedule": scan["schedule_id"], "reason": reason})
            if position is not None:
                _event(connection, claim, position, reason)

    def ready(self, claim: Claim) -> UUID | None:
        with self.engine.begin() as connection:
            self._lock_scan(connection, claim)
            return connection.scalar(text("""
                SELECT evaluation_id FROM agent_research.discovery_work
                WHERE run_id=:run AND state='READY' ORDER BY source_position LIMIT 1
            """), {"run": claim.run_id})

    def recover_unknown(self, claim: Claim) -> None:
        with self.engine.begin() as connection:
            _lock_run(connection, claim)
            rows = connection.execute(text("""
                UPDATE agent_research.discovery_work w
                SET state='OUTCOME_UNKNOWN',reason='OUTCOME_UNKNOWN'
                FROM agent_research.model_attempts a
                WHERE a.attempt_id=w.attempt_id AND w.run_id=:run AND w.state='DISPATCHING'
                  AND a.status='OUTCOME_UNKNOWN'
                RETURNING w.source_position
            """), {"run": claim.run_id}).scalars().all()
            for position in rows:
                _event(connection, claim, position, "OUTCOME_UNKNOWN")

    def authorize(
        self, claim: Claim, evaluation_id: UUID, attempt_id: UUID, request_hash: str, *,
        evaluation_hash: str,
        recheck: Callable[[ReadOnlyScopedConnection, EvidenceRevision], bool],
    ) -> None:
        with self.engine.begin() as connection:
            scan = self._lock_scan(connection, claim)
            connection.execute(text("SELECT agent_research.lock_discovery_source_policy()"))
            row = connection.execute(text("""
                SELECT s.*,w.evaluation_hash FROM agent_research.discovery_work w
                JOIN agent_research.discovery_sources s ON s.position=w.source_position
                WHERE w.evaluation_id=:evaluation AND w.schedule_id=:schedule
                  AND w.state='PENDING' AND w.expires_at>clock_timestamp()
                FOR UPDATE OF w
            """), {"evaluation": evaluation_id, "schedule": scan["schedule_id"]}
            ).mappings().one_or_none()
            if (
                row is None or row["evaluation_hash"] != evaluation_hash
                or not _allowed(connection, row["position"])
                or recheck(ReadOnlyScopedConnection(connection), _evidence(row)) is not True
            ):
                raise RunConflict("AGENT_POLICY_DENIED")
            ModelAttemptStore.authorize_in_transaction(connection, claim, attempt_id, request_hash)
            connection.execute(text("""
                UPDATE agent_research.discovery_work
                SET state='DISPATCHING',reason='DISPATCH_AUTHORIZED',run_id=:run,attempt_id=:attempt
                WHERE evaluation_id=:evaluation
            """), {"run": claim.run_id, "attempt": attempt_id, "evaluation": evaluation_id})
            _event(connection, claim, row["position"], "DISPATCH_AUTHORIZED")

    def settle(
        self, claim: Claim, evaluation_id: UUID, attempt_id: UUID, *,
        outcome: ModelOutcome, safe_result: list | None = None, usage: Tokens | None = None,
        denials: tuple[str, ...] = (),
    ) -> None:
        if (outcome is ModelOutcome.COMPLETED) != (safe_result is not None):
            raise ValueError("invalid discovery result")
        state = {
            ModelOutcome.COMPLETED: "READY", ModelOutcome.OUTCOME_UNKNOWN: "OUTCOME_UNKNOWN",
            ModelOutcome.RESULT_UNAVAILABLE: "BLOCKED",
        }[outcome]
        with self.engine.begin() as connection:
            _lock_run(connection, claim)
            position = connection.execute(text("""
                UPDATE agent_research.discovery_work SET state=:state,reason=:reason,
                  safe_result=CAST(:result AS jsonb)
                WHERE evaluation_id=:evaluation AND run_id=:run AND attempt_id=:attempt
                  AND state='DISPATCHING' RETURNING source_position
            """), {"state": state, "reason": outcome.value, "evaluation": evaluation_id,
                    "run": claim.run_id, "attempt": attempt_id,
                    "result": None if safe_result is None else json.dumps(safe_result)}
            ).scalar_one_or_none()
            if position is None:
                raise RunConflict("AGENT_POLICY_DENIED")
            ModelAttemptStore.settle_in_transaction(
                connection, claim, attempt_id, outcome, reported_tokens=usage,
                result_ref=str(evaluation_id) if safe_result is not None else None,
            )
            _event(connection, claim, position, outcome.value)
            for denial in denials:
                _event(connection, claim, position, denial)

    def publish(
        self, claim: Claim, evaluation_id: UUID, *,
        recheck: Callable[[ReadOnlyScopedConnection, EvidenceRevision], bool],
        write: Callable[[ScopedConnection, list, EvidenceRevision], int],
    ) -> int:
        with self.engine.begin() as connection:
            scan = self._lock_scan(connection, claim)
            connection.execute(text("SELECT agent_research.lock_discovery_source_policy()"))
            row = connection.execute(text("""
                SELECT s.*,w.safe_result FROM agent_research.discovery_work w
                JOIN agent_research.discovery_sources s ON s.position=w.source_position
                WHERE w.evaluation_id=:evaluation AND w.run_id=:run AND w.schedule_id=:schedule
                  AND w.state='READY' FOR UPDATE OF w
            """), {"evaluation": evaluation_id, "run": claim.run_id,
                    "schedule": scan["schedule_id"]}).mappings().one_or_none()
            if row is None:
                raise RunConflict("AGENT_POLICY_DENIED")
            valid = _allowed(connection, row["position"]) and recheck(
                ReadOnlyScopedConnection(connection), _evidence(row),
            ) is True
            count = write(
                ScopedConnection(connection), row["safe_result"], _evidence(row),
            ) if valid else 0
            reason = "PUBLISHED" if count else (
                "NO_NEW_RESEARCH_IDEAS" if valid else "SOURCE_NOT_ALLOWED"
            )
            connection.execute(text("""
                UPDATE agent_research.discovery_work
                SET state=:state,reason=:reason,safe_result=NULL
                WHERE evaluation_id=:id
            """), {"state": "COMPLETED" if valid else "BLOCKED",
                    "reason": reason, "id": evaluation_id})
            _event(connection, claim, row["position"], reason)
            _lock_run(connection, claim)
            return count
