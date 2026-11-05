"""Persisted attempts, stale fences, atomic slot release and old claim SQL."""


def verify_recovery(engine, jobs, first, denied):
    from uuid import uuid4

    from sqlalchemy import text

    from packages.backtesting.jobs import encoded
    from packages.research.models import content_fingerprint
    from tests.integration.test_retrospective_storage import LEGACY_CLAIM_SQL

    def rewind():
        with jobs.transaction(first) as c:
            c.execute(
                text(
                    "UPDATE backtest.experiment_batches SET status='ACCEPTED',owner=NULL,"
                    "lease_until=NULL WHERE batch_id=:id"
                ),
                {"id": first.batch_id},
            )

    denied(rewind)

    # Synthetic expiration uses only the disposable migration-owner test role.
    def expire():
        with engine.begin() as c:
            c.execute(text("ALTER TABLE backtest.experiment_batches DISABLE TRIGGER a_quant_batch"))
            c.execute(
                text(
                    "UPDATE backtest.experiment_batches SET "
                    "lease_until=clock_timestamp()-interval '1s' "
                    "WHERE batch_id=:id"
                ),
                {"id": first.batch_id},
            )
            c.execute(text("ALTER TABLE backtest.experiment_batches ENABLE TRIGGER a_quant_batch"))

    expire()

    def reset_spent_steps():
        with jobs.transaction() as c:
            c.execute(
                text(
                    "UPDATE backtest.experiment_batches SET owner=:owner,fence=fence+1,"
                    "attempts=attempts+1,started_at=clock_timestamp(),bar_steps=0,"
                    "lease_until=clock_timestamp()+interval '300 seconds' WHERE batch_id=:id"
                ),
                {"id": first.batch_id, "owner": uuid4()},
            )

    denied(reset_spent_steps)
    second = jobs.claim(uuid4())
    assert second.batch_id == first.batch_id and second.fence == 2
    assert second.row["bar_steps"] == 100000
    denied(lambda: jobs.checkpoint(first))
    # Add a pending INTERNAL run reference. The exact old binary's unfiltered
    # runs SQL sees it, but cannot promote it or consume a separate lease.
    with jobs.transaction(second) as c:
        child = uuid4()
        bound = (
            c.execute(
                text(
                    "SELECT d.fingerprint,d.policy_hash,c.candidate_id,c.role FROM "
                    "backtest.experiment_batches b JOIN backtest.experiment_definitions d "
                    "USING(definition_id) "
                    "JOIN backtest.experiment_candidates c USING(definition_id) WHERE "
                    "b.batch_id=:batch "
                    "ORDER BY c.candidate_id,c.role LIMIT 1"
                ),
                {"batch": second.batch_id},
            )
            .mappings()
            .one()
        )
        request = dict(
            definitionHash=bound["fingerprint"],
            strategySelectionPolicyHash=bound["policy_hash"],
            candidateId=bound["candidate_id"],
            role=bound["role"],
            phaseKey="0:training",
        )
        c.execute(
            text(
                "INSERT INTO backtest.runs(run_id,actor,idempotency_key,request,request_hash,"
                "snapshot_version,snapshot_at,code_version,trace_id,quant_batch_id) SELECT "
                ":id,actor,"
                ":key,CAST(:body AS "
                "jsonb),:hash,snapshot_version,snapshot_at,code_version,trace_id,batch_id "
                "FROM backtest.experiment_batches WHERE batch_id=:batch"
            ),
            {
                "id": child,
                "key": "internal-child-" + str(child),
                "hash": content_fingerprint(request),
                "body": encoded(request),
                "batch": second.batch_id,
            },
        )

    def old_claim():
        with jobs.engine.begin() as c:
            c.execute(text(LEGACY_CLAIM_SQL), {"owner": uuid4()})

    denied(old_claim)
    with engine.connect() as c:
        state = c.execute(
            text("SELECT status,owner,fence,attempts FROM backtest.runs WHERE run_id=:id"),
            {"id": child},
        ).one()
        assert state == ("ACCEPTED", None, 0, 0)
        assert c.execute(text("SELECT backtest.active_slots()")).scalar_one() == 11
    expire()
    third = jobs.claim(uuid4())
    assert third.fence == 3 and third.row["attempts"] == 3
    denied(lambda: jobs.publish(second, status="FAILED", error_code="INTERNAL_FAILURE"))
    expire()
    assert jobs.claim(uuid4()) is None
    with engine.connect() as c:
        assert c.execute(
            text("SELECT status,error_code FROM backtest.experiment_batches WHERE batch_id=:id"),
            {"id": first.batch_id},
        ).one() == ("FAILED", "ATTEMPTS_EXHAUSTED")


def verify_shared_guards(control, worker, store, jobs, definition, validation, request, denied):
    from uuid import uuid4

    from sqlalchemy import text

    from packages.backtesting.jobs import BacktestJobs, encoded
    from packages.research.models import content_fingerprint

    strict = BacktestJobs(control).enqueue(
        request, "local-operator", "qsl-collision-legacy-0001", "a" * 32, "b" * 40
    )
    identifier = strict["run_id"]
    with store.transaction() as c:
        assert (
            c.execute(
                text(
                    "SELECT backtest.request_experiment_batch(:id,:definition,:version,"
                    ":hash,:validation,:validation_hash,'local-operator','qsl-collision-quant-0001',"
                    "repeat('b',40),repeat('a',32))"
                ),
                dict(
                    id=identifier,
                    definition=definition["definition_id"],
                    version=definition["definition_version"],
                    hash=definition["fingerprint"],
                    validation=validation["validation_id"],
                    validation_hash=validation["fingerprint"],
                ),
            ).scalar_one()
            == identifier
        )
    old_jobs = BacktestJobs(worker)
    old = old_jobs.claim(uuid4())
    assert old.run_id == identifier

    def raw_claim():
        with jobs.transaction() as c:
            c.execute(
                text(
                    "UPDATE backtest.experiment_batches SET status='RUNNING',owner=:owner,"
                    "fence=fence+1,attempts=attempts+1,started_at=clock_timestamp(),"
                    "lease_until=clock_timestamp()+interval '300 seconds' WHERE batch_id=:id"
                ),
                dict(id=identifier, owner=uuid4()),
            )

    denied(raw_claim)
    old_jobs.publish(old, {}, status="FAILED", error_code="INTERNAL_FAILURE")
    new = jobs.claim(uuid4())
    assert new.batch_id == identifier
    jobs.publish(new, status="FAILED", error_code="INTERNAL_FAILURE")

    def stale_admission():
        with control.begin() as c:
            c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            c.execute(text("SET LOCAL backtest.exposure_version='quant-exposure-v1'"))
            c.execute(
                text(
                    "SELECT backtest.request_run(:id,'local-operator',"
                    "'qsl-stale-isolation-0001',CAST(:body AS "
                    "jsonb),:hash,repeat('b',40),repeat('a',32))"
                ),
                dict(id=uuid4(), body=encoded(request), hash=content_fingerprint(request)),
            )

    denied(stale_admission)

    strict = BacktestJobs(control).enqueue(
        request, "local-operator", "qsl-expired-legacy-0001", "a" * 32, "b" * 40
    )
    old_id = strict["run_id"]

    def expire_old():
        with worker.begin() as c:
            c.execute(
                text(
                    "UPDATE backtest.runs SET lease_until=clock_timestamp()-interval '1 second' "
                    "WHERE run_id=:id"
                ),
                {"id": old_id},
            )

    for attempt in range(1, 4):
        expired = old_jobs.claim(uuid4())
        assert expired.run_id == old_id and expired.row["attempts"] == attempt
        expire_old()
    queued = store.enqueue(
        definition["definition_id"],
        definition["fingerprint"],
        "local-operator",
        "qsl-expired-legacy-neighbor",
        "a" * 32,
        "b" * 40,
        definition_version=definition["definition_version"],
        validation_id=validation["validation_id"],
        validation_hash=validation["fingerprint"],
    )
    held = jobs.claim(uuid4())
    assert held.batch_id == queued["batch_id"]

    def mutate_old(change):
        with worker.begin() as c:
            c.execute(text(f"UPDATE backtest.runs SET {change} WHERE run_id=:id"), {"id": old_id})

    denied(lambda: mutate_old("lease_until=clock_timestamp()+interval '300 seconds'"))
    denied(lambda: mutate_old("started_at=clock_timestamp()"))
    denied(lambda: mutate_old("attempts=attempts-1"))
    with worker.connect() as c:
        assert c.execute(
            text(
                "SELECT status,attempts,lease_until<=clock_timestamp() FROM backtest.runs "
                "WHERE run_id=:id"
            ),
            {"id": old_id},
        ).one() == ("RUNNING", 3, True)
    jobs.publish(held, status="FAILED", error_code="INTERNAL_FAILURE")
    assert old_jobs.claim(uuid4()) is None
