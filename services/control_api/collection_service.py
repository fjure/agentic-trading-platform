"""Versionsgebundene Sammlungswünsche und begrenzte gespeicherte Beobachtungsmetriken."""

import json
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal
from time import monotonic

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from packages.backtesting.calendar import read_schedule_rows
from packages.backtesting.coverage import read_coverage, read_quality
from packages.backtesting.dataset import DatasetPort
from packages.backtesting.models import BacktestBudgetExceeded, ExperimentConfig, Fold
from packages.backtesting.readiness import AssessmentBudget, TrustSnapshot, assess
from packages.backtesting.readiness_jobs import load_observations
from packages.domain.models import utc_string
from packages.research.collection import SCHEDULE, registered_instruments
from packages.research.evidence_storage import EvidenceBudgetExceeded, read_trust_snapshot
from services.control_api.backtest_service import identity
from services.control_api.collection_dto import (
    CollectionPlan,
    CollectionPlanPage,
    CollectionPlanUpdate,
)


class CollectionError(ValueError):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


def plain(value):
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


class CollectionPlanService:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _identifier(value):
        result = identity(value)
        if value not in registered_instruments():
            raise CollectionError(404, "NOT_FOUND")
        return result

    @staticmethod
    def _transaction(connection):
        connection.execute(text("SET LOCAL statement_timeout='3s'"))
        connection.execute(text("SET LOCAL lock_timeout='1s'"))

    def _statistics(self, connection, instrument_id):
        """Gemeinsamer Assessor, maximal24h/1s; kein Experiment-/Simulationsauftrag."""
        deadline = monotonic() + 1

        def checkpoint():
            if monotonic() > deadline:
                raise BacktestBudgetExceeded("collection projection budget exhausted")

        port = DatasetPort()
        snapshot = port.snapshot(connection)
        end, start = snapshot.at, snapshot.at - timedelta(hours=24)
        symbol = registered_instruments()[str(instrument_id)]
        config = ExperimentConfig(
            instrument_id,
            symbol,
            "flat-v1",
            (),
            Decimal("1"),
            Decimal("0"),
            Decimal("0"),
            Decimal("0.000001"),
            (Fold(start - timedelta(minutes=15), start, start, end),),
        )
        frozen = port.freeze(connection, instrument_id, start, end, snapshot, checkpoint)
        calendars = read_schedule_rows(connection, snapshot)
        quality = read_quality(connection, instrument_id, start, end, snapshot)
        proofs = read_coverage(connection, instrument_id, snapshot)
        trust = TrustSnapshot.from_mapping(read_trust_snapshot(connection, end, snapshot.version))
        observed = load_observations(
            connection, instrument_id, start, end, snapshot, frozen.revisions
        )
        checkpoint()
        result = assess(
            config,
            frozen,
            calendars,
            quality,
            proofs,
            trust=trust,
            observations=observed,
            budget=AssessmentBudget(max_seconds=1),
            checkpoint=checkpoint,
        )
        if not result.evaluation_complete:
            raise CollectionError(503, "UNAVAILABLE")
        windows = result.windows_dict()
        # Ohne benanntes Nutzerprotokoll keine Behauptung zu dessen Warm-up/Experiment.
        windows.update(strictSignal=[], strictOutcome=[], completeExperiment=[], complete=False)
        metrics = plain(result.metrics)
        for metric in metrics:
            metric["limitations"].append("EXPERIMENT_PROTOCOL_NOT_BOUND")
        return dict(metrics=metrics, windows=windows)

    def _project(self, connection, instrument_id):
        base = connection.execute(
            text("SELECT research.collection_plan_projection(:id)"), {"id": instrument_id}
        ).scalar_one()
        try:
            with connection.begin_nested():
                stats = self._statistics(connection, instrument_id)
        except (BacktestBudgetExceeded, EvidenceBudgetExceeded, DBAPIError, CollectionError):
            stats = self._unknown_statistics()
            base["reasons"] = base["reasons"][:31] + ["METRICS_UNAVAILABLE"]
        return {**base, **stats, "schedule": SCHEDULE}

    @staticmethod
    def _unknown_statistics():
        return dict(
            metrics=[],
            windows=dict(
                barSignal=[],
                barOutcome=[],
                strictSignal=[],
                strictOutcome=[],
                completeExperiment=[],
                complete=False,
            ),
        )

    def read_plans(self, query):
        if query:
            raise CollectionError(400, "INVALID_REQUEST")
        try:
            with self.engine.begin() as connection:
                self._transaction(connection)
                connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                items = []
                for identifier in registered_instruments():
                    instrument = self._identifier(identifier)
                    if connection.execute(
                        text(
                            "SELECT EXISTS(SELECT FROM research.instruments "
                            "WHERE instrument_id=:id)"
                        ),
                        {"id": instrument},
                    ).scalar_one():
                        items.append(self._project(connection, instrument))
                now = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
                return CollectionPlanPage.model_validate(
                    dict(items=items, observedAt=utc_string(now))
                ).model_dump()
        except (BacktestBudgetExceeded, EvidenceBudgetExceeded, DBAPIError):
            raise CollectionError(503, "UNAVAILABLE") from None

    def update_plan(self, instrument_id, payload, actor, key, trace, if_match=None):
        instrument = self._identifier(instrument_id)
        request = CollectionPlanUpdate.model_validate(payload).model_dump()
        if actor != "local-operator":
            raise CollectionError(403, "FORBIDDEN")
        if if_match is not None:
            raise CollectionError(400, "INVALID_REQUEST")
        try:
            with self.engine.begin() as connection:
                self._transaction(connection)
                previous = (
                    connection.execute(
                        text("""
                  SELECT request_body,response FROM research.collection_commands
                  WHERE actor=:actor AND instrument_id=:id AND idempotency_key=:key
                """),
                        dict(actor=actor, id=instrument, key=key),
                    )
                    .mappings()
                    .one_or_none()
                )
                if previous is not None:
                    if previous["request_body"] != request:
                        raise CollectionError(409, "IDEMPOTENCY_CONFLICT")
                    return CollectionPlan.model_validate(previous["response"]).model_dump()
            # Die Auswertung darf weder den Planlock halten noch eine Pause verhindern.
            # SQL-Timeouts brechen ihre eigene Transaktion ab; die Mutation bleibt nutzbar.
            try:
                with self.engine.begin() as connection:
                    self._transaction(connection)
                    stats = self._statistics(connection, instrument)
            except (BacktestBudgetExceeded, EvidenceBudgetExceeded, DBAPIError, CollectionError):
                if request["enabled"]:
                    raise
                stats = self._unknown_statistics()
            with self.engine.begin() as connection:
                self._transaction(connection)
                # Feste Schedule ist serverseitig, niemals Bestandteil der Eingabe.
                response = connection.execute(
                    text("""
                  SELECT research.set_collection_plan(:id,:enabled,:expected,:actor,:key,:trace,
                    CAST(:stats AS jsonb))
                """),
                    dict(
                        id=instrument,
                        enabled=request["enabled"],
                        expected=request["expectedVersion"],
                        actor=actor,
                        key=key,
                        trace=trace,
                        stats=json.dumps(stats),
                    ),
                ).scalar_one()
                response["schedule"] = SCHEDULE
                response = CollectionPlan.model_validate(response).model_dump()
                return response
        except DBAPIError as error:
            code = getattr(getattr(error.orig, "diag", None), "message_primary", "")
            if code in {"IDEMPOTENCY_CONFLICT", "VERSION_CONFLICT"}:
                raise CollectionError(409, code) from None
            if code == "NOT_FOUND":
                raise CollectionError(404, code) from None
            if code == "FORBIDDEN":
                raise CollectionError(403, code) from None
            raise CollectionError(503, "UNAVAILABLE") from None
        except (BacktestBudgetExceeded, EvidenceBudgetExceeded):
            raise CollectionError(503, "UNAVAILABLE") from None
