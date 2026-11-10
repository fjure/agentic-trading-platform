"""SQL-only QSL ports. No simulation, provider access or HTTP availability.

Each method commits a short transaction. A batch owns the existing global lease;
internal runs are immutable execution references and never jobs of their own.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from packages.backtesting.jobs import BacktestError, encoded
from packages.research.models import content_fingerprint

VERSION = "quant-experiment-v1"
EXPOSURE_VERSION = "quant-exposure-v1"
PUBLIC_ROLES = {
    "SEARCH": "SEARCH_CANDIDATE",
    "FLAT_REFERENCE": "REFERENCE_FLAT",
    "BUY_HOLD_REFERENCE": "REFERENCE_BUY_HOLD",
}


@lru_cache(maxsize=4)
def _validator(component):
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "contracts/schemas/v1/quant-lab.schema.json"
        ).read_text(encoding="utf-8")
    )
    return Draft202012Validator(
        {**schema, "$ref": "#/$defs/" + component},
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def reliability_identity(report, findings, references):
    return dict(
        report={
            key: value
            for key, value in report.items()
            if key not in {"reportHash", "reportId", "batchId", "runId"}
        },
        findings=findings,
        revisionReferences=references,
    )


def result_identity(body, *, candidate_id, role, phase_key, status):
    projection = {
        key: value
        for key, value in body.items()
        if key not in {"resultHash", "runId", "reportId", "batchId"}
    }
    if isinstance(projection.get("reliability"), dict):
        excluded = {"runId", "reportId"}
        if role == "SEARCH" and status == "COMPLETE" and phase_key.endswith(":validation"):
            excluded.add("reportHash")
        projection["reliability"] = {
            key: value for key, value in projection["reliability"].items() if key not in excluded
        }
    return ("quant-candidate-result-v1", candidate_id, role, phase_key, status, projection)


def definition_domain(body, *, definition_version=1, predecessor_hash=None):
    """Map the closed transport input into the one canonical domain identity."""
    from packages.backtesting.experiment import (
        ExperimentDefinition,
        ExperimentFold,
        PhaseProtocol,
        PhaseWindow,
        SearchSpace,
        StrategySelectionPolicy,
    )

    def window(value):
        return PhaseWindow(
            datetime.fromisoformat(value["start"].replace("Z", "+00:00")),
            datetime.fromisoformat(value["end"].replace("Z", "+00:00")),
        )

    if any(fold["fold"] != index for index, fold in enumerate(body["protocol"]["folds"])):
        raise ValueError("fold indexes must follow the immutable protocol order")
    protocol = PhaseProtocol(
        tuple(
            ExperimentFold(*(window(fold[key]) for key in ("training", "validation", "test")))
            for fold in body["protocol"]["folds"]
        ),
        window(body["protocol"]["holdout"]),
    )
    return ExperimentDefinition(
        UUID(body["instrumentId"]),
        body["symbol"],
        body["hypothesis"],
        tuple(SearchSpace.from_mapping(space) for space in body["searchSpaces"]),
        protocol,
        StrategySelectionPolicy.from_mapping(body["strategySelectionPolicy"]),
        start_cash=Decimal(body["startCash"]),
        fee_usd=Decimal(body["feeUsd"]),
        slippage_bps=Decimal(body["slippageBps"]),
        quantity_step=Decimal(body["quantityStep"]),
        research_mode=body["researchMode"],
        definition_version=definition_version,
        predecessor_hash=predecessor_hash,
    )


def definition_wire(domain, request):
    """Store the canonical spelling of the same typed definition and policy."""
    from packages.domain.models import utc_string
    from packages.research.models import _canonical

    policy = domain.strategy_selection_policy
    body = dict(
        request,
        searchSpaces=[
            dict(
                strategyId=space.strategy_id,
                version=space.strategy_version,
                parameterValues={key: list(values) for key, values in space.parameter_values},
            )
            for space in domain.search_spaces
        ],
        strategySelectionPolicy=dict(
            policyId=policy.policy_id,
            maxDrawdown=policy.max_drawdown,
            minValidationSessions=policy.min_validation_sessions,
            minimumEntryCount=policy.minimum_entry_count,
            minimumInvestedBarRatio=policy.minimum_invested_bar_ratio,
        ),
        startCash=_canonical(domain.start_cash),
        feeUsd=_canonical(domain.fee_usd),
        slippageBps=_canonical(domain.slippage_bps),
        quantityStep=_canonical(domain.quantity_step),
    )

    def period(window):
        return dict(start=utc_string(window.start), end=utc_string(window.end))

    body["protocol"] = dict(
        request["protocol"],
        folds=[
            dict(
                fold=index,
                training=period(fold.training),
                validation=period(fold.validation),
                test=period(fold.test),
            )
            for index, fold in enumerate(domain.protocol.folds)
        ],
        holdout=period(domain.protocol.holdout),
    )
    return body


@dataclass(frozen=True)
class ExperimentLease:
    batch_id: UUID
    owner: UUID
    fence: int
    row: dict


class ExperimentStorage:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def transaction(self, lease=None):
        failure = None
        try:
            with self.engine.begin() as connection:
                connection.execute(text("SET LOCAL statement_timeout='2s'"))
                connection.execute(text("SET LOCAL lock_timeout='1s'"))
                connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
                connection.execute(
                    text(
                        "SELECT set_config('backtest.quant_version',:version,true),"
                        "set_config('backtest.exposure_version',:exposure,true)"
                    ),
                    {"version": VERSION, "exposure": EXPOSURE_VERSION},
                )
                if lease is not None:
                    connection.execute(
                        text(
                            "SELECT set_config('backtest.owner',:owner,true),"
                            "set_config('backtest.fence',:fence,true)"
                        ),
                        {"owner": str(lease.owner), "fence": str(lease.fence)},
                    )
                    self.fenced(connection, lease)
                yield connection
                if lease is not None:
                    # A terminal publish verifies its fence before changing status.
                    running = connection.execute(
                        text(
                            "SELECT status='RUNNING' FROM "
                            "backtest.experiment_batches WHERE batch_id=:id"
                        ),
                        {"id": lease.batch_id},
                    ).scalar_one()
                    if running:
                        self.fenced(connection, lease)
        except SQLAlchemyError as error:
            message = getattr(
                getattr(getattr(error, "orig", None), "diag", None), "message_primary", ""
            )
            status, code = {
                "backtest idempotency conflict": (409, "IDEMPOTENCY_CONFLICT"),
                "backtest queue full": (429, "QUEUE_EXHAUSTED"),
                "quant definition conflict": (409, "DEFINITION_CONFLICT"),
                "quant validation conflict": (409, "DEFINITION_CONFLICT"),
                "quant lease lost": (409, "LEASE_LOST"),
                "quant snapshot conflict": (409, "SNAPSHOT_CONFLICT"),
                "quant seed conflict": (409, "SNAPSHOT_CONFLICT"),
                "quant budget exhausted": (409, "BUDGET_EXHAUSTED"),
                "APPROVAL_REQUIRED": (409, "APPROVAL_REQUIRED"),
                "quant approval conflict": (409, "APPROVAL_STALE"),
            }.get(message, (503, "UNAVAILABLE"))
            failure = BacktestError(status, code)
        if failure:
            raise failure from None

    @staticmethod
    def fenced(connection, lease):
        connection.execute(text("SELECT backtest.assert_quant_fence(:id)"), {"id": lease.batch_id})

    def create_definition(
        self, definition, candidates, *, actor="local-operator", identifier=None,
        agent_origin: tuple[UUID, str, int] | None = None,
    ):
        """Candidates are validated QSL domain configurations with explicit roles.

        Configuration hashing excludes role, so an explicit search flat remains a
        separate role even when it has the same configuration as the reference.
        """
        identifier = identifier or uuid4()
        _validator("QuantLabExperimentCreateRequest").validate(definition)
        with self.transaction() as connection:
            predecessor = (
                self._definition(connection, UUID(definition["predecessorDefinitionId"]))
                if definition.get("predecessorDefinitionId")
                else None
            )
            domain = definition_domain(
                definition,
                definition_version=(predecessor["definition_version"] + 1 if predecessor else 1),
                predecessor_hash=predecessor["fingerprint"] if predecessor else None,
            )
            definition = definition_wire(domain, definition)
            from packages.backtesting.experiment import (
                CandidateConfiguration,
                canonical_definition_preimage,
                canonical_policy_preimage,
            )

            prepared = []
            for item in candidates:
                configuration = item["configuration"]
                candidate = CandidateConfiguration(
                    configuration["strategyId"],
                    tuple(sorted(configuration["parameters"].items())),
                    configuration["version"],
                )
                configuration = dict(
                    strategyId=candidate.strategy_id,
                    version=candidate.strategy_version,
                    parameters=dict(candidate.parameters),
                )
                preimage = (candidate.strategy_id, candidate.strategy_version, candidate.parameters)
                prepared.append(
                    dict(
                        role=item["role"],
                        candidateId=candidate.candidate_id,
                        configuration=configuration,
                        canonicalText=encoded(preimage),
                    )
                )
            if {c["candidateId"] for c in prepared if c["role"] == "SEARCH"} != {
                c.candidate_id for c in domain.candidates
            }:
                raise ValueError("search configurations must equal the complete registered grid")
            # API replay identifiers are deterministic. Serialize the read/insert
            # for one identifier without locking unrelated draft creation.
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"quant-definition:{identifier}"},
            )
            existing = (
                connection.execute(
                    text("SELECT * FROM backtest.experiment_definitions WHERE definition_id=:id"),
                    {"id": identifier},
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if (
                    existing["actor"] != actor
                    or existing["fingerprint"] != domain.definition_hash
                    or existing["body"] != definition
                ):
                    raise BacktestError(409, "IDEMPOTENCY_CONFLICT")
                if agent_origin is not None:
                    bound = connection.execute(text("""
                        SELECT run_id,idea_id,idea_version
                        FROM backtest.experiment_agent_provenance WHERE definition_id=:id
                    """), {"id": identifier}).one_or_none()
                    if bound is None or tuple(bound) != agent_origin:
                        raise BacktestError(409, "IDEMPOTENCY_CONFLICT")
                return dict(existing)
            operation = (
                "request_experiment_definition" if agent_origin is None
                else "request_agent_experiment_definition"
            )
            origin_arguments = "" if agent_origin is None else ",:run,:idea,:idea_version"
            origin_values = {} if agent_origin is None else dict(
                zip(("run", "idea", "idea_version"), agent_origin, strict=True),
            )
            connection.execute(
                text(
                    f"SELECT backtest.{operation}(:id,:actor,"
                    "CAST(:body AS "
                    "jsonb),:canonical,:hash,:policy,:policy_canonical,CAST(:candidates AS "
                    f"jsonb){origin_arguments})"
                ),
                dict(
                    id=identifier,
                    actor=actor,
                    body=encoded(definition),
                    canonical=encoded(canonical_definition_preimage(domain)),
                    hash=domain.definition_hash,
                    policy=domain.strategy_selection_policy.policy_hash,
                    policy_canonical=encoded(
                        canonical_policy_preimage(domain.strategy_selection_policy)
                    ),
                    candidates=encoded(prepared),
                    **origin_values,
                ),
            )
            return self._definition(connection, identifier)

    @staticmethod
    def _definition(connection, identifier):
        row = (
            connection.execute(
                text("SELECT * FROM backtest.experiment_definitions WHERE definition_id=:id"),
                {"id": identifier},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BacktestError(404, "NOT_FOUND")
        return dict(row)

    def definition(self, identifier):
        with self.transaction() as connection:
            return self._definition(connection, identifier)

    def source_receipt(self, batch_id):
        with self.transaction() as connection:
            return dict(
                connection.execute(
                    text("SELECT * FROM backtest.experiment_source_receipts WHERE batch_id=:id"),
                    {"id": batch_id},
                )
                .mappings()
                .one()
            )

    def validate(self, identifier, validation):
        with self.transaction() as connection:
            validation_id = connection.execute(
                text(
                    "SELECT backtest.validate_experiment(:id,CAST(:body AS jsonb),:canonical,:hash)"
                ),
                dict(
                    id=identifier,
                    body=encoded(validation),
                    canonical=encoded(validation),
                    hash=content_fingerprint(validation),
                ),
            ).scalar_one()
            return dict(
                connection.execute(
                    text("SELECT * FROM backtest.experiment_validations WHERE validation_id=:id"),
                    {"id": validation_id},
                )
                .mappings()
                .one()
            )

    def record_validation_request(self, identifier, key, validation, *, valid):
        key_hash = sha256(key.encode("ascii")).hexdigest()
        with self.transaction() as connection:
            connection.execute(
                text(
                    "SELECT backtest.record_experiment_validation_request("
                    ":id,:key,CAST(:body AS jsonb),:valid)"
                ),
                {"id": identifier, "key": key_hash, "body": encoded(validation), "valid": valid},
            )
            return self._validation_request(connection, identifier, key_hash=key_hash)

    @staticmethod
    def _validation_request(connection, identifier, *, key_hash=None, invalid_id=None):
        row = (
            connection.execute(
                text("""SELECT r.validation_id,r.definition_id,r.validated_at,r.valid,
                coalesce(r.fingerprint,v.fingerprint) AS fingerprint,
                r.body
              FROM backtest.experiment_validation_requests r
              LEFT JOIN backtest.experiment_validations v ON r.approval_id=v.validation_id
              WHERE r.definition_id=:definition AND
                CASE WHEN CAST(:key AS text) IS NOT NULL THEN r.key_hash=:key
                ELSE NOT r.valid AND (CAST(:id AS uuid) IS NULL OR r.validation_id=:id) END
              ORDER BY r.validated_at DESC,r.validation_id DESC LIMIT 1"""),
                {"definition": identifier, "id": invalid_id, "key": key_hash},
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    def validation_request(self, identifier, key):
        with self.transaction() as connection:
            return self._validation_request(
                connection, identifier, key_hash=sha256(key.encode("ascii")).hexdigest()
            )

    def read_invalid_validation(self, identifier, *, validation_id=None):
        with self.transaction() as connection:
            return self._validation_request(connection, identifier, invalid_id=validation_id)

    def enqueue(
        self,
        identifier,
        definition_hash,
        actor,
        key,
        trace,
        code_version,
        *,
        definition_version,
        validation_id,
        validation_hash,
    ):
        with self.transaction() as connection:
            batch_id = connection.execute(
                text(
                    "SELECT backtest.request_experiment_batch("
                    ":id,:definition,:version,:hash,:validation,:validation_hash,"
                    ":actor,:key,:code,:trace)"
                ),
                dict(
                    id=uuid4(),
                    definition=identifier,
                    version=definition_version,
                    hash=definition_hash,
                    validation=validation_id,
                    validation_hash=validation_hash,
                    actor=actor,
                    key=key,
                    code=code_version,
                    trace=trace,
                ),
            ).scalar_one()
            return dict(
                connection.execute(
                    text("SELECT * FROM backtest.experiment_batches WHERE batch_id=:id"),
                    {"id": batch_id},
                )
                .mappings()
                .one()
            )

    def enqueue_approved(self, approval_id, binding_hash, actor, key, trace, code_version):
        """The private role can consume a persisted approval through canonical admission."""
        with self.transaction() as connection:
            batch_id = connection.execute(text("""
                SELECT agent_research.consume_quant_approval(
                    :approval,:hash,:actor,:key,:trace,:code,:batch)
            """), {
                "approval": approval_id, "hash": binding_hash, "actor": actor,
                "key": key, "trace": trace, "code": code_version, "batch": uuid4(),
            }).scalar_one()
            return batch_id

    def prepare_approval(self, binding, key):
        with self.transaction() as connection:
            return connection.execute(text("""
                SELECT agent_research.prepare_quant_approval(:id,CAST(:binding AS jsonb),:body,:key)
            """), {
                "id": uuid4(), "binding": binding.canonical_text, "body": binding.canonical_text,
                "key": key,
            }).scalar_one()

    def record_approximation(
        self, actor, idea_id, idea_version, decision_id, revision, proposal_id, proposal_hash,
    ):
        with self.transaction() as connection:
            return connection.execute(text("""
                SELECT agent_research.record_quant_approximation(
                    :actor,:idea,:version,:decision,:revision,:proposal,:hash)
            """), {
                "actor": actor, "idea": idea_id, "version": idea_version,
                "decision": decision_id, "revision": revision,
                "proposal": proposal_id, "hash": proposal_hash,
            }).scalar_one()

    def register_approximation(self, definition_id, proposal_id, approximation):
        with self.transaction() as connection:
            connection.execute(text("""
                SELECT agent_research.register_quant_approximation(
                    :idea,:version,:definition,:proposal,:hash,:description)
            """), {
                "idea": approximation.idea_id, "version": approximation.idea_version,
                "definition": definition_id, "proposal": proposal_id,
                "hash": approximation.binding, "description": approximation.difference,
            })

    def record_approval_denial(self, approval_id, actor, trace, code):
        with self.transaction() as connection:
            connection.execute(text("""
                SELECT agent_research.record_quant_approval_denial(:id,:actor,:trace,:code)
            """), {"id": approval_id, "actor": actor, "trace": trace, "code": code})

    def approval_budget(self, batch_id):
        with self.transaction() as connection:
            available = connection.execute(
                text("SELECT to_regprocedure('backtest.agent_batch_budget(uuid)') IS NOT NULL"),
            ).scalar_one()
            if not available:
                return None
            return connection.execute(
                text("SELECT backtest.agent_batch_budget(:id)"), {"id": batch_id},
            ).scalar_one()

    def claim(self, owner):
        if not isinstance(owner, UUID):
            raise ValueError("typed owner required")
        with self.transaction() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(894217346)"))
            connection.execute(
                text(
                    "UPDATE backtest.experiment_batches SET status='FAILED',"
                    "error_code='ATTEMPTS_EXHAUSTED',owner=NULL,lease_until=NULL,"
                    "updated_at=clock_timestamp() "
                    "WHERE status='RUNNING' AND lease_until<=clock_timestamp() AND attempts=3"
                )
            )
            first = connection.execute(text("SELECT * FROM backtest.oldest_job()"))
            first = first.mappings().one_or_none()
            if first is None or first["kind"] != "quant":
                return None
            # The shared DB trigger enforces the global lease even against old binaries.
            busy = connection.execute(
                text(
                    "SELECT EXISTS(SELECT FROM backtest.runs WHERE "
                    "quant_batch_id IS NULL AND status='RUNNING' AND "
                    "lease_until>clock_timestamp()) OR "
                    "EXISTS(SELECT FROM backtest.readiness_jobs WHERE status='RUNNING' "
                    "AND lease_until>clock_timestamp()) OR EXISTS(SELECT FROM "
                    "backtest.retrospective_assessments WHERE status='RUNNING' AND "
                    "lease_until>clock_timestamp()) OR EXISTS(SELECT FROM "
                    "backtest.experiment_batches "
                    "WHERE status='RUNNING' AND lease_until>clock_timestamp())"
                )
            ).scalar_one()
            if busy:
                return None
            row = (
                connection.execute(
                    text(
                        "UPDATE backtest.experiment_batches SET status='RUNNING',"
                        "owner=:owner,fence=fence+1,attempts=attempts+1,started_at=clock_timestamp(),"
                        "lease_until=clock_timestamp()+interval '300 "
                        "seconds',updated_at=clock_timestamp() "
                        "WHERE batch_id=:id RETURNING *"
                    ),
                    {"owner": owner, "id": first["id"]},
                )
                .mappings()
                .one()
            )
            return ExperimentLease(row["batch_id"], owner, row["fence"], dict(row))

    @staticmethod
    def _insert(connection, table, lease, body, *, canonical_preimage=None, **bindings):
        # Table/column names are exclusively private static callers, never user values.
        identity = body if canonical_preimage is None else canonical_preimage
        payload = dict(
            batch_id=lease.batch_id,
            fingerprint=content_fingerprint(identity),
            body=encoded(body),
            canonical_text=encoded(identity),
            **bindings,
        )
        columns = ",".join(payload)
        values = ",".join("CAST(:body AS jsonb)" if key == "body" else ":" + key for key in payload)
        previous_keys = [
            key
            for key in bindings
            if key
            not in (
                "seed_ids",
                "start_at",
                "end_at",
                "fold_index",
                "phase",
                "run_id",
                "reliability_report_id",
                "selected_candidate_id",
                "status",
            )
        ]
        where = " AND ".join(["batch_id=:batch_id", *(key + "=:" + key for key in previous_keys)])
        previous = connection.execute(
            text(f"SELECT fingerprint FROM backtest.{table} WHERE {where}"), payload
        ).scalar_one_or_none()
        if previous is not None:
            if previous != payload["fingerprint"]:
                raise BacktestError(409, "SNAPSHOT_CONFLICT")
            return previous
        connection.execute(
            text(f"INSERT INTO backtest.{table}({columns}) VALUES({values})"), payload
        )
        return payload["fingerprint"]

    def bind_snapshot(self, lease, snapshot, revisions, phases, *, manifest):
        """All snapshot metadata, source references and phases commit together."""
        from packages.backtesting.experiment import canonical_manifest_preimage
        from packages.backtesting.retrospective_storage import _IDENTITY, _SOURCES

        with self.transaction(lease) as connection:
            self._insert(
                connection,
                "experiment_snapshots",
                lease,
                snapshot,
                canonical_preimage=canonical_manifest_preimage(manifest),
            )
            for kind, identifier in sorted(
                set(revisions), key=lambda item: (item[0], str(item[1]))
            ):
                sources = {
                    **_SOURCES,
                    "COVERAGE": (
                        "backtest.corporate_action_coverage",
                        "coverage_id",
                        "fingerprint",
                        "coverage_id",
                    ),
                }
                table, id_key, hash_key, fk = sources[kind]
                row = connection.execute(
                    text(f"SELECT to_jsonb(x) FROM {table} x WHERE {id_key}=:id"),
                    {"id": identifier},
                ).scalar_one()
                identity_keys = _IDENTITY.get(
                    kind,
                    (
                        "coverage_id",
                        "instrument_id",
                        "start_at",
                        "end_at",
                        "certified_at",
                        "source_reference",
                        "no_actions",
                    ),
                )
                identity = {key: row[key] for key in identity_keys}
                fingerprint = (
                    row[hash_key]
                    if hash_key
                    else connection.execute(
                        text(
                            f"SELECT encode(sha256(convert_to(to_jsonb(x)::text,'UTF8')),'hex') "
                            f"FROM {table} x WHERE {id_key}=:id"
                        ),
                        {"id": identifier},
                    ).scalar_one()
                )
                connection.execute(
                    text(
                        f"INSERT INTO backtest.experiment_revision_bindings"
                        f"(batch_id,kind,reference_id,expected_fingerprint,expected_identity,{fk}) "
                        "VALUES(:batch,:kind,:id,:hash,CAST(:identity AS jsonb),:id) ON "
                        "CONFLICT DO NOTHING"
                    ),
                    dict(
                        batch=lease.batch_id,
                        kind=kind,
                        id=identifier,
                        hash=fingerprint,
                        identity=encoded(identity),
                    ),
                )
            for phase in phases:
                self._insert(
                    connection,
                    "experiment_phase_bindings",
                    lease,
                    phase["body"],
                    phase_key=phase["phaseKey"],
                    phase=phase["phase"],
                    fold_index=phase["fold"],
                    start_at=phase["start"],
                    end_at=phase["end"],
                    seed_ids=phase["seedRevisionIds"],
                )

    def freeze_selections(self, lease, selections):
        """One transaction freezes ALL fold decisions and the final decision."""
        with self.transaction(lease) as connection:
            expected = connection.execute(
                text(
                    "SELECT jsonb_array_length(d.body->'protocol'->'folds')+1 "
                    "FROM backtest.experiment_batches b JOIN "
                    "backtest.experiment_definitions d USING(definition_id) "
                    "WHERE batch_id=:id"
                ),
                {"id": lease.batch_id},
            ).scalar_one()
            scopes = {
                "final" if row["scope"] == "FINAL" else str(row["fold"]) for row in selections
            }
            if len(selections) != expected or scopes != {
                "final",
                *(str(i) for i in range(expected - 1)),
            }:
                raise ValueError("all fold and final selections required atomically")
            for selection in selections:
                selection = dict(selection)
                scope = "final" if selection["scope"] == "FINAL" else str(selection["fold"])
                inputs = tuple(
                    connection.execute(
                        text(
                            "SELECT l.fingerprint FROM "
                            "backtest.experiment_result_links l JOIN "
                            "backtest.experiment_phase_bindings p "
                            "USING(batch_id,phase_key) WHERE l.batch_id=:id AND l.role='SEARCH' "
                            "AND p.phase='validation' AND (:scope='final' OR "
                            "p.fold_index::text=:scope) "
                            'ORDER BY l.fingerprint COLLATE "C"'
                        ),
                        {"id": lease.batch_id, "scope": scope},
                    ).scalars()
                )
                expected_input_hash = content_fingerprint(inputs)
                if selection["validationInputHash"] != expected_input_hash:
                    raise BacktestError(409, "SNAPSHOT_CONFLICT")
                policy_binding = json.loads(
                    connection.execute(
                        text(
                            "SELECT d.policy_canonical "
                            "FROM backtest.experiment_batches b JOIN "
                            "backtest.experiment_definitions d "
                            "USING(definition_id) WHERE batch_id=:id"
                        ),
                        {"id": lease.batch_id},
                    ).scalar_one()
                )
                payload = {
                    key: value
                    for key, value in selection.items()
                    if key not in {"selectionHash", "frozenAt"}
                }
                preimage = (
                    "quant-selection-v1",
                    policy_binding,
                    "final" if scope == "final" else "fold:" + scope,
                    inputs,
                    payload,
                )
                selection_hash = content_fingerprint(preimage)
                if selection.get("selectionHash", selection_hash) != selection_hash:
                    raise BacktestError(409, "SNAPSHOT_CONFLICT")
                selection["selectionHash"] = selection_hash
                self._insert(
                    connection,
                    "experiment_selections",
                    lease,
                    selection,
                    canonical_preimage=preimage,
                    scope=scope,
                    selected_candidate_id=selection["selectedCandidateId"],
                    status=selection["status"],
                )

    def record_result(
        self,
        lease,
        body,
        *,
        candidate_id,
        role,
        phase_key,
        status,
        run_id=None,
        reliability_report_id=None,
    ):
        if status == "COMPLETE":
            raise ValueError("complete phases require atomic child publication")
        with self.transaction(lease) as connection:
            return self._insert(
                connection,
                "experiment_result_links",
                lease,
                body,
                canonical_preimage=result_identity(
                    body, candidate_id=candidate_id, role=role, phase_key=phase_key, status=status
                ),
                candidate_id=candidate_id,
                role=role,
                phase_key=phase_key,
                status=status,
                run_id=run_id,
                reliability_report_id=reliability_report_id,
            )

    def publish_child(
        self,
        lease,
        request,
        manifest,
        result,
        *,
        link,
        reliability,
        findings=(),
        revision_references=(),
        identifier=None,
    ):
        """Existing run/manifest/result ports under the parent's fence, never enqueue."""
        identifier = identifier or uuid5(lease.batch_id, encoded(request))
        with self.transaction(lease) as connection:
            previous = (
                connection.execute(
                    text(
                        "SELECT request,status FROM backtest.runs WHERE run_id=:id "
                        "AND quant_batch_id=:batch FOR UPDATE"
                    ),
                    {"id": identifier, "batch": lease.batch_id},
                )
                .mappings()
                .one_or_none()
            )
            if previous is not None and previous["request"] != request:
                raise BacktestError(409, "SNAPSHOT_CONFLICT")
            if previous is None:
                connection.execute(
                    text(
                        "INSERT INTO backtest.runs(run_id,actor,idempotency_key,request,"
                        "request_hash,snapshot_version,snapshot_at,code_version,trace_id,"
                        "quant_batch_id) "
                        "SELECT :id,actor,:key,CAST(:body AS "
                        "jsonb),:hash,snapshot_version,snapshot_at,"
                        "code_version,trace_id,batch_id FROM backtest.experiment_batches WHERE "
                        "batch_id=:batch"
                    ),
                    dict(
                        id=identifier,
                        key="quant-child-" + str(identifier),
                        body=encoded(request),
                        hash=content_fingerprint(request),
                        batch=lease.batch_id,
                    ),
                )
            published = self._publish_reliability(
                connection, lease, identifier, request, reliability, findings, revision_references
            )
            result = dict(result, reliability=published["reliability"])
            result.setdefault("selectionValues", None)
            result["dataComplete"] = link["dataComplete"]
            result["requiredMetricsValid"] = link["requiredMetricsValid"]
            for table, body in (("manifests", manifest), ("results", result)):
                saved = (
                    connection.execute(
                        text(f"SELECT fingerprint,body FROM backtest.{table} WHERE run_id=:id"),
                        {"id": identifier},
                    )
                    .mappings()
                    .one_or_none()
                )
                if saved is not None:
                    if saved["fingerprint"] != content_fingerprint(body) or saved["body"] != body:
                        raise BacktestError(409, "SNAPSHOT_CONFLICT")
                    continue
                connection.execute(
                    text(
                        f"INSERT INTO backtest.{table}(run_id,fingerprint,body) "
                        "VALUES(:id,:hash,CAST(:body AS jsonb))"
                    ),
                    dict(id=identifier, hash=content_fingerprint(body), body=encoded(body)),
                )
            if previous is None or previous["status"] == "ACCEPTED":
                connection.execute(
                    text(
                        "UPDATE backtest.runs SET status='SUCCEEDED',updated_at=clock_timestamp() "
                        "WHERE run_id=:id"
                    ),
                    {"id": identifier},
                )
            result_hash = content_fingerprint(result)
            binding = dict(
                candidate_id=request["candidateId"],
                role=request["role"],
                phase_key=request["phaseKey"],
                status="COMPLETE",
            )
            link = dict(
                link,
                definitionHash=request["definitionHash"],
                strategySelectionPolicyHash=request["strategySelectionPolicyHash"],
                resultHash=result_hash,
                selectionValues=result["selectionValues"],
                reliability=published["reliability"],
                reliabilityStatus=published["reliability"]["evidenceStatus"],
            )
            if request["role"] == "SEARCH" and request["phaseKey"].endswith(":validation"):
                link["selectionEvidenceHash"] = connection.execute(
                    text("SELECT backtest.quant_selection_evidence(:batch,:phase)"),
                    {"batch": lease.batch_id, "phase": request["phaseKey"]},
                ).scalar_one()
            link_hash = self._insert(
                connection,
                "experiment_result_links",
                lease,
                link,
                canonical_preimage=result_identity(link, **binding),
                **binding,
                run_id=identifier,
                reliability_report_id=published["report_id"],
            )
        return dict(published, run_id=identifier, result_hash=result_hash, link_hash=link_hash)

    def _publish_reliability(
        self, connection, lease, run_id, request, report, findings, references
    ):
        from packages.backtesting.retrospective_storage import _report_validator
        from packages.domain.models import utc_string

        context = (
            connection.execute(
                text(
                    "SELECT b.*,d.body AS definition,d.canonical_text AS canonical_definition,"
                    "d.policy_hash,p.phase,p.fold_index,s.fingerprint AS "
                    "manifest_hash,s.canonical_text AS canonical_manifest "
                    "FROM backtest.experiment_batches b JOIN "
                    "backtest.experiment_definitions d USING(definition_id) "
                    "JOIN backtest.experiment_phase_bindings p USING(batch_id) JOIN "
                    "backtest.experiment_snapshots s "
                    "USING(batch_id) WHERE batch_id=:id AND p.phase_key=:phase"
                ),
                {"id": lease.batch_id, "phase": request["phaseKey"]},
            )
            .mappings()
            .one()
        )
        definition = context["definition"]
        report_id = uuid5(run_id, "quant-reliability-report-v1")
        body = dict(
            schemaVersion="quant-lab-v1",
            version="quant-reliability-report-v1",
            reportId=str(report_id),
            batchId=str(lease.batch_id),
            runId=str(run_id),
            candidateId=request["candidateId"],
            instrumentId=definition["instrumentId"],
            role=PUBLIC_ROLES[request["role"]],
            phase=context["phase"],
            fold=context["fold_index"],
            phaseRole=dict(
                training="IN_SAMPLE_ANALYSIS",
                validation="PARAMETER_SELECTION",
                test="WALK_FORWARD_OOS",
                holdout="FINAL_HOLDOUT",
            )[context["phase"]],
            researchMode=definition["researchMode"],
            dataSelectionPolicyVersion=definition["dataSelectionPolicyVersion"],
            snapshotReference=dict(
                snapshotVersion=context["snapshot_version"],
                snapshotAt=utc_string(context["snapshot_at"]),
            ),
            protocolHash=content_fingerprint(
                (
                    "quant-research-protocol-v1",
                    "NOT_TRAINABLE",
                    json.loads(context["canonical_definition"])[1]["protocol"],
                )
            ),
            strategySelectionPolicyHash=context["policy_hash"],
            methods=dict(
                manifestHash=context["manifest_hash"],
                protocol=definition["protocol"],
                experimentVersion=VERSION,
                simulatorVersion=json.loads(context["canonical_manifest"])[1]["simulator_version"],
                metricsVersion="quant-metrics-v1",
                codeVersion=context["code_version"],
                qualityPolicyVersion="quant-reliability-v1",
            ),
            findingsCount=len(findings),
            revisionReferenceCount=len(references),
        )
        body.update(report)
        semantic_hash = content_fingerprint(reliability_identity(body, findings, references))
        if body.get("reportHash", semantic_hash) != semantic_hash:
            raise BacktestError(409, "SNAPSHOT_CONFLICT")
        body["reportHash"] = semantic_hash
        try:
            _validator("QuantLabReliabilityReport").validate(body)
            for finding in findings:
                _report_validator("BacktestReliabilityFinding").validate(finding)
            for reference in references:
                _report_validator("BacktestReliabilityReference").validate(reference)
        except ValidationError:
            raise BacktestError(409, "SNAPSHOT_CONFLICT") from None
        del body["reportHash"]
        envelope = dict(report=body, findings=findings, revisionReferences=references)
        previous = (
            connection.execute(
                text(
                    "SELECT fingerprint,quant_report_hash FROM backtest.retrospective_reports "
                    "WHERE report_id=:id AND quant_batch_id=:batch"
                ),
                {"id": report_id, "batch": lease.batch_id},
            )
            .mappings()
            .one_or_none()
        )
        if previous is not None and (
            previous["fingerprint"] != content_fingerprint(envelope)
            or previous["quant_report_hash"] != semantic_hash
        ):
            raise BacktestError(409, "SNAPSHOT_CONFLICT")
        if previous is None:
            connection.execute(
                text(
                    "INSERT INTO backtest.retrospective_reports(report_id,run_id,phase,fingerprint,"
                    "body,findings,revision_references,canonical_text,quant_batch_id,"
                    "quant_phase_key,quant_report_hash) "
                    "VALUES(:id,:run,'FINAL',:hash,CAST(:body AS jsonb),CAST(:findings AS "
                    "jsonb),CAST(:refs AS jsonb),"
                    ":canonical,:batch,:phase,:report_hash)"
                ),
                dict(
                    id=report_id,
                    run=run_id,
                    hash=content_fingerprint(envelope),
                    body=encoded(body),
                    findings=encoded(findings),
                    refs=encoded(references),
                    canonical=encoded(envelope),
                    batch=lease.batch_id,
                    phase=request["phaseKey"],
                    report_hash=semantic_hash,
                ),
            )
        return dict(
            report_id=report_id,
            reliability=dict(
                runId=str(run_id),
                reportId=str(report_id),
                reportHash=semantic_hash,
                eligibility=body["overallStatus"],
                rating=body["rating"],
                evidenceStatus=body["evidenceStatus"],
            ),
        )

    def reliability_report(self, batch_id, report_id, *, section="FINDINGS", offset=0, limit=200):
        if (
            section not in {"FINDINGS", "REVISION_REFERENCES"}
            or type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 200
        ):
            raise ValueError("bounded typed report page required")
        column = "findings" if section == "FINDINGS" else "revision_references"
        with self.transaction() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT r.body,r.quant_report_hash,coalesce((SELECT "
                        "jsonb_agg(value ORDER BY ord) "
                        f"FROM jsonb_array_elements(r.{column}) WITH ORDINALITY AS item(value,ord) "
                        "WHERE ord>:offset AND ord<=:end),'[]'::jsonb) AS items FROM "
                        "backtest.retrospective_reports r "
                        "JOIN backtest.experiment_result_links l ON "
                        "l.reliability_report_id=r.report_id "
                        "AND l.batch_id=r.quant_batch_id AND l.run_id=r.run_id AND "
                        "l.status='COMPLETE' "
                        "JOIN backtest.runs child ON child.run_id=l.run_id AND "
                        "child.status='SUCCEEDED' "
                        "WHERE r.quant_batch_id=:batch AND r.report_id=:report"
                    ),
                    dict(batch=batch_id, report=report_id, offset=offset, end=offset + limit),
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise BacktestError(404, "NOT_FOUND")
            return dict(
                report=dict(row["body"], reportHash=row["quant_report_hash"]), items=row["items"]
            )

    def checkpoint(self, lease, *, additional_bar_steps=0):
        if type(additional_bar_steps) is not int or additional_bar_steps < 0:
            raise ValueError("nonnegative integer work required")
        with self.transaction(lease) as connection:
            connection.execute(
                text(
                    "UPDATE backtest.experiment_batches SET bar_steps=bar_steps+:steps,"
                    "updated_at=clock_timestamp() WHERE batch_id=:id"
                ),
                {"id": lease.batch_id, "steps": additional_bar_steps},
            )

    def publish(self, lease, report=None, *, status="SUCCEEDED", error_code=None):
        if status not in {"SUCCEEDED", "BLOCKED", "FAILED"} or (status == "SUCCEEDED") != (
            error_code is None
        ):
            raise ValueError("complete success or explicit terminal failure required")
        if status == "SUCCEEDED" and report is None:
            raise ValueError("immutable complete report required")
        with self.transaction(lease) as connection:
            if report is not None:
                self._insert(connection, "experiment_reports", lease, report)
            self.fenced(connection, lease)
            connection.execute(
                text(
                    "UPDATE backtest.experiment_batches SET status=:status,error_code=:error,"
                    "owner=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE batch_id=:id"
                ),
                {"id": lease.batch_id, "status": status, "error": error_code},
            )

    def exposure_history(self, batch_id):
        """Immutable admission predecessor view; no hypothesis/revision/mode filtering."""
        with self.transaction() as connection:
            return [
                dict(
                    row,
                    knowledge_status="RESULT_AVAILABLE" if row["result_available"] else "REQUESTED",
                )
                for row in connection.execute(
                    text(
                        "WITH previous AS (SELECT DISTINCT ON "
                        "(e.source_kind,e.source_id,e.phase_key) e.*,"
                        "bool_or(e.knowledge_status='RESULT_AVAILABLE') OVER "
                        "(PARTITION BY e.source_kind,e.source_id,e.phase_key) AS result_available "
                        "FROM backtest.test_exposures e WHERE e.exposure_id<="
                        "(SELECT registry_watermark FROM backtest.experiment_batches WHERE "
                        "batch_id=:id) "
                        "ORDER BY e.source_kind,e.source_id,e.phase_key,e.exposure_id DESC) "
                        "SELECT old.*,current.research_role AS current_role,"
                        "greatest(old.start_at,current.start_at) AS overlap_start,"
                        "least(old.end_at,current.end_at) AS overlap_end FROM "
                        "backtest.experiment_batches b "
                        "JOIN backtest.test_exposures current ON "
                        "current.source_kind='BATCH' AND current.source_id=b.batch_id "
                        "AND current.status='ACCEPTED' AND current.knowledge_status='REQUESTED' "
                        "JOIN previous old ON old.exposure_id<=b.registry_watermark "
                        "AND (old.instrument_id IS NULL OR "
                        "old.instrument_id=current.instrument_id) AND "
                        "old.feed=current.feed AND old.interval=current.interval "
                        "AND old.adjustment=current.adjustment AND "
                        "(old.start_at<current.end_at AND current.start_at<old.end_at "
                        "OR old.start_at IS NULL) WHERE b.batch_id=:id ORDER BY "
                        "old.exposure_id,current.exposure_id LIMIT 200"
                    ),
                    {"id": batch_id},
                ).mappings()
            ]
