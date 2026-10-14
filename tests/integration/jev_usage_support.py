"""Private guest; only synthetic MockTransport, never provider credentials/network."""

import hashlib
from dataclasses import replace
from decimal import Decimal


def checks(admin, _execution, engine, now):
    import httpx
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.exc import DBAPIError

    from packages.decisions.jev import JevDecisionEngine, load_config
    from packages.decisions.replay import CachedResponse, ReplayCase, replay
    from packages.domain import models as m
    from packages.persistence import PostgresLedger
    from services.worker.planner import PlannerConfig, plan_proposal

    class Clock:
        def now(self):
            return now

    class Ids:
        def new_id(self, namespace, seed=None):
            return hashlib.sha256(f"jev-pg:{namespace}:{seed}".encode()).hexdigest()

    worker_engine = create_engine(engine.url, hide_parameters=True)

    @event.listens_for(worker_engine, "connect")
    def role(connection, _record):
        connection.execute("SET ROLE atl_worker")
        connection.commit()

    worker = PostgresLedger(engine=worker_engine)
    config = replace(load_config(), enabled=True)
    labels = config.questions["evidence_quality"]["criteria"]
    body = {
        "model": config.model,
        "answers": {
            "regime": {
                "type": "choice",
                "choice": "TREND",
                "confidence": 0.8,
                "probabilities": {"TREND": 0.8, "RANGE": 0.1, "UNCERTAIN": 0.1},
            },
            "evidence_quality": {
                "type": "score",
                "score": 9,
                "confidence": 0.9,
                "legend": {str(i): label for i, label in enumerate(labels)},
                "probabilities": {str(i): int(i == 9) for i in range(10)},
            },
            "signal_conflict": {"type": "noul", "noul": 0.1},
            "shadow_direction": {
                "type": "choice",
                "choice": "LONG",
                "confidence": 0.8,
                "probabilities": {"LONG": 0.8, "FLAT": 0.1, "ABSTAIN": 0.1},
            },
        },
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }
    try:
        with admin.transaction() as tx:
            state = tx.get(m.MarketState, "ms")
        state = replace(
            state,
            id="jev-admitted-input",
            features={
                "previous_close": Decimal("99"),
                "last_close": state.bar.close,
                "window_volume": Decimal("1000"),
            },
        )
        with worker.transaction() as tx:
            tx.insert(state)
        adapter = JevDecisionEngine(
            clock=Clock(),
            ids=Ids(),
            api_key="synthetic-only",
            config=config,
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=body)),
        )
        result = adapter.decide(state, "persisted.v1")
        assert result.reason_code == "JEV_SHADOW" and result.choice is m.Choice.ABSTAIN
        assert (result.input_tokens, result.output_tokens) == (100, 10)
        assert (
            plan_proposal(
                decision=result,
                market_state=state,
                config=PlannerConfig("fixed-one.v1", Decimal("1")),
                clock=Clock(),
                ids=Ids(),
                ledger=worker,
            )
            is None
        )
        del adapter
        restarted = PostgresLedger(engine=worker_engine)
        with restarted.transaction() as tx:
            restored = tx.get(m.DecisionResult, result.id)
            historical = tx.get(m.DecisionResult, "dec")
            assert restored == result
            assert historical.input_tokens is historical.output_tokens is None
            assert (
                tx.connection.execute(
                    text("SELECT count(*) FROM public.trade_proposals WHERE decision_id=:d"),
                    {"d": result.id},
                ).scalar_one()
                == 0
            )
        with admin.transaction() as tx:
            assert tx.connection.execute(
                text("SELECT count(*) FROM public.order_intents")
            ).scalar_one() == 0
        report = replay(
            (ReplayCase("stored", state, now),),
            config.replay_config("persisted.v1"),
            source="cached",
            cached={"stored": CachedResponse(restored)},
        )
        assert report.records[0].result == result
        assert (report.metrics.reported_input_tokens, report.metrics.reported_output_tokens) == (
            100,
            10,
        )
        try:
            with worker.transaction() as tx:
                tx.insert(
                    m.AuditEvent(
                        "invalid-count-audit",
                        now,
                        "fixture",
                        "decision.persist",
                        ("TEST",),
                        "bad-count",
                        1,
                        state.trace_id,
                    )
                )
                tx.connection.execute(
                    text("""
                    INSERT INTO public.decisions
                    SELECT (jsonb_populate_record(NULL::public.decisions,
                      to_jsonb(d) || jsonb_build_object('id','bad-count','input_tokens',NULL,
                                                      'output_tokens',10))).*
                    FROM public.decisions d WHERE id=:id
                """),
                    {"id": result.id},
                )
        except DBAPIError as exc:
            assert exc.orig.sqlstate == "23514"
        else:
            raise AssertionError("database accepted unpaired counts")
        with admin.transaction() as tx:
            assert (
                tx.connection.execute(
                    text("SELECT count(*) FROM public.decisions WHERE id='bad-count'")
                ).scalar_one()
                == 0
            )
        print("PASS Jev shadow usage persisted and replayed")
    finally:
        worker_engine.dispose()


if __name__ == "__main__":
    from risk_support import guest

    guest(checks)
