"""Schedule and run an offline, evidence-bound discovery step."""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from packages.agent_research.budget import ModelOutcome, TokenPrices, Tokens, reserve_model
from packages.agent_research.companion.dialogue import Dialogue
from packages.agent_research.companion.ideas import IdeaAction, IdeaLifecycle, IdeaOrigin
from packages.agent_research.companion.store import CompanionStore
from packages.agent_research.companion.testability import HypothesisIdentity, hypothesis_signature
from packages.agent_research.discovery.evidence import (
    EvaluationBinding,
    EvidenceAccess,
    EvidenceKind,
    EvidenceRevision,
    TriggerPolicy,
    evaluate_evidence,
)
from packages.agent_research.discovery.publication import (
    EvidenceClaim,
    KnownPublication,
    PublicationDenied,
    _related,
    validate_publication,
)
from packages.agent_research.discovery.schedule import ScheduleDenied, ScheduleStore
from packages.agent_research.discovery.store import DiscoveryStore, _event, _evidence
from packages.agent_research.model_attempts import ModelAttemptStore
from packages.agent_research.registry import AgentVersion
from packages.agent_research.run_state import RunState
from packages.agent_research.runs import RunKind, RunStore
from packages.persistence.transaction import ReadOnlyScopedConnection
from packages.research.models import content_fingerprint
from services.llm_gateway.discovery import OfflineDiscoveryDispatch, ProviderDiscoveryDispatch
from services.llm_gateway.provider_http import conservative_input_bound, provider_request_bytes
from services.llm_gateway.provider_wire import ResultState
from services.runtime.agent_research import ResearchSupervisor, StepResult


@dataclass(frozen=True, slots=True)
class DiscoveryConfiguration:
    agent: AgentVersion
    trigger: TriggerPolicy
    evaluation: EvaluationBinding
    prices: TokenPrices
    tokens: Tokens
    context_window: int
    policy_version: int = 1
    provider_authority: Callable[[ReadOnlyScopedConnection], bool] | None = None
    evidence_revision: int | None = None
    data_classes: frozenset[str] = frozenset()


_SOURCE_KINDS = {
    "HISTORICAL_REVISION": {EvidenceKind.MARKET_REVISION, EvidenceKind.DATA_QUALITY},
    "PERSISTED_RESEARCH_RESULT": {EvidenceKind.RESEARCH_FINDING, EvidenceKind.QUANT_RESULT},
}


class ResearchDiscovery:
    def __init__(
        self, schedules: ScheduleStore, store: DiscoveryStore, attempts: ModelAttemptStore,
        gateway: OfflineDiscoveryDispatch | ProviderDiscoveryDispatch, ideas: CompanionStore,
        lifecycle: IdeaLifecycle, *,
        configure: Callable[[str], DiscoveryConfiguration],
        access: Callable[[ReadOnlyScopedConnection, EvidenceRevision], EvidenceAccess],
        recheck: Callable[[ReadOnlyScopedConnection, EvidenceRevision], bool],
        evidence_claim: Callable[[EvidenceRevision], EvidenceClaim],
        permits: Callable[[dict], bool], actor: str = "local-operator",
    ):
        self.schedules = schedules
        self.store = store
        self.attempts = attempts
        self.gateway = gateway
        self.ideas = ideas
        self.lifecycle = lifecycle
        self.configure = configure
        self.access = access
        self.recheck = recheck
        self.evidence_claim = evidence_claim
        self.permits = permits
        self.actor = actor

    def tick(self, schedule_id: str):
        config = self.configure(schedule_id)

        def enqueue(connection, schedule, occurrence, *, epoch):
            if config.provider_authority is not None and (
                config.provider_authority(ReadOnlyScopedConnection(connection)) is not True
            ):
                raise ScheduleDenied("DISCOVERY_SCHEDULE_STALE")
            key = content_fingerprint((schedule.schedule_id, occurrence.local_date.isoformat()))
            return RunStore.enqueue_in_transaction(
                connection, actor=self.actor, key=key, kind=RunKind.SCHEDULED,
                input_ref=schedule.schedule_id, trace_id=key[:32], agent=config.agent,
            )

        return self.schedules.tick(schedule_id, enqueue=enqueue)

    def supervisor(self, schedule_ids: tuple[str, ...]) -> ResearchSupervisor:
        if len(schedule_ids) > 100 or len(set(schedule_ids)) != len(schedule_ids):
            raise ValueError("invalid discovery schedule list")

        def tick():
            for schedule_id in schedule_ids:
                self.tick(schedule_id)

        return ResearchSupervisor(
            RunStore(self.store.engine), {RunKind.SCHEDULED: self.step},
            attempts=self.attempts, tick_schedules=tick,
        )

    def request_reanalysis(
        self, schedule_id, epoch, previous_id, approval_id, *, permits, connection=None,
    ):
        schedule = self.schedules.read(schedule_id, connection=connection)
        if schedule is None:
            raise ScheduleDenied("DISCOVERY_SCHEDULE_STALE")
        config = self.configure(schedule_id)
        evaluation = replace(
            config.evaluation, reanalysis_id=approval_id,
            envelope_hash=content_fingerprint(asdict(self.gateway.capability)),
        )
        return self.store.request_reanalysis(
            schedule_id=schedule_id, epoch=epoch, actor=self.actor, previous_id=previous_id,
            evaluation=evaluation,
            pending_limit=schedule.to_document()["limits"]["maxPendingItems"],
            permits=permits,
            connection=connection,
        )

    def step(self, claim, work, recovered) -> StepResult:
        try:
            return self._step(claim, work, recovered)
        except ScheduleDenied:
            return StepResult(RunState.BLOCKED, "DISCOVERY_SCHEDULE_STALE")

    def _step(self, claim, work, recovered) -> StepResult:
        if any(attempt.status == "OUTCOME_UNKNOWN" for attempt in recovered):
            self.store.recover_unknown(claim)
            return StepResult(RunState.BLOCKED, "OUTCOME_UNKNOWN")
        schedule = self.schedules.read(work.input_ref)
        if schedule is None:
            return StepResult(RunState.BLOCKED, "DISCOVERY_SCHEDULE_STALE")
        config = self.configure(work.input_ref)
        document = schedule.to_document()
        scope = document["scope"]
        if isinstance(self.gateway, ProviderDiscoveryDispatch) and (
            config.provider_authority is None or config.evidence_revision is None
            or not set(scope["sourceKinds"]) <= config.data_classes
        ):
            return StepResult(RunState.BLOCKED, "DISCOVERY_BINDING_CHANGED")
        kinds = set().union(*(_SOURCE_KINDS[name] for name in scope["sourceKinds"]))
        if (
            document["triggerPolicyVersion"] != config.policy_version
            or config.trigger.rule_version != config.evaluation.rule_version
            or config.trigger.allowlist_version != config.evaluation.allowlist_version
            or (config.evaluation.agent_id, config.evaluation.agent_version) != (
                config.agent.agent_id, config.agent.version,
            )
        ):
            return StepResult(RunState.BLOCKED, "DISCOVERY_BINDING_CHANGED")
        config = replace(config, trigger=replace(
            config.trigger, instruments=config.trigger.instruments & set(scope["instrumentIds"]),
            kinds=config.trigger.kinds & kinds,
        ), evaluation=replace(
            config.evaluation, envelope_hash=content_fingerprint(asdict(self.gateway.capability)),
        ))
        epoch = self.store.occurrence_epoch(claim, work.input_ref)
        self.store.open_scan(
            claim, schedule_id=work.input_ref, epoch=epoch, policy=config.trigger,
        )
        limits = document["limits"]
        ready = self.store.ready(claim)
        if ready is not None:
            count = self._publish(claim, ready, limits["maxIdeas"], config)
            return StepResult(RunState.QUEUED, "PUBLISHED" if count else "NO_NEW_RESEARCH_IDEAS")
        calls, spent_input, spent_output, spent_cost, scanned, published = self.store.totals(claim)
        if published >= limits["maxIdeas"]:
            return StepResult(RunState.COMPLETED, "DISCOVERY_PUBLICATION_LIMIT")
        remaining = limits["maxSourceItems"] - scanned
        scan = None if remaining <= 0 else self.store.scan(
            claim, config.trigger, config.evaluation, access=self.access,
            limit=min(50, remaining), pending_limit=limits["maxPendingItems"],
            ttl=timedelta(days=7),
        )
        item = self.store.next_pending(claim)
        if item is None:
            return StepResult(
                RunState.QUEUED if scan is not None and scan.more else RunState.COMPLETED,
                "NO_NEW_RESEARCH_IDEAS",
            )
        reserved = reserve_model(config.prices, config.tokens, config.context_window).bound
        if (
            calls >= limits["maxModelCalls"] or limits["maxIdeas"] == 0
            or spent_input + reserved.input_tokens > limits["maxInputTokens"]
            or spent_output + reserved.output_tokens > limits["maxOutputTokens"]
            or spent_cost + reserved.cost > Decimal(limits["maxCost"])
            or reserved.currency != limits["currency"]
        ):
            return StepResult(RunState.BUDGET_EXHAUSTED, "PENDING_BUDGET")
        return self._dispatch(claim, work, item, config, min(16, limits["maxIdeas"]))

    def _dispatch(self, claim, work, item, config, max_ideas):
        capability, model = self.gateway.capability, config.agent.model_binding
        evaluation = replace(config.evaluation, reanalysis_id=item["reanalysis_key"] or None)
        if (
            model is None or item["model_binding_hash"] != content_fingerprint(
                asdict(evaluation)
            )
            or (capability.provider, capability.model, capability.profile_id,
                capability.profile_generation, capability.model_generation) != (
                model.provider, model.model_id, model.profile_id,
                model.profile_generation, model.model_generation,
            )
            or (evaluation.provider, evaluation.model_id, evaluation.profile_id,
                evaluation.profile_generation, evaluation.model_generation) != (
                capability.provider, capability.model, capability.profile_id,
                capability.profile_generation, capability.model_generation,
            )
            or capability.max_output_tokens > config.tokens.output
        ):
            self.store.block_pending(claim, item["evaluation_id"], "DISCOVERY_BINDING_CHANGED")
            return StepResult(RunState.BLOCKED, "DISCOVERY_BINDING_CHANGED")
        source = _evidence(item)
        evidence = self.evidence_claim(source)
        if (
            not evidence.rights_allowed or not evidence.quality_ok or evidence.retracted
            or (evidence.kind, evidence.evidence_id, evidence.revision, evidence.fingerprint,
                evidence.instrument_id, evidence.observed_at,
                evidence.observation_start, evidence.observation_end) != (
                source.position.kind, source.position.source_id, source.revision,
                source.content_hash,
                source.instrument_id, source.observed_at, source.window_start, source.window_end,
            )
        ):
            self.store.block_pending(claim, item["evaluation_id"], "SOURCE_NOT_ALLOWED")
            return StepResult(RunState.QUEUED, "SOURCE_NOT_ALLOWED")
        payload = json.dumps({
            "purpose": "PROACTIVE_RESEARCH_DISCOVERY_V1",
            "evidence": source.immutable_binding(),
            "quotes": evidence.quotes,
        }, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        production = isinstance(self.gateway, ProviderDiscoveryDispatch)
        if production and (config.provider_authority is None or config.evidence_revision is None):
            return StepResult(RunState.BLOCKED, "DISCOVERY_BINDING_CHANGED")
        if self.permits(json.loads(payload)) is not True or (
            not production and conservative_input_bound(capability, payload) > config.tokens.input
        ):
            self.store.block_pending(claim, item["evaluation_id"], "DISCOVERY_CONTEXT_DENIED")
            return StepResult(RunState.QUEUED, "DISCOVERY_CONTEXT_DENIED")
        identity = f"discovery-attempt:{claim.run_id}:{claim.fence}:{item['evaluation_id']}"
        attempt_id = uuid5(NAMESPACE_URL, identity)
        bound_bytes = provider_request_bytes(capability, payload) if production else payload
        request_hash = content_fingerprint((
            item["evaluation_hash"], sha256(bound_bytes).hexdigest(),
        ))
        authority = (
            {"evidence_revision": config.evidence_revision, "authority_hash": request_hash}
            if production else {}
        )
        timeout = int(capability.timeout_seconds)
        deadline = min(work.dispatch_deadline, datetime.now(UTC) + timedelta(seconds=timeout))
        if deadline <= datetime.now(UTC):
            return StepResult(RunState.QUEUED, "LEASE_DEADLINE")
        attempt = self.attempts.reserve(
            claim, attempt_id=attempt_id, step_id=f"discovery:{item['evaluation_id']}",
            request_hash=request_hash, model=model, prices=config.prices,
            tokens=config.tokens, context_window=config.context_window, timeout_seconds=timeout,
            **authority,
        )
        if attempt.status != "RESERVED":
            return StepResult(RunState.BLOCKED, "OUTCOME_UNKNOWN")

        def authorize():
            self.store.authorize(
                claim, item["evaluation_id"], attempt_id, request_hash,
                evaluation_hash=item["evaluation_hash"],
                recheck=lambda scope, evidence: self._current(scope, evidence, config),
                **authority,
            )

        result = self.gateway.send(
            attempt_id, request_hash, deadline, payload, authorize=authorize,
        )
        if result.state is ResultState.DISPATCH_DENIED:
            self.attempts.settle(
                claim, attempt_id, ModelOutcome.UNSENT, unsent_evidence_ref=str(attempt_id),
                **authority,
            )
            self.store.block_pending(claim, item["evaluation_id"], "DISCOVERY_DISPATCH_DENIED")
            return StepResult(RunState.QUEUED, "DISCOVERY_DISPATCH_DENIED")
        usage = None if result.usage is None else Tokens(
            result.usage.input_tokens, result.usage.output_tokens,
        )
        if result.state is ResultState.OUTCOME_UNKNOWN:
            self.store.settle(
                claim, item["evaluation_id"], attempt_id, outcome=ModelOutcome.OUTCOME_UNKNOWN,
                **authority,
            )
            return StepResult(RunState.BLOCKED, "OUTCOME_UNKNOWN")
        candidates = None
        denials = ()
        if result.state is ResultState.ACCEPTED:
            try:
                validated = validate_publication(
                    json.dumps(result.output).encode(), evidence=(evidence,), now=datetime.now(UTC),
                    priority_rule_version=config.trigger.rule_version, max_ideas=max_ideas,
                    permits=self.permits,
                )
                candidates = [{
                    "publication": candidate.publication_key,
                    "hypothesis": candidate.hypothesis_key,
                    "comparison": content_fingerprint(candidate.comparison_key),
                    "comparison_fields": list(candidate.comparison_key),
                    "idea": candidate.idea_content(run_id=str(claim.run_id)),
                } for candidate in validated.candidates]
                denials = validated.denials
            except PublicationDenied as error:
                denials = (str(error),)
        outcome = ModelOutcome.RESULT_UNAVAILABLE if candidates is None else ModelOutcome.COMPLETED
        self.store.settle(
            claim, item["evaluation_id"], attempt_id,
            outcome=outcome, safe_result=candidates, usage=usage, denials=denials,
            **authority,
        )
        return StepResult(RunState.QUEUED, "DISCOVERY_RESULT_SAVED")

    def _current(self, scope, source: EvidenceRevision, config: DiscoveryConfiguration) -> bool:
        if config.provider_authority is not None and config.provider_authority(scope) is not True:
            return False
        now = scope.execute(text("SELECT clock_timestamp()")).scalar_one()
        return evaluate_evidence(
            source, self.access(scope, source), config.trigger, now=now,
        ).eligible and self.recheck(scope, source) is True

    def _publish(self, claim, evaluation_id, limit: int, config: DiscoveryConfiguration) -> int:
        def write(scope, candidates, source):
            scope.execute(text("SELECT pg_advisory_xact_lock(894217349)"))
            count = scope.execute(text("""
                SELECT count(*) FROM agent_research.discovery_publications WHERE run_id=:run
            """), {"run": claim.run_id}).scalar_one()
            added = 0
            for candidate in candidates[:max(0, limit - count)]:
                existing = scope.execute(text("""
                    SELECT EXISTS(SELECT FROM agent_research.discovery_publications
                      WHERE publication_key=:publication)
                """), {"publication": candidate["publication"]}).scalar_one()
                if existing:
                    _event(scope, claim, source.position.commit_version, "DUPLICATE_RESEARCH_IDEA")
                    continue
                document = candidate["idea"]
                if self._known_manual(scope, document):
                    _event(scope, claim, source.position.commit_version, "PRIOR_RESEARCH_EXISTS")
                    continue
                known = self._known(scope, candidate)
                if known is None:
                    _event(scope, claim, source.position.commit_version, "HISTORY_REVIEW_REQUIRED")
                    continue
                try:
                    related = _related(
                        known, candidate["hypothesis"], tuple(candidate["comparison_fields"]),
                        (self.evidence_claim(source),), datetime.fromisoformat(
                            document["observationStart"],
                        ), datetime.fromisoformat(document["observationEnd"]),
                    )
                except PublicationDenied as error:
                    _event(scope, claim, source.position.commit_version, str(error))
                    continue
                idea_id = str(uuid5(NAMESPACE_URL, candidate["publication"]))
                dialogue_id = str(uuid5(NAMESPACE_URL, f"discovery-dialogue:{idea_id}"))
                now = datetime.now(UTC)
                version = self.lifecycle.create(
                    document | {"relatedIdeaIds": list(related)},
                    idea_id=idea_id, initiator_id=self.actor,
                    origin=IdeaOrigin.AGENT, created_at=now,
                )
                proposed = self.lifecycle.revise(
                    version, expected_version=1, action=IdeaAction.PUBLISH,
                    decision_id=candidate["publication"], created_at=now,
                )
                self.ideas.create(
                    self.actor, idea_id, dialogue_id, Dialogue(dialogue_id, 2).record(),
                    key=candidate["publication"], command=candidate["publication"].encode(),
                    make_idea=version.to_document, scope=scope, successor=proposed.to_document(),
                )
                scope.execute(text("""
                    INSERT INTO agent_research.discovery_publications
                      (publication_key,hypothesis_key,comparison_key,comparison_fields,run_id,
                       evaluation_id,idea_id,idea_version)
                    VALUES(:publication,:hypothesis,:comparison,CAST(:fields AS jsonb),
                      :run,:evaluation,:idea,2)
                """), {key: candidate[key] for key in ("publication", "hypothesis", "comparison")}
                    | {"run": claim.run_id, "evaluation": evaluation_id, "idea": idea_id,
                       "fields": json.dumps(candidate["comparison_fields"])})
                added += 1
            return added

        return self.store.publish(
            claim, evaluation_id,
            recheck=lambda scope, evidence: self._current(scope, evidence, config), write=write,
        )

    def _known_manual(self, scope, idea: dict) -> bool:
        signature = hypothesis_signature(HypothesisIdentity.from_document(idea["hypothesis"]))
        if signature is None:
            return False
        return scope.execute(text("""
            SELECT EXISTS(SELECT FROM agent_research.companion_idea_versions v
              JOIN agent_research.companion_ideas i USING(idea_id)
              WHERE i.actor=:actor AND v.hypothesis_signature=:signature
                AND v.document->>'origin'='USER')
        """), {"actor": self.actor, "signature": signature}).scalar_one()

    def _known(self, scope, candidate: dict) -> tuple[KnownPublication, ...] | None:
        instrument, direction, *_ = candidate["comparison_fields"]
        idea = candidate["idea"]
        rows = scope.execute(text("""
            SELECT p.idea_id,p.hypothesis_key,p.comparison_fields,v.document
            FROM agent_research.discovery_publications p
            JOIN agent_research.companion_ideas i ON i.idea_id=p.idea_id
            JOIN agent_research.companion_idea_versions v ON v.idea_id=p.idea_id
              AND v.version=p.idea_version
            WHERE i.actor=:actor AND p.comparison_fields->>0=:instrument
              AND (p.hypothesis_key=:hypothesis OR (
                p.comparison_fields->>1=:direction
                AND (v.document->>'observationStart')::timestamptz<CAST(:end AS timestamptz)
                AND (v.document->>'observationEnd')::timestamptz>CAST(:start AS timestamptz)))
            ORDER BY p.publication_key LIMIT 201
        """), {"actor": self.actor, "instrument": instrument, "direction": direction,
                "hypothesis": candidate["hypothesis"], "start": idea["observationStart"],
                "end": idea["observationEnd"]}).all()
        if len(rows) > 200:
            return None
        return tuple(KnownPublication(
            row.idea_id, row.hypothesis_key, tuple(row.comparison_fields),
            datetime.fromisoformat(row.document["observationStart"]),
            datetime.fromisoformat(row.document["observationEnd"]),
            frozenset(
                (item["kind"], item["evidenceId"], item["revision"])
                for item in row.document["evidence"]
            ),
        ) for row in rows)
