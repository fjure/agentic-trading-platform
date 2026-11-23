"""Operator-owned evaluation over the ordinary Research consent and dispatch path."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import text

from packages.agent_research.budget import Tokens, reserve_model
from packages.agent_research.model_attempts import preview_budget_binding
from packages.agent_research.processing import ProcessingBinding, ProcessingStore, ProjectionVault
from packages.agent_research.runs import RunConflict, RunState
from services.llm_gateway.provider_http import provider_request_bytes
from services.llm_gateway.provider_wire import ResultState

from .agent_interpretation import OfflineDialogueStep, ProviderDialogueProfile
from .agent_research import StepResult

ENCODING = "EVALUATION_CASE_V1"


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    status: str
    confirmed_cost: Decimal | None = None


def case_excerpt(case):
    """The registered prompt receives the complete case, including expert constraints."""
    return json.dumps(
        {"format": ENCODING, "input": case.text, "dialogueState": case.dialogue_state},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class _PrivateProcessing:
    """Keep case projections process-local and retain only opaque consent metadata."""

    def __init__(self, engine, profile, approval):
        self.processing = ProcessingStore(engine, ProjectionVault())
        self.profile, self.approval = profile, approval
        self.bindings = {}

    def check_dispatch(self, scope, binding):
        if (
            self.bindings.get(binding.run_id) != binding
            or self.approval.expires_at <= datetime.now(UTC)
            or self.profile.current(scope, binding) is not True
        ):
            raise RunConflict("PROCESSING_GRANT_INVALID")
        if isinstance(self.profile, ProviderDialogueProfile):
            current_budget = preview_budget_binding(
                scope, self.profile.agent, self.profile.agent.model_binding, binding.dialogue_id,
            )
            if current_budget != binding.budget_binding_sha256:
                raise RunConflict("PROCESSING_GRANT_INVALID")


class EvaluationSupervisor:
    """Use ordinary ledger and admission while retaining case content in a private vault."""

    def __init__(self, ports, store, approval):
        self.ports, self.store, self.approval = ports, store, approval
        ordinary = ports["model_step"]
        if ordinary is None:
            raise ValueError("EVALUATION_PROVIDER_UNAVAILABLE")
        profile = ordinary.profile
        capability = profile.capability
        if (capability.profile_id, capability.model) != (
            approval.binding.profile,
            approval.binding.model,
        ) or reserve_model(
            profile.prices, profile.tokens, profile.context_window
        ).bound.cost > approval.cost_bound:
            raise ValueError("EVALUATION_BINDING_MISMATCH")
        self.projection = _PrivateProcessing(ordinary.engine, profile, approval)
        self.step = OfflineDialogueStep(
            ordinary.engine, ordinary.runs, ordinary.attempts, None, None, self.projection, profile,
        )

    def consume(self, approval, cases):
        return approval == self.approval and self.store.consume(approval, cases) is True

    def execute(self, approval, repetition, case):
        if (
            approval != self.approval
            or self.store.reserve(approval, repetition, case.case_id) is not True
        ):
            raise ValueError("EVALUATION_RESERVATION_DENIED")
        actor = "private-model-evaluation"
        token = str(uuid5(NAMESPACE_URL, f"evaluation:{approval.id}:{repetition}:{case.case_id}"))
        excerpt = case_excerpt(case)
        content = json.dumps(
            {"excerpt": excerpt, "confirmedContext": {}},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()
        profile = self.step.profile
        if (
            approval.expires_at <= datetime.now(UTC)
            or profile.permits(content) is not True
        ):
            raise ValueError("EVALUATION_DISPATCH_BINDING_MISMATCH")
        turn = str(uuid5(NAMESPACE_URL, token + ":turn"))
        runs = self.ports["runs"]
        run_id = self.step.enqueue(actor, token, turn, uuid4().hex)
        claim = runs.claim(uuid4(), run_id=run_id)
        if claim is None or claim.run_id != run_id:
            raise ValueError("EVALUATION_SUPERVISOR_BUSY")
        runs.transition(claim, RunState.WAITING_FOR_PROCESSING_DECISION)
        capability = profile.capability
        evidence = {}
        with self.step.engine.connect() as scope:
            deadline = scope.execute(
                text("SELECT deadline_at FROM agent_research.runs WHERE run_id=:run"),
                {"run": run_id},
            ).scalar_one()
            if isinstance(profile, ProviderDialogueProfile):
                evidence = {
                    "evidence_revision": profile.evidence_revision,
                    "tariff_revision": profile.tariff_revision,
                    "provider_request_sha256": sha256(
                        provider_request_bytes(capability, content)
                    ).hexdigest(),
                    "budget_binding_sha256": preview_budget_binding(
                        scope, profile.agent, profile.agent.model_binding, token,
                    ),
                }
        binding = ProcessingBinding(
            run_id, actor, token, 1, turn, 1,
            capability.provider.value, capability.profile_id, capability.profile_generation,
            capability.model, capability.model_generation, capability.credential_generation,
            profile.policy_generation, capability.envelope_version,
            ("OPERATOR_RESEARCH_EXCERPT",), "evaluation-observation", **evidence,
        )
        processing = self.projection.processing
        self.projection.bindings[run_id] = binding
        session = sha256((token + ":session").encode()).hexdigest()
        preview = processing.vault.issue(
            binding, session=session, content=content,
            deadline=min(deadline, approval.expires_at),
        )
        receipt = processing.confirm(
            preview.preview_id, preview.commitment, binding,
            session=session, key=token + ":consent",
            check_current=self.projection.check_dispatch,
        )
        if self.store.authorize(approval, repetition, case.case_id) is not True:
            processing.vault.discard(preview.preview_id)
            raise ValueError("EVALUATION_RESERVATION_DENIED")
        claim = runs.claim(uuid4(), run_id=run_id)
        if claim is None or claim.run_id != run_id:
            processing.vault.discard(preview.preview_id)
            raise ValueError("EVALUATION_SUPERVISOR_BUSY")
        prepared = self.step.dispatch_authorized(claim, runs.load_input(claim))
        if isinstance(prepared, StepResult):
            runs.transition(claim, prepared.target, checkpoint_ref=prepared.checkpoint_ref)
            self.store.settle(approval, repetition, case.case_id, "provider_blocked", None, None)
            return EvaluationOutcome("provider_blocked")
        binding, receipt_id, dispatch, result = prepared
        assert receipt_id == receipt.receipt_id
        status = {
            ResultState.ACCEPTED: "answered",
            ResultState.REFUSED: "refused",
            ResultState.OUTCOME_UNKNOWN: "timeout",
        }.get(result.state, "provider_blocked")
        output = None
        if result.state is ResultState.ACCEPTED and result.usage is not None:
            result_ref = "evaluation:" + token
            processing.accept_result(
                claim,
                receipt_id,
                dispatch.attempt_id,
                result_ref,
                binding=binding,
                check_policy=self.projection.check_dispatch,
                persist_result=lambda _: None,
                reported_tokens=Tokens(result.usage.input_tokens, result.usage.output_tokens),
            )
            with self.step.engine.connect() as connection:
                settled = connection.execute(
                    text("""
                    SELECT status,confirmed_cost FROM agent_research.model_attempts
                    WHERE attempt_id=:attempt AND run_id=:run
                """),
                    {"attempt": dispatch.attempt_id, "run": claim.run_id},
                ).one()
            if settled.status == "COMPLETED":
                output = result.output
                runs.transition(claim, RunState.COMPLETED, checkpoint_ref=result_ref)
            else:
                status = "provider_blocked"
                runs.transition(claim, RunState.WAITING_FOR_INPUT)
        else:
            if result.state is ResultState.ACCEPTED:
                status = "timeout"
            self.step._settle_failure(claim, dispatch, result)
            runs.transition(claim, RunState.WAITING_FOR_INPUT)
        # Token usage alone is not an invoice. The private store retains its
        # conservative bound until independently confirmed monetary evidence exists.
        cost = None
        self.projection.bindings.pop(run_id, None)
        self.store.settle(approval, repetition, case.case_id, status, cost, output)
        return EvaluationOutcome(status, cost)


class InstalledEvaluationSupervisor(EvaluationSupervisor):
    def __init__(self, ports, store, approval):
        super().__init__(ports, store, approval)
        self.server = self.thread = self.engine = self.client = None

    def close(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)
        self.client.close()
        self.engine.dispose()
        if self.thread.is_alive():
            raise RuntimeError("EVALUATION_SUPERVISOR_UNAVAILABLE")


def evaluation_supervisor(store, approval, *, files):
    """Replace the ordinary Supervisor command inside its existing private Compose role."""
    import httpx
    import uvicorn

    from packages.agent_research.provider_authority import ProviderAuthorityStore
    from services.llm_gateway.provider_installation import (
        DISPATCH_KEY_PATH,
        PROFILE_PATH,
        parse_installation,
    )
    from services.llm_gateway.supervisor_rpc import create_admission_app

    from .agent_service import (
        COMMAND_KEY_PATH,
        _database_engine,
        create_agent_research_service,
        load_relay_key,
    )
    from .provider_credential_authority import register_credential_authority
    from .provider_profiles import check_persisted_evidence, load_dialogue_profile

    settings_bytes = files["settings"].read_bytes()
    settings = json.loads(settings_bytes)
    if PROFILE_PATH.is_symlink():
        raise ValueError("EVALUATION_BINDING_MISMATCH")
    descriptor = PROFILE_PATH.read_bytes()
    if (
        set(settings) != {"projectionEncoding", "providerInstallationSha256"}
        or settings["projectionEncoding"] != ENCODING
        or sha256(descriptor).hexdigest() != settings["providerInstallationSha256"]
        or sha256(settings_bytes).hexdigest() != approval.binding.settings_sha256
    ):
        raise ValueError("EVALUATION_BINDING_MISMATCH")
    installation = parse_installation(descriptor)
    if (
        installation is None
        or installation.discovery is not None
        or installation.probe_output_tokens is not None
        or (
            installation.binding.profile_id,
            installation.binding.model_id,
            installation.binding.api_version,
            installation.binding.prompt_sha256,
            installation.binding.schema_sha256,
        )
        != (
            approval.binding.profile,
            approval.binding.model,
            approval.binding.api,
            approval.binding.prompt_sha256,
            approval.binding.schema_sha256,
        )
        or approval.expires_at <= datetime.now(UTC)
    ):
        raise ValueError("EVALUATION_BINDING_MISMATCH")
    engine = _database_engine()
    client = httpx.Client(
        base_url="http://research-llm-gateway:8080",
        timeout=20,
        trust_env=False,
        follow_redirects=False,
    )
    server, thread = None, None
    try:
        authority = ProviderAuthorityStore(engine, check_evidence=check_persisted_evidence)
        profile, auth = load_dialogue_profile(
            engine,
            authority,
            installation,
            load_relay_key(DISPATCH_KEY_PATH),
            client,
        )
        app = create_agent_research_service(
            engine,
            relay_key=load_relay_key(),
            command_key=load_relay_key(COMMAND_KEY_PATH),
            provider_profile=profile,
            admission_auth=auth,
        )
        ports = app.state.research_evaluation
        evaluator = InstalledEvaluationSupervisor(ports, store, approval)
        # Keep Setup's existing signed revocation channel reachable while the
        # exclusive evaluator owns the private admission listener.
        admission = create_admission_app(auth, evaluator.step.admission.consume)
        register_credential_authority(
            admission, authority,
            load_relay_key(Path("/run/secrets/research-credential-authority-key")),
        )
        server = uvicorn.Server(
            uvicorn.Config(
                admission,
                host="0.0.0.0",
                port=8013,
                access_log=False,
                log_config=None,
                proxy_headers=False,
                server_header=False,
            )
        )
        thread = Thread(target=server.run, daemon=True, name="evaluation-admission")
        thread.start()
        deadline = monotonic() + 5
        while not server.started and thread.is_alive() and monotonic() < deadline:
            sleep(0.01)
        if not server.started:
            raise RuntimeError("EVALUATION_SUPERVISOR_UNAVAILABLE")
        evaluator.server, evaluator.thread = server, thread
        evaluator.engine, evaluator.client = engine, client
        return evaluator
    except BaseException:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=5)
        client.close()
        engine.dispose()
        raise
