"""Persistent idempotent start requests and bounded isolated execution."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from sqlalchemy import text

from packages.domain.models import decimal_string, decimal_value, utc_string
from packages.persistence import PostgresLedger
from services.demo.pipeline import execute_run, recover_interrupted
from services.demo.storage import (
    database_name,
    financial_engine,
    initialize_metadata,
    private_engine,
    provision_run,
    valid_trace,
)


class DemoError(RuntimeError):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


def validate_start(payload):
    if (
        type(payload) is not dict
        or set(payload) != {"initialBudget", "currency", "strategy"}
        or type(payload["currency"]) is not str
        or type(payload["strategy"]) is not str
        or payload["currency"] != "USD"
        or payload["strategy"] not in {"mock-neutral", "neutral-baseline"}
        or type(payload["initialBudget"]) is not str
        or not re.fullmatch(r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,18})?", payload["initialBudget"])
    ):
        raise DemoError("INVALID_REQUEST", 400)
    budget = decimal_value(payload["initialBudget"])
    if not 0 < budget <= 1000000:
        raise DemoError("INVALID_REQUEST", 400)
    return {**payload, "initialBudget": decimal_string(budget)}


def public_run(row):
    value = {
        "runId": str(row["run_id"]),
        "status": row["status"],
        "simulationOnly": True,
        "traceId": row["trace_id"],
        "initialBudget": decimal_string(row["initial_budget"]),
        "currency": "USD",
        "strategy": row["strategy"],
        "createdAt": utc_string(row["created_at"]),
        "updatedAt": utc_string(row["updated_at"]),
    }
    if row["error_code"]:
        value["errorCode"] = row["error_code"]
    return value


class DemoCoordinator:
    def __init__(self, metadata_engine, *, asynchronous=True, fail_after_accept=False):
        self.engine = metadata_engine
        self.asynchronous = asynchronous
        self.fail_after_accept = fail_after_accept
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="demo")
        initialize_metadata(self.engine)
        self.recover()

    @contextmanager
    def _lock(self, name):
        with self.engine.connect() as c:
            c.execute(text("SELECT pg_advisory_lock(hashtext(:name))"), {"name": name})
            c.commit()
            try:
                yield
            finally:
                c.execute(text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": name})
                c.commit()

    def _row(self, run_id):
        try:
            database_name(run_id)
        except (ValueError, TypeError, AttributeError):
            raise DemoError("INVALID_REQUEST", 400) from None
        with self.engine.connect() as c:
            row = (
                c.execute(text("SELECT * FROM public.demo_runs WHERE run_id=:id"), {"id": run_id})
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise DemoError("INVALID_REQUEST", 404)
        return row

    def _finish(self, run_id, status, error=None, *, only_running=False):
        with self.engine.begin() as c:
            c.execute(
                text("""UPDATE public.demo_runs SET status=:status,error_code=:error,
              updated_at=clock_timestamp() WHERE run_id=:id
              AND (status='RUNNING' OR :unconditional)"""),
                {"id": run_id, "status": status, "error": error, "unconditional": not only_running},
            )

    def start(self, payload, key, actor, trace):
        payload = validate_start(payload)
        valid_trace(trace)
        if (
            not isinstance(key, str)
            or not 1 <= len(key) <= 200
            or not isinstance(actor, str)
            or not 1 <= len(actor) <= 255
        ):
            raise DemoError("INVALID_REQUEST", 400)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        created = False
        with self._lock("demo-start.v1"):
            with self.engine.begin() as c:
                row = (
                    c.execute(
                        text("SELECT * FROM public.demo_runs WHERE request_key=:key"), {"key": key}
                    )
                    .mappings()
                    .one_or_none()
                )
                if row:
                    if row["request_hash"] != digest or row["actor"] != actor:
                        raise DemoError("IDEMPOTENCY_CONFLICT", 409)
                else:
                    active = c.execute(
                        text("SELECT count(*) FROM public.demo_runs WHERE status='RUNNING'")
                    ).scalar_one()
                    if active:
                        raise DemoError("VERSION_CONFLICT", 409)
                    run_id = str(uuid.uuid4())
                    row = (
                        c.execute(
                            text("""INSERT INTO public.demo_runs
                      (run_id,request_key,request_hash,actor,trace_id,initial_budget,strategy,status)
                      VALUES (:id,:key,:hash,:actor,:trace,:budget,:strategy,'RUNNING')
                      RETURNING *"""),
                            {
                                "id": run_id,
                                "key": key,
                                "hash": digest,
                                "actor": actor,
                                "trace": trace,
                                "budget": payload["initialBudget"],
                                "strategy": payload["strategy"],
                            },
                        )
                        .mappings()
                        .one()
                    )
                    created = True
            result = public_run(row)
            if created:
                if self.asynchronous:
                    self.executor.submit(self._execute, result["runId"])
                else:
                    self._execute(result["runId"])
                    result = self.get_run(result["runId"])
        return 202, result, {"Location": "/api/v1/demo/runs/" + result["runId"]}

    def _execute(self, run_id):
        engine = None
        try:
            with self._lock(run_id):
                if self._row(run_id)["status"] != "RUNNING":
                    return
                engine = provision_run(self.engine, run_id)
            error = execute_run(
                self.engine,
                self._row(run_id),
                engine,
                fail_after_accept=self.fail_after_accept,
                activation_guard=lambda: self._lock(run_id),
                running=lambda: self._row(run_id)["status"] == "RUNNING",
                transport_guard=lambda: self._transport_guard(run_id),
            )
            self._finish(run_id, "FAILED" if error else "SUCCEEDED", error, only_running=True)
        except Exception:
            if self._row(run_id)["status"] == "RUNNING":
                try:
                    recover_interrupted(self.engine, self._row(run_id))
                except Exception:
                    pass  # no resubmit, reservation release, latch clearing, or destructive reset
                self._finish(run_id, "PAUSED", "TEMPORARILY_UNAVAILABLE", only_running=True)
        finally:
            if engine is not None:
                engine.dispose()
            self._apply_control(run_id)

    @contextmanager
    def _transport_guard(self, run_id):
        from services.execution.gates import SubmitDenied

        # Metadata control requests and the pure local Fake transport serialize
        # on this row. An already committed pause/kill can never be followed by
        # another synthetic acceptance or fill, even before DB latch delivery.
        with self.engine.begin() as c:
            status = c.execute(
                text("SELECT status FROM public.demo_runs WHERE run_id=:id FOR UPDATE"),
                {"id": run_id},
            ).scalar_one()
            if status != "RUNNING":
                raise SubmitDenied("PAUSED_NEW")
            yield

    def recover(self):
        with self.engine.connect() as c:
            runs = (
                c.execute(text("SELECT * FROM public.demo_runs WHERE status='RUNNING'"))
                .mappings()
                .all()
            )
        for run in runs:
            run_id = str(run["run_id"])
            with self._lock(run_id):
                if self._row(run_id)["status"] != "RUNNING":
                    continue
                try:
                    recover_interrupted(self.engine, run)
                except Exception:
                    pass
                self._finish(run_id, "PAUSED", "TEMPORARILY_UNAVAILABLE")
        with self.engine.connect() as c:
            pending = (
                c.execute(
                    text("SELECT run_id FROM public.demo_runs WHERE control_action IS NOT NULL")
                )
                .scalars()
                .all()
            )
        for run_id in pending:
            self._apply_control(str(run_id))

    def list_runs(self):
        with self.engine.connect() as c:
            rows = (
                c.execute(text("SELECT * FROM public.demo_runs ORDER BY created_at DESC LIMIT 100"))
                .mappings()
                .all()
            )
        return {"items": [public_run(row) for row in rows], "nextCursor": None}

    def get_run(self, run_id):
        return public_run(self._row(run_id))

    @contextmanager
    def _reader(self, run_id):
        from services.control_api.snapshot import SnapshotReader

        self._row(run_id)
        engine = financial_engine(self.engine, run_id, role="atl_control_api")
        try:
            yield SnapshotReader(PostgresLedger(engine=engine), "paper", account_alias="demo")
        finally:
            engine.dispose()

    def snapshot(self, run_id, trace):
        valid_trace(trace)
        with self._reader(run_id) as reader:
            return reader.snapshot(trace)

    def history(self, run_id, resource, **kwargs):
        with self._reader(run_id) as reader:
            return reader.page(resource, **kwargs)

    def control(self, run_id, action, actor, trace):
        self._row(run_id)
        valid_trace(trace)
        if action not in {"pause", "kill"} or type(actor) is not str or not 1 <= len(actor) <= 255:
            raise DemoError("INVALID_REQUEST", 400)
        # Commit the latch intent before touching a financial database or waiting
        # for initialization. A kill intent is never downgraded by later pause.
        with self.engine.begin() as c:
            c.execute(
                text("""UPDATE public.demo_runs SET status='PAUSED',error_code=NULL,
              control_action=CASE WHEN control_action='kill' THEN 'kill' ELSE :action END,
              control_trace=:trace,control_actor=:actor,updated_at=clock_timestamp()
              WHERE run_id=:id"""),
                {"id": run_id, "action": action, "trace": trace, "actor": actor},
            )
        self._apply_control(run_id)
        return {
            "runId": run_id,
            "action": action,
            "status": "PAUSED",
            "simulationOnly": True,
            "traceId": trace,
        }

    def _apply_control(self, run_id):
        """Best effort now, mandatory at worker checkpoints/startup; never block."""
        from services.control_api.control import kill, pause

        row = self._row(run_id)
        if not row["control_action"]:
            return
        with self.engine.connect() as guard:
            locked = guard.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:id))"), {"id": run_id}
            ).scalar_one()
            guard.commit()
            if not locked:
                return
            engine = None
            try:
                exists = guard.execute(
                    text("SELECT EXISTS(SELECT FROM pg_database WHERE datname=:db)"),
                    {"db": database_name(run_id)},
                ).scalar_one()
                guard.commit()
                if not exists:
                    return
                engine = financial_engine(self.engine, run_id, role="atl_control_api")
                with engine.connect() as c:
                    state = c.execute(
                        text(
                            "SELECT safety_state FROM public.risk_account_context "
                            "WHERE account_id='paper'"
                        )
                    ).scalar_one_or_none()
                if (
                    state is None
                    or state == "HALTED"
                    or (state == "PAUSE_NEW" and row["control_action"] == "pause")
                ):
                    return
                (kill if row["control_action"] == "kill" else pause)(
                    ledger=PostgresLedger(engine=engine),
                    account_id="paper",
                    actor_id=row["control_actor"],
                    trace_id=row["control_trace"],
                    reason_code="KILL_SWITCH_ACTIVE"
                    if row["control_action"] == "kill"
                    else "PAUSED_NEW",
                )
            except Exception:
                # The committed metadata intent blocks all future demo work;
                # missing/unready DB or unavailable role can never authorize it.
                return
            finally:
                if engine is not None:
                    engine.dispose()
                guard.execute(text("SELECT pg_advisory_unlock(hashtext(:id))"), {"id": run_id})
                guard.commit()

    def close(self):
        self.executor.shutdown(wait=True)
        self.engine.dispose()


def create_coordinator():
    return DemoCoordinator(private_engine())
