"""APF-02 PostgreSQL run lifecycle, fencing, role isolation and restore."""

import json
import sys
from pathlib import Path
from uuid import uuid4

WORKER_ROLE = "atl_agent_research"
PROOF = "PASS agent research migration/ACL/idempotency/fence/wait/cancel/tools/consent/budget"
TABLES = tuple(
    "agent_research." + name for name in (
        "runs", "run_events", "tool_receipts", "tool_denials", "model_budget_policies",
        "model_attempts", "model_attempt_events", "model_accounting_receipts", "model_prices",
        "processing_grants", "processing_events", "work_budget_policies", "work_budget_usage",
    )
)


def host():
    from tests.integration import ledger_support
    from tests.integration import test_readiness_storage as harness

    previous_groups, previous_tables = ledger_support.GROUPS, harness.TABLES
    try:
        ledger_support.GROUPS = (*previous_groups, WORKER_ROLE)
        harness.TABLES = (*previous_tables, *TABLES)
        return harness.host("tests.integration.test_agent_research_runtime")
    finally:
        ledger_support.GROUPS = previous_groups
        harness.TABLES = previous_tables


def _financial_privileges(connection):
    from sqlalchemy import text

    return tuple(connection.execute(text("""
        SELECT r.rolname, c.relname, privilege,
               has_table_privilege(r.oid, c.oid, privilege) AS allowed
          FROM pg_roles r CROSS JOIN pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE',
                                  'REFERENCES','TRIGGER']) AS privileges(privilege)
         WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f')
           AND r.rolname IN ('atl_worker','atl_execution','atl_control_api',
                             'atl_watchdog','atl_backtest_worker','atl_agent_research')
         ORDER BY r.rolname, c.relname, privilege
    """)))


def _prepare_consent(worker, store, request):
    from datetime import UTC, datetime, timedelta

    from packages.agent_research.processing import (
        ProcessingBinding,
        ProcessingStore,
        ProjectionVault,
    )
    from packages.agent_research.run_state import RunState

    run_id = store.enqueue(**request)
    claim = store.claim(uuid4())
    assert claim is not None and claim.run_id == run_id
    store.transition(claim, RunState.WAITING_FOR_PROCESSING_DECISION)

    binding = ProcessingBinding(
        run_id=run_id, actor=request["actor"], dialogue_id="dialogue-v1", dialogue_version=1,
        turn_id="turn-v1", turn_version=1, provider="OPENAI", profile_id="offline-profile",
        profile_generation=1, model_id="offline-model", model_generation=1,
        credential_generation=1, policy_generation=1, envelope_version="offline-envelope-v1",
        data_classes=("OPERATOR_RESEARCH_EXCERPT",), step_id="semantic-step-1",
    )
    vault = ProjectionVault()
    preview = vault.issue(
        binding, session="synthetic-session", content=b"synthetic-context-that-must-not-persist",
        deadline=datetime.now(UTC) + timedelta(minutes=2),
    )
    return ProcessingStore(worker, vault), preview, binding


def _expect_conflict(code, operation):
    from packages.agent_research.runs import RunConflict

    try:
        operation()
    except RunConflict as error:
        assert error.code == code
    else:
        raise AssertionError(f"Expected {code}")


def _verify_consent(owner, worker, store, request):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import text

    from packages.agent_research.processing import ProcessingStore, ProjectionVault

    processing, preview, binding = _prepare_consent(
        worker, store, request | {"key": "synthetic-consent-run-key"},
    )
    start = Barrier(2, timeout=10)
    confirmation = {
        "session": "synthetic-session", "key": "synthetic-consent-action-key",
        "check_current": lambda *_: None,
    }

    def confirm():
        start.wait()
        return processing.confirm(preview.preview_id, preview.commitment, binding, **confirmation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = [pool.submit(confirm) for _ in range(2)]
        receipt = first.result()
        assert second.result() == receipt and receipt.state == "AUTHORIZED"

    restarted = ProcessingStore(worker, ProjectionVault())
    replay = restarted.confirm(preview.preview_id, preview.commitment, binding, **confirmation)
    assert replay.receipt_id == receipt.receipt_id and replay.state == "UNAVAILABLE"
    assert replay.revision == receipt.revision + 1 and replay.attempt_id is None
    _expect_conflict(
        "PROCESSING_GRANT_INVALID",
        lambda: restarted.confirm(
            preview.preview_id, preview.commitment, binding,
            **(confirmation | {"key": "synthetic-distinct-action-key"}),
        ),
    )
    with owner.connect() as connection:
        persisted = connection.scalar(text("SELECT row_to_json(g)::text FROM "
                                           "agent_research.processing_grants g"))
        assert "synthetic-context-that-must-not-persist" not in persisted
        assert "synthetic-session" not in persisted
    unavailable = restarted.status(receipt.receipt_id, actor=binding.actor)
    assert unavailable.state == "UNAVAILABLE" and unavailable.attempt_id is None
    revoked = restarted.revoke(
        receipt.receipt_id, actor=binding.actor, expected_revision=unavailable.revision,
    )
    assert revoked.state == "REVOKED" and revoked.attempt_id is None
    assert restarted.status(receipt.receipt_id, actor=binding.actor) == revoked
    claim = store.claim(uuid4())
    assert claim is not None and claim.run_id == binding.run_id
    store.cancel(claim.run_id, claim.version)


def _verify_cost_only_reconciliation(owner, attempts, attempt_id):
    from decimal import Decimal

    from sqlalchemy import text

    cost = Decimal("0.00015")
    for _ in range(2):
        attempts.reconcile_confirmed_cost(attempt_id, "synthetic-billing-proof", cost)
    _expect_conflict(
        "IDEMPOTENCY_CONFLICT",
        lambda: attempts.reconcile_confirmed_cost(attempt_id, "other-billing-proof", cost),
    )
    with owner.connect() as connection:
        result = connection.execute(text("""
            SELECT status,confirmed_cost,held_cost,held_input,held_output,result_ref
              FROM agent_research.model_attempts WHERE attempt_id=:id
        """), {"id": attempt_id}).one()
        assert result.status == "OUTCOME_UNKNOWN" and result.result_ref is None
        assert (result.confirmed_cost, result.held_cost) == (cost, 0)
        assert (result.held_input, result.held_output) == (100, 50)


def _verify_budget_and_dispatch(owner, worker, store, request):
    from dataclasses import replace
    from unittest.mock import patch

    from packages.agent_research import registry

    model = registry.ModelBinding("OPENAI", "offline-profile", 1, "offline-model", 1)
    agent = replace(registry.QUANT_RESEARCH_V1, model_binding=model)
    request = request | {"agent": agent, "key": "synthetic-model-bound-run"}
    with patch.object(registry, "AGENTS", {(agent.agent_id, agent.version): agent}):
        _verify_model_bound_run(owner, worker, store, request, agent, model)


def _verify_model_bound_run(owner, worker, store, request, agent, model):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import asdict
    from decimal import Decimal
    from threading import Barrier

    from sqlalchemy import text

    from packages.agent_research.budget import BudgetDenied, ModelOutcome, TokenPrices, Tokens
    from packages.agent_research.model_attempts import ModelAttemptStore, model_budget_scopes

    processing, preview, binding = _prepare_consent(worker, store, request)
    receipt = processing.confirm(
        preview.preview_id, preview.commitment, binding, session="synthetic-session",
        key="synthetic-model-dispatch", check_current=lambda *_: None,
    )
    claim = store.claim(uuid4())
    assert claim is not None and claim.run_id == binding.run_id
    scopes = model_budget_scopes(
        agent.agent_id, agent.version, request["kind"], model, request["input_ref"],
    )
    with owner.begin() as connection:
        connection.execute(text("""
            INSERT INTO agent_research.model_budget_policies (scope,version,currency,max_cost,
                max_input_tokens,max_output_tokens,max_calls,enabled)
            VALUES (:scope,1,'USD',0.10,4096,1024,1,true)
        """), [{"scope": scope} for scope in scopes])
        connection.execute(text("""
            INSERT INTO agent_research.model_prices (model_binding,version,currency,
                input_price,output_price,bounds_unconfirmed_cost)
            VALUES (CAST(:model AS jsonb),1,'USD',1,2,false)
        """), {"model": json.dumps(asdict(model))})

    attempts = ModelAttemptStore(worker)
    reserve = {
        "step_id": binding.step_id, "request_hash": preview.commitment, "model": model,
        "prices": TokenPrices(1, "USD", Decimal("1"), Decimal("2")),
        "tokens": Tokens(100, 50), "context_window": 1000, "timeout_seconds": 20,
    }
    try:
        attempts.reserve(claim, attempt_id=uuid4(), **(reserve | {
            "prices": TokenPrices(1, "USD", Decimal("0"), Decimal("0")),
        }))
    except BudgetDenied as error:
        assert error.code == "BUDGET_POLICY_UNAVAILABLE"
    else:
        raise AssertionError("Untrusted provider rates were accepted")
    attempt = attempts.reserve(claim, attempt_id=uuid4(), **reserve)
    assert attempt.status == "RESERVED"
    assert attempts.reserve(claim, attempt_id=attempt.attempt_id, **reserve) == attempt
    _expect_conflict(
        "PROCESSING_GRANT_REQUIRED",
        lambda: attempts.authorize(claim, attempt.attempt_id, preview.commitment),
    )

    def dispatch():
        return processing.authorize(
            claim, receipt.receipt_id, attempt.attempt_id, binding, check_policy=lambda *_: None,
        )

    admitted = dispatch()
    assert admitted.content == b"synthetic-context-that-must-not-persist"
    assert admitted.attempt_id == attempt.attempt_id
    _expect_conflict("PROCESSING_GRANT_INVALID", dispatch)
    revoked = processing.revoke(receipt.receipt_id, actor=binding.actor, expected_revision=2)
    assert revoked.state == "REVOKED" and revoked.attempt_id == attempt.attempt_id
    persisted = []
    _expect_conflict(
        "PROCESSING_GRANT_REVOKED",
        lambda: processing.accept_result(
            claim, receipt.receipt_id, attempt.attempt_id, "forbidden-result-ref",
            binding=binding, check_policy=lambda *_: None,
            persist_result=lambda _: persisted.append(True),
        ),
    )
    assert persisted == []
    unknown = attempts.settle(claim, attempt.attempt_id, ModelOutcome.OUTCOME_UNKNOWN)
    assert unknown.status == "OUTCOME_UNKNOWN"
    _expect_conflict(
        "PROVIDER_OUTCOME_UNKNOWN", lambda: attempts.reserve(claim, attempt_id=uuid4(), **reserve),
    )
    try:
        attempts.reserve(claim, attempt_id=uuid4(), **(reserve | {"step_id": "semantic-step-2"}))
    except BudgetDenied as error:
        assert error.code == "BUDGET_EXHAUSTED"
    else:
        raise AssertionError("A new step exceeded the persisted model quota")
    with owner.connect() as connection:
        accounting = connection.execute(text("""
            SELECT held_cost,cost_bound,held_input,held_output,confirmed_cost,result_ref
              FROM agent_research.model_attempts WHERE attempt_id=:id
        """), {"id": attempt.attempt_id}).one()
        assert accounting.held_cost == accounting.cost_bound > 0
        assert (accounting.held_input, accounting.held_output) == (100, 50)
        assert accounting.confirmed_cost is None and accounting.result_ref is None
    store.cancel(claim.run_id, claim.version)
    _verify_cost_only_reconciliation(owner, attempts, attempt.attempt_id)

    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.model_budget_policies SET version=2,max_calls=10
        """))
        connection.execute(text("""
            UPDATE agent_research.model_budget_policies SET max_calls=2
            WHERE scope LIKE 'global:%'
        """))
    second_run = store.enqueue(**(request | {"key": "synthetic-concurrent-budget"}))
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.runs SET created_at=created_at-interval '1 day'
            WHERE run_id=:id
        """), {"id": second_run})
    second_claim = store.claim(uuid4())
    assert second_claim is not None and second_claim.run_id == second_run
    barrier = Barrier(2)

    def compete(step_id):
        barrier.wait(timeout=5)
        try:
            return attempts.reserve(
                second_claim, attempt_id=uuid4(), **(reserve | {"step_id": step_id})
            ).status
        except BudgetDenied as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(compete, ("race-step-1", "race-step-2"))) == [
            "BUDGET_EXHAUSTED", "RESERVED",
        ]
    with owner.connect() as connection:
        assert connection.scalar(text("""
            SELECT period=(r.created_at AT TIME ZONE 'UTC')::date
            FROM agent_research.model_attempts a JOIN agent_research.runs r USING(run_id)
            WHERE run_id=:id
        """), {"id": second_run}) is True
    store.cancel(second_claim.run_id, second_claim.version)

    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.model_budget_policies SET version=3,max_calls=10
        """))
    processing, preview, binding = _prepare_consent(
        worker, store, request | {"key": "synthetic-recovery-result"}
    )
    receipt = processing.confirm(
        preview.preview_id, preview.commitment, binding, session="synthetic-session",
        key="synthetic-recovery-consent", check_current=lambda *_: None,
    )
    claim = store.claim(uuid4())
    assert claim is not None and claim.run_id == binding.run_id
    reserve = reserve | {"step_id": binding.step_id, "request_hash": preview.commitment}
    attempt = attempts.reserve(claim, attempt_id=uuid4(), **reserve)
    processing.authorize(
        claim, receipt.receipt_id, attempt.attempt_id, binding, check_policy=lambda *_: None,
    )
    _expect_conflict(
        "PROCESSING_GRANT_REQUIRED",
        lambda: attempts.settle(
            claim, attempt.attempt_id, ModelOutcome.COMPLETED, result_ref="unapproved-result",
        ),
    )
    accepted = processing.accept_result(
        claim, receipt.receipt_id, attempt.attempt_id, "persisted-result", binding=binding,
        check_policy=lambda *_: None, persist_result=lambda scope: scope.execute(text("SELECT 1")),
        reported_tokens=Tokens(50, 20), confirmed_cost=Decimal("0.00009"),
    )
    assert accepted.state == "ACCEPTED" and accepted.result_ref == "persisted-result"
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.runs SET lease_until=clock_timestamp()-interval '1 second'
            WHERE run_id=:id
        """), {"id": claim.run_id})
    recovered = store.claim(uuid4())
    assert recovered is not None and recovered.run_id == claim.run_id
    assert recovered.fence > claim.fence
    result = attempts.reserve(recovered, attempt_id=attempt.attempt_id, **reserve)
    assert result.status == "COMPLETED" and result.result_ref == "persisted-result"
    assert attempts.recover(recovered) == (result,)
    store.cancel(recovered.run_id, recovered.version)
    _verify_stale_attempts(owner, store, attempts, request, model, reserve)


def _verify_stale_attempts(owner, store, attempts, request, model, reserve):
    from sqlalchemy import text

    from packages.agent_research.model_attempts import model_budget_scopes
    from packages.agent_research.runs import RunKind

    request = request | {"key": "synthetic-stale-attempts", "kind": RunKind.REEVALUATION}
    agent = request["agent"]
    scopes = model_budget_scopes(
        agent.agent_id, agent.version, request["kind"], model, request["input_ref"],
    )
    with owner.begin() as connection:
        connection.execute(text("""
            INSERT INTO agent_research.model_budget_policies (scope,version,currency,max_cost,
                max_input_tokens,max_output_tokens,max_calls,enabled)
            VALUES (:scope,3,'USD',0.10,4096,1024,10,true)
            ON CONFLICT (scope) DO NOTHING
        """), [{"scope": scope} for scope in scopes])

    run_id = store.enqueue(**request)
    claim = store.claim(uuid4())
    assert claim is not None and claim.run_id == run_id
    pending = attempts.reserve(
        claim, attempt_id=uuid4(), **(reserve | {"step_id": "stale-before-dispatch"}),
    )
    dispatch = reserve | {"step_id": "stale-after-dispatch"}
    dispatched = attempts.reserve(claim, attempt_id=uuid4(), **dispatch)
    assert attempts.authorize(claim, dispatched.attempt_id, dispatch["request_hash"]).status == (
        "DISPATCH_AUTHORIZED"
    )
    _expect_conflict(
        "PROVIDER_OUTCOME_UNKNOWN",
        lambda: attempts.authorize(claim, pending.attempt_id, reserve["request_hash"]),
    )
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.runs SET lease_until=clock_timestamp()-interval '1 second'
            WHERE run_id=:id
        """), {"id": run_id})
    recovered = store.claim(uuid4())
    assert recovered is not None and recovered.run_id == run_id and recovered.fence > claim.fence
    statuses = {attempt.attempt_id: attempt.status for attempt in attempts.recover(recovered)}
    assert statuses == {pending.attempt_id: "UNSENT", dispatched.attempt_id: "OUTCOME_UNKNOWN"}
    repeated = attempts.recover(recovered)
    assert len(repeated) == 1 and repeated[0].attempt_id == dispatched.attempt_id
    assert repeated[0].status == "OUTCOME_UNKNOWN"
    _expect_conflict(
        "PROVIDER_OUTCOME_UNKNOWN",
        lambda: attempts.reserve(recovered, attempt_id=uuid4(), **dispatch),
    )
    with owner.connect() as connection:
        rows = connection.execute(text("""
            SELECT a.attempt_id,a.held_cost,a.held_input,a.held_output,e.status
            FROM agent_research.model_attempts a
            JOIN agent_research.model_attempt_events e ON e.attempt_id=a.attempt_id
            WHERE a.run_id=:id AND e.status=a.status
        """), {"id": run_id})
        by_id = {row.attempt_id: row for row in rows}
        unsent = by_id[pending.attempt_id]
        assert (unsent.status, unsent.held_cost, unsent.held_input, unsent.held_output) == (
            "UNSENT", 0, 0, 0,
        )
        unknown = by_id[dispatched.attempt_id]
        assert unknown.status == "OUTCOME_UNKNOWN" and unknown.held_cost > 0
        assert (unknown.held_input, unknown.held_output) == (100, 50)
    store.cancel(recovered.run_id, recovered.version)


def _verify_work_hierarchy(owner, worker, store, request):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Barrier

    from sqlalchemy import text

    from packages.agent_research.budget import BudgetDenied
    from packages.agent_research.run_state import RunState
    from packages.agent_research.tools import ToolCall, ToolJournal

    scope = "agent:" + request["agent"].agent_id

    def usage(connection):
        return dict(connection.execute(text("""
            SELECT scope,tool_calls FROM agent_research.work_budget_usage
            WHERE scope IN ('global',:agent)
              AND period=(clock_timestamp() AT TIME ZONE 'UTC')::date
        """), {"agent": scope}).tuples().all())

    def cap(connection, scope_key, maximum):
        connection.execute(text("""
            UPDATE agent_research.work_budget_policies
            SET max_tool_calls=:maximum,version=version+1 WHERE scope=:scope
        """), {"maximum": maximum, "scope": scope_key})

    with owner.begin() as connection:
        baseline = usage(connection)
        cap(connection, "global", baseline["global"] + 1)

    journal = ToolJournal(worker)
    call = ToolCall("hierarchy-step", "QUANT_READ", 1, "b" * 64)
    executed = []

    def operation(_):
        executed.append(True)
        return "hierarchy-result"

    def invoke(claim):
        return journal.execute(claim, call, authorize=lambda _: None, operation=operation)

    first_run = store.enqueue(**(request | {"key": "synthetic-hierarchy-first"}))
    first = store.claim(uuid4())
    assert first is not None and first.run_id == first_run
    ready = Barrier(2)

    def compete(step_id):
        ready.wait(timeout=5)
        try:
            return journal.execute(
                first, replace(call, step_id=step_id),
                authorize=lambda _: None, operation=operation,
            )
        except BudgetDenied as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(compete, ("tool-race-1", "tool-race-2"))) == [
            "BUDGET_EXHAUSTED", "hierarchy-result",
        ]
    store.cancel(first.run_id, first.version)

    second_run = store.enqueue(**(request | {"key": "synthetic-hierarchy-second"}))
    second = store.claim(uuid4())
    assert second is not None and second.run_id == second_run
    for exhausted_scope in ("global", scope):
        try:
            invoke(second)
        except BudgetDenied as error:
            assert error.code == "BUDGET_EXHAUSTED"
        else:
            raise AssertionError(f"New run bypassed shared {exhausted_scope} budget")
        if exhausted_scope == "global":
            with owner.begin() as connection:
                cap(connection, "global", baseline["global"] + 10)
                cap(connection, scope, baseline[scope] + 1)
    assert executed == [True]
    with owner.connect() as connection:
        assert usage(connection) == {key: count + 1 for key, count in baseline.items()}
    store.cancel(second.run_id, second.version)
    with owner.begin() as connection:
        connection.execute(text("""
            UPDATE agent_research.work_budget_policies p SET max_steps=u.steps,version=p.version+1
            FROM agent_research.work_budget_usage u
            WHERE p.scope='global' AND u.scope=p.scope
              AND u.period=(clock_timestamp() AT TIME ZONE 'UTC')::date
        """))
    third = store.enqueue(**(request | {"key": "synthetic-hierarchy-third"}))
    assert store.claim(uuid4()) is None
    summary = store.snapshot(third, actor=request["actor"])
    assert summary is not None and summary.state is RunState.BUDGET_EXHAUSTED
    assert summary.steps == 0


def guest():
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Barrier

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.engine import URL
    from sqlalchemy.exc import DBAPIError

    from packages.agent_research.registry import QUANT_RESEARCH_V1, AgentPolicyDenied
    from packages.agent_research.run_state import RunState
    from packages.agent_research.runs import RunConflict, RunKind, RunStore
    from packages.agent_research.tools import ToolCall, ToolJournal

    payload = json.load(sys.stdin)
    url = URL.create(
        "postgresql+psycopg",
        username=payload["role"], password=payload["password"], host="postgres", port=5432,
        database=payload["database"], query={"connect_timeout": "5"},
    )
    owner = create_engine(url, hide_parameters=True)

    def engine_for(role):
        engine = create_engine(url, hide_parameters=True)

        @event.listens_for(engine, "begin")
        def set_role(connection):
            connection.exec_driver_sql(f"SET LOCAL ROLE {role}")

        return engine

    worker = engine_for(WORKER_ROLE)
    try:
        config = Config("alembic.ini")
        with owner.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0036_quant_validation_attempts")
            before = _financial_privileges(connection)
            command.upgrade(config, "0040_agent_research_work_budget")
            assert _financial_privileges(connection) == before
            assert any(row.rolname == WORKER_ROLE and row.relname == "jobs" for row in before)
            assert not any(row.allowed for row in before if row.rolname == WORKER_ROLE)
            trading_job_count = connection.scalar(text("SELECT count(*) FROM public.jobs"))

        try:
            with worker.connect() as connection:
                connection.execute(text("SELECT 1 FROM public.jobs LIMIT 1"))
        except DBAPIError as error:
            assert error.orig.sqlstate == "42501"
        else:
            raise AssertionError("Research role can read the trading queue")

        store = RunStore(worker)
        first_worker, second_worker = uuid4(), uuid4()
        request = {
            "actor": "local-operator", "key": "synthetic-agent-run-key", "kind": RunKind.DIALOGUE,
            "input_ref": str(uuid4()), "trace_id": "a" * 32, "agent": QUANT_RESEARCH_V1,
        }
        start = Barrier(2, timeout=10)

        def enqueue():
            start.wait()
            return store.enqueue(**request)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = [pool.submit(enqueue) for _ in range(2)]
            run_id = first.result()
            assert second.result() == run_id

        try:
            store.enqueue(**(request | {"input_ref": str(uuid4())}))
        except RunConflict as error:
            assert error.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("Conflicting idempotency key accepted")

        with owner.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM agent_research.runs")) == 1
            assert connection.scalar(text("SELECT count(*) FROM public.jobs")) == trading_job_count

        for statement in (
            "UPDATE agent_research.runs SET input_ref = 'changed-input'",
            "DELETE FROM agent_research.run_events",
        ):
            try:
                with worker.begin() as connection:
                    connection.execute(text(statement))
            except DBAPIError as error:
                assert error.orig.sqlstate == "42501"
            else:
                raise AssertionError("Research role can rewrite an immutable binding or audit")

        lease = store.claim(first_worker)
        assert lease is not None and lease.run_id == run_id and lease.owner == first_worker
        assert store.claim(second_worker) is None
        store.renew(lease)

        journal = ToolJournal(worker)
        tool_call = ToolCall("step-1", "QUANT_READ", 1, "b" * 64)
        calls = []

        def operation(_):
            calls.append("executed")
            return "synthetic-result-ref"

        assert journal.execute(lease, tool_call, authorize=lambda _: None, operation=operation) == (
            "synthetic-result-ref"
        )
        assert journal.execute(lease, tool_call, authorize=lambda _: None, operation=operation) == (
            "synthetic-result-ref"
        )
        try:
            journal.execute(
                lease, replace(tool_call, input_hash="c" * 64),
                authorize=lambda _: None, operation=operation,
            )
        except RunConflict as error:
            assert error.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("A replay changed the tool input")
        try:
            journal.execute(
                lease, replace(tool_call, tool_id="BROKER_SUBMIT"),
                authorize=lambda _: None, operation=operation,
            )
        except AgentPolicyDenied as error:
            assert error.code == "AGENT_POLICY_DENIED"
        else:
            raise AssertionError("Unregistered tool reached an operation")
        assert calls == ["executed"]
        with owner.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM agent_research.tool_receipts")) == 1
            denied = connection.execute(text("SELECT code FROM agent_research.tool_denials"))
            assert set(denied.scalars()) == {"AGENT_POLICY_DENIED", "IDEMPOTENCY_CONFLICT"}

        with owner.begin() as connection:
            connection.execute(text("""
                UPDATE agent_research.runs SET lease_until = clock_timestamp() - interval '1 second'
                WHERE run_id = :run_id
            """), {"run_id": run_id})
        recovered = store.claim(second_worker)
        assert recovered is not None and recovered.owner == second_worker
        assert recovered.run_id == run_id and recovered.fence > lease.fence
        with worker.begin() as connection:
            denial = connection.scalar(text("""
                SELECT agent_research.charge_work(:id,:owner,:fence,:version,'steps')
            """), {"id": lease.run_id, "owner": lease.owner,
                   "fence": lease.fence, "version": lease.version})
            assert denial == "LEASE_LOST"

        with owner.connect() as connection:
            event_count = connection.scalar(text("SELECT count(*) FROM agent_research.run_events"))

        try:
            store.transition(lease, RunState.COMPLETED)
        except RunConflict as error:
            assert error.code == "LEASE_LOST"
        else:
            raise AssertionError("Stale fence accepted")

        try:
            store.renew(lease)
        except RunConflict as error:
            assert error.code == "LEASE_LOST"
        else:
            raise AssertionError("Stale owner extended a lease")

        with owner.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM agent_research.run_events")) == (
                event_count
            )

        store.transition(recovered, RunState.WAITING_FOR_INPUT, checkpoint_ref="checkpoint-v1")
        assert store.claim(first_worker) is None
        with owner.connect() as connection:
            row = connection.execute(text("""
                SELECT state, version, lease_until FROM agent_research.runs WHERE run_id = :run_id
            """), {"run_id": run_id}).one()
            assert row.state == RunState.WAITING_FOR_INPUT and row.lease_until is None
        store.resume_input(run_id, row.version)
        resumed = store.claim(first_worker)
        assert resumed is not None and resumed.run_id == run_id and resumed.fence > recovered.fence
        store.cancel(run_id, resumed.version)
        try:
            store.cancel(run_id, row.version)
        except RunConflict as error:
            assert error.code == "VERSION_CONFLICT"
        else:
            raise AssertionError("Stale cancellation version accepted")
        try:
            store.transition(resumed, RunState.COMPLETED)
        except RunConflict as error:
            assert error.code == "LEASE_LOST"
        else:
            raise AssertionError("Cancelled worker committed a terminal result")
        with owner.connect() as connection:
            state = connection.execute(text("""
                SELECT state FROM agent_research.runs WHERE run_id = :run_id
            """), {"run_id": run_id}).scalar_one()
            assert state == RunState.CANCELLED

        _verify_consent(owner, worker, store, request)
        _verify_budget_and_dispatch(owner, worker, store, request)
        _verify_work_hierarchy(owner, worker, store, request)
    finally:
        worker.dispose()
        owner.dispose()

    print(PROOF)


def test_agent_research_runtime_actual_postgresql():
    assert host() == PROOF + "/restore"


if __name__ == "__main__":
    try:
        guest()
    except Exception as error:
        import traceback

        frames = traceback.extract_tb(error.__traceback__)
        own_frames = [
            frame for frame in frames
            if frame.filename.startswith(("/opt/lab/packages/", "/opt/lab/tests/"))
        ]
        where = ",".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in own_frames[-4:])
        state = getattr(getattr(error, "orig", None), "sqlstate", None)
        print(f"FAIL {type(error).__name__} {state or '-'} {where}")
        raise SystemExit(1) from None
