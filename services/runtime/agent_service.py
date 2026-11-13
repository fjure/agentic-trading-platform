"""Composition of the isolated Research service and private Control relay."""

import json
import os
import stat
from contextlib import asynccontextmanager
from hmac import digest
from pathlib import Path
from threading import Event, Thread
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.agent_research.companion.dialogue import Dialogue, ExpertBinding
from packages.agent_research.companion.ideas import IdeaAction, IdeaLifecycle
from packages.agent_research.companion.interpretation import InterpretationBoundary
from packages.agent_research.companion.service import CompanionService, DialogueCommand
from packages.agent_research.companion.store import CompanionConflict, CompanionStore
from packages.agent_research.companion.testability import (
    MAPPING_VERSION,
    HypothesisIdentity,
    ema_crossover_mapping,
)
from packages.agent_research.discovery.schedule import DailySchedule, ScheduleStore, tzdata_version
from packages.agent_research.model_attempts import ModelAttemptStore
from packages.agent_research.processing import ProcessingStore, ProjectionVault
from packages.agent_research.reporting.service import ReportDenied, ResearchReports
from packages.agent_research.reporting.source import QuantReportSource, SourceIntegrityError
from packages.agent_research.reporting.store import ReportStore, ReportStoreError
from packages.agent_research.run_state import RunState
from packages.agent_research.runs import RunConflict, RunKind, RunStore
from packages.agent_research.tools import ToolCall, ToolJournal
from packages.backtesting.experiment import SearchSpace
from packages.backtesting.strategies import CATALOG_VERSION
from packages.research.models import content_fingerprint
from packages.research.registry import definitions
from services.control_api.agent_processing_projection import ProcessingProjection
from services.llm_gateway.content_policy import safe_result_content

from .agent_control import ResearchControl
from .agent_interpretation import OfflineDialogueProfile, OfflineDialogueStep
from .agent_research import ResearchSupervisor, StepResult
from .agent_research_relay import AgentRelayAuthentication, create_agent_research_relay

RELAY_KEY_PATH = Path("/run/secrets/research-relay-key")
COMMAND_KEY_PATH = Path("/run/secrets/research-command-key")
RESEARCH_DATABASE_PATH = Path("/run/secrets/agent-research-database")
_CONTRACT = Path(__file__).resolve().parents[2] / "contracts/schemas/v1/agent-research.schema.json"
_LOCAL_ACTIONS = frozenset(
    {
        IdeaAction.CREATE,
        IdeaAction.REVIEW,
        IdeaAction.REJECT,
        IdeaAction.REVISE,
        IdeaAction.CONFIRM_HYPOTHESIS,
        IdeaAction.VALIDATE_DRAFT,
    }
)


def load_relay_key(path: Path = RELAY_KEY_PATH) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        if path.is_symlink():
            raise OSError
        with os.fdopen(os.open(path, flags), "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size != 32
                or (os.name == "posix" and info.st_mode & 0o077)
            ):
                raise OSError
            value = source.read(33)
        if len(value) != 32:
            raise OSError
        return value
    except OSError:
        raise ValueError("RESEARCH_RELAY_UNAVAILABLE") from None


def _idea_content(document):
    prose = [
        document.get(field)
        for field in (
            "title",
            "description",
            "originalText",
            "hypothesis",
            "assumptions",
            "openQuestions",
            "uncertainties",
        )
    ]
    proposal = document.get("interpretation")
    if proposal:
        prose.extend(proposal.get(field) for field in ("interpretation", "conflicts"))
        approximation = proposal.get("approximation")
        if approximation:
            prose.extend(
                approximation.get(field)
                for field in ("original", "substitute", "difference", "limitations")
            )
    return safe_result_content(prose)


def create_agent_research_service(
    engine,
    *,
    relay_key: bytes,
    command_key: bytes,
    schedule_id="daily-research",
    resolve_authority=None,
    notify_cancel=None,
    discovery=None,
    steps=None,
    extra=None,
    resolve_expert=None,
    offline_profile: OfflineDialogueProfile | None = None,
):
    """Bind only already-authorized Research ports. Default dispatch authority is absent."""
    auth = AgentRelayAuthentication(relay_key)
    runs, schedules = RunStore(engine), ScheduleStore(engine)
    ideas = CompanionStore(engine, command_key=command_key)

    def stored_expert(actor, definition_id, version, fingerprint):
        try:
            identifier = UUID(definition_id)
        except (ValueError, TypeError, AttributeError):
            return None
        with engine.connect() as connection:
            symbol = connection.execute(
                text("""
                SELECT agent_research.expert_definition_symbol(:actor,:id,:version,:hash)
            """),
                {"actor": actor, "id": identifier, "version": version, "hash": fingerprint},
            ).scalar_one()
        if symbol is None or not safe_result_content(symbol):
            return None
        return ExpertBinding(definition_id, version, fingerprint, (("MARKET", symbol),))

    companion = CompanionService(
        ideas,
        IdeaLifecycle(
            _CONTRACT, permits=_idea_content, verifies=lambda action, *_: action in _LOCAL_ACTIONS
        ),
        InterpretationBoundary(_CONTRACT, permits=safe_result_content),
        permits_local_text=safe_result_content,
        resolve_expert=resolve_expert or stored_expert,
    )
    processing = ProcessingProjection(
        ideas,
        ProcessingStore(engine, ProjectionVault()),
        resolve_authority=resolve_authority,
        notify_cancel=notify_cancel,
    )
    attempts = ModelAttemptStore(engine)
    model_step = None
    if offline_profile is not None:
        if resolve_authority is not None:
            raise ValueError("offline profile owns interpretation authority")
        model_step = OfflineDialogueStep(
            engine,
            runs,
            attempts,
            ideas,
            companion,
            processing,
            offline_profile,
        )
        processing.resolve_authority = model_step.authority
        processing.notify_cancel = model_step.gateway.cancel
    report_store = ReportStore(engine)
    report_writer = ResearchReports(
        QuantReportSource(engine), report_store, permits=safe_result_content
    )
    control = ResearchControl(
        engine,
        key=digest(command_key, b"apf-control-commands-v1", "sha256"),
        companion=companion,
        ideas=ideas,
        runs=runs,
        schedules=schedules,
        reports=report_store,
        processing=processing,
        schedule_id=schedule_id,
        extra=extra,
        discovery=discovery,
        enqueue_interpretation=None if model_step is None else model_step.enqueue,
    )
    handlers = dict(steps or {})
    local_step = handlers.get(RunKind.DIALOGUE, lambda *_: StepResult(RunState.WAITING_FOR_INPUT))
    if model_step is not None:
        local_step = model_step.run
    journal = ToolJournal(engine)

    def dialogue_step(claim, request, recovered):
        with engine.connect() as connection:
            preparation = connection.execute(text("""
                SELECT dialogue_id,dialogue_version,idea_id,idea_version,
                       template_id,template_version,template_hash
                FROM agent_research.quant_prepare_intents WHERE run_id=:run
            """), {"run": claim.run_id}).one_or_none()
            result = connection.execute(
                text("""
                SELECT * FROM agent_research.claimed_quant_result(:run,:owner,:fence)
            """),
                {"run": claim.run_id, "owner": claim.owner, "fence": claim.fence},
            ).one_or_none()
        if result is None:
            if preparation is not None:
                if request.checkpoint_ref == "QUANT_VALIDATION_READY":
                    return _complete_quant_preparation(engine, companion, claim, preparation)
                return _quant_preflight(journal, claim, request.input_ref, preparation)
            return local_step(claim, request, recovered)
        if result.status != "SUCCEEDED":
            return StepResult(RunState.BLOCKED, "QUANT_RESULT_UNAVAILABLE")
        try:
            document = report_writer.publish(str(result.batch_id), actor=result.actor, claim=claim)
        except (ReportDenied, ReportStoreError, SourceIntegrityError):
            return StepResult(RunState.BLOCKED, "REPORT_RESULT_UNAVAILABLE")
        return StepResult(RunState.COMPLETED, document["summary"]["reportId"])

    handlers[RunKind.DIALOGUE] = dialogue_step
    if discovery is not None:
        handlers[RunKind.SCHEDULED] = discovery.step

    def tick():
        with engine.begin() as connection:
            connection.execute(text("SELECT agent_research.ready_quant_runs()"))
            connection.execute(text("SELECT agent_research.ready_prepared_quant_runs()"))
        if discovery is not None:
            discovery.tick(schedule_id)

    supervisor = ResearchSupervisor(
        runs,
        handlers,
        attempts=attempts,
        tick_schedules=tick,
    )

    @asynccontextmanager
    async def lifespan(_app):
        stop, owner = Event(), uuid4()

        def worker():
            while not stop.is_set():
                try:
                    worked = supervisor.run_once(owner)
                except Exception:
                    # A failed step retains its lease for the deterministic recovery path.
                    worked = False
                stop.wait(0.1 if worked else 0.5)

        thread = Thread(target=worker, daemon=True, name="agent-research-supervisor")
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=3)

    return create_agent_research_relay(auth, control, lifespan=lifespan)


def _quant_preflight(journal, claim, input_ref, intent):
    if input_ref != intent.dialogue_id:
        return StepResult(RunState.BLOCKED, "QUANT_TEMPLATE_STALE")
    identifier = str(intent.template_id)
    binding = (intent.idea_id, intent.idea_version, intent.dialogue_version,
               identifier, intent.template_version, intent.template_hash)

    def authorize(scope):
        template = scope.execute(text("""
            SELECT agent_research.claimed_quant_template(
                :run,:owner,:fence,:version,:idea_version,:dialogue_version
            )
        """), {
            "run": claim.run_id, "owner": claim.owner, "fence": claim.fence,
            "version": claim.version, "idea_version": intent.idea_version,
            "dialogue_version": intent.dialogue_version,
        }).scalar_one()
        if template is None or (
            template["id"], template["version"], template["hash"],
            template["ideaId"], template["ideaVersion"],
        ) != (identifier, intent.template_version, intent.template_hash,
              intent.idea_id, intent.idea_version):
            raise RunConflict("QUANT_TEMPLATE_STALE")
        if template["dataContext"] is None:
            raise RunConflict("QUANT_DATA_RIGHTS_UNVERIFIED")
        hypothesis, dialogue = scope.execute(text("""
            SELECT i.document->'hypothesis',d.document
            FROM agent_research.companion_idea_versions i
            JOIN agent_research.companion_dialogue_versions d
              ON d.idea_id=i.idea_id AND d.idea_version=i.version
            WHERE i.idea_id=:idea AND i.version=:version
              AND d.dialogue_id=:dialogue AND d.version=:dialogue_version
        """), {
            "idea": intent.idea_id, "version": intent.idea_version,
            "dialogue": intent.dialogue_id, "dialogue_version": intent.dialogue_version,
        }).one()
        if not _matches_quant_hypothesis(hypothesis, dialogue, template["parameters"]):
            raise RunConflict("QUANT_TEMPLATE_MISMATCH")

    try:
        journal.execute(
            claim, ToolCall("quant-template", "QUANT_READ", 1, content_fingerprint(binding)),
            authorize=authorize, operation=lambda _: identifier,
        )
    except RunConflict as error:
        if error.code not in {
            "QUANT_TEMPLATE_STALE", "QUANT_DATA_RIGHTS_UNVERIFIED", "QUANT_TEMPLATE_MISMATCH",
        }:
            raise
        return StepResult(RunState.BLOCKED, error.code)
    return StepResult(RunState.WAITING_FOR_INPUT, "QUANT_PREPARATION_READY")


def _complete_quant_preparation(engine, companion, claim, intent):
    with engine.connect() as connection:
        definition = connection.scalar(text("""
            SELECT agent_research.claimed_prepared_quant(:run,:owner,:fence,:version)
        """), {
            "run": claim.run_id, "owner": claim.owner, "fence": claim.fence,
            "version": claim.version,
        })
    if definition is None:
        return StepResult(RunState.BLOCKED, "QUANT_VALIDATION_EVIDENCE")

    def prepare(scope):
        key = content_fingerprint(("prepared-quant-idea-v1", str(claim.run_id)))
        command = DialogueCommand(
            "local-operator", intent.idea_id, intent.dialogue_id, intent.idea_version,
            intent.dialogue_version, key,
        )
        try:
            companion.revise_idea(
                command, action=IdeaAction.VALIDATE_DRAFT, decision_id=key, scope=scope,
                changes={
                    "experimentDraftIds": [str(definition)],
                    "testability": {
                        "classification": "EXACTLY_TESTABLE", "catalogVersion": CATALOG_VERSION,
                        "mappingVersion": MAPPING_VERSION, "reasons": ["EXACT_MAPPING_VERIFIED"],
                        "options": [], "limitations": [],
                    },
                },
            )
        except CompanionConflict as error:
            if error.code not in {"STALE_IDEA_VERSION", "STALE_DIALOGUE_VERSION"}:
                raise
            raise RunConflict("QUANT_VALIDATION_STALE") from None
        try:
            scope.execute(
                text("SELECT agent_research.prepare_result_approval(:run)"), {"run": claim.run_id},
            )
        except DBAPIError as error:
            diagnostic = getattr(error.orig, "diag", None)
            if getattr(diagnostic, "message_primary", None) != "quant approval conflict":
                raise
            raise RunConflict("QUANT_VALIDATION_STALE") from None

    return StepResult(RunState.WAITING_FOR_APPROVAL, after_transition=prepare)


def _matches_quant_hypothesis(document, dialogue, template):
    try:
        hypothesis = HypothesisIdentity.from_document(document)
        if Dialogue.from_record(dialogue).confirmed != dict(hypothesis.fields()):
            return False
        space, = (SearchSpace.from_mapping(item) for item in template["searchSpaces"])
        if space.candidate_count != 1 or template["symbol"] != hypothesis.market:
            return False
        parameters = tuple((key, values[0]) for key, values in space.parameter_values)
        mapping = ema_crossover_mapping(hypothesis, parameters, template["researchMode"])
        if mapping is None or (space.strategy_id, space.strategy_version) != (
            mapping.strategy_id, mapping.strategy_version,
        ):
            return False
        protocol = template["protocol"]
        start = protocol["folds"][0]["training"]["start"][:10]
        end = protocol["holdout"]["end"][:10]
        return hypothesis.observation_window == f"{start}/{end}"
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def _database_engine():
    from sqlalchemy import URL, create_engine, event

    from .config import secret_fields

    credentials = secret_fields(RESEARCH_DATABASE_PATH, {"username", "password"})
    if credentials["username"] != "atl_agent_research_login":
        raise ValueError("RESEARCH_DATABASE_UNAVAILABLE")
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=credentials["username"],
            password=credentials["password"],
            host="postgres",
            port=5432,
            database="lab_paper_runtime",
        ),
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=0,
        pool_timeout=1,
        connect_args={"connect_timeout": 3},
    )

    @event.listens_for(engine, "connect")
    def assign_role(connection, _record):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET ROLE atl_agent_research")
                cursor.execute("SET TIME ZONE 'UTC'")
                cursor.execute("SET statement_timeout='3s'")
                cursor.execute("SET lock_timeout='1s'")
                cursor.execute("SELECT current_user,current_database(),session_user")
                if cursor.fetchone() != (
                    "atl_agent_research",
                    "lab_paper_runtime",
                    "atl_agent_research_login",
                ):
                    raise ValueError
            connection.commit()
        except Exception:
            connection.rollback()
            raise PermissionError("RESEARCH_DATABASE_UNAVAILABLE") from None

    return engine


def main():
    import uvicorn

    relay_key, command_key = load_relay_key(), load_relay_key(COMMAND_KEY_PATH)
    engine = _database_engine()
    try:
        schedule = DailySchedule.parse(
            {
                "scheduleId": "daily-research",
                "version": 1,
                "state": "DISABLED",
                "timeZone": "UTC",
                "localTime": "07:00",
                "tzdataVersion": tzdata_version("UTC"),
                "triggerPolicyVersion": 1,
                "scope": {
                    "instrumentIds": [str(item.instrument_id) for item, _ in definitions()],
                    "sourceKinds": ["HISTORICAL_REVISION", "PERSISTED_RESEARCH_RESULT"],
                },
                "limits": {
                    "maxInputTokens": 1,
                    "maxOutputTokens": 1,
                    "maxCost": "0",
                    "currency": "USD",
                    "maxModelCalls": 0,
                    "maxIdeas": 0,
                    "maxSourceItems": 50,
                    "maxPendingItems": 25,
                },
            }
        ).to_document()
        configuration = {
            key: value
            for key, value in schedule.items()
            if key not in {"scheduleId", "version", "state"}
        }
        with engine.begin() as connection:
            connection.execute(
                text("""
                SELECT agent_research.initialize_daily_schedule(CAST(:configuration AS jsonb))
            """),
                {"configuration": json.dumps(configuration)},
            )
        app = create_agent_research_service(engine, relay_key=relay_key, command_key=command_key)
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8013,
            access_log=False,
            log_config=None,
            proxy_headers=False,
            server_header=False,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
