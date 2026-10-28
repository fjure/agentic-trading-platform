"""Actual PostgreSQL ACL checks for the deliberately narrow retrospective ports."""


def verify_roles(engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    tables = (
        "retrospective_assessments",
        "retrospective_datasets",
        "retrospective_revision_bindings",
        "retrospective_reports",
        "retrospective_audit",
        "license_receipts",
    )
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_backtest_worker"))
        assert set(
            connection.execute(text("SELECT * FROM backtest.source_license_status"))
            .mappings()
            .one()
        ) == {"provider", "status", "observed_at", "policy_version"}
        assert not connection.execute(
            text("SELECT has_table_privilege(current_user,'research.source_status','SELECT')")
        ).scalar_one()
        for table in tables:
            assert connection.execute(
                text("SELECT has_table_privilege(current_user,:table,'SELECT')"),
                {"table": "backtest." + table},
            ).scalar_one()
            assert not connection.execute(
                text(
                    "SELECT has_table_privilege(current_user,:table,'DELETE') OR "
                    "has_table_privilege(current_user,:table,'TRUNCATE')"
                ),
                {"table": "backtest." + table},
            ).scalar_one()
        assert not connection.execute(
            text("""SELECT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S') AND
            (has_table_privilege(current_user,c.oid,'SELECT') OR
              has_table_privilege(current_user,c.oid,'INSERT')
             OR has_table_privilege(current_user,c.oid,'UPDATE') OR
               has_table_privilege(current_user,c.oid,'DELETE')))""")
        ).scalar_one()
        assert not connection.execute(
            text(
                "SELECT has_function_privilege(current_user,'research.reserve_version()','EXECUTE')"
            )
        ).scalar_one()
        assert not connection.execute(
            text(
                "SELECT "
                "has_function_privilege(current_user,'backtest.request_retrospective_assessment(uuid,text,text,jsonb,text,text,text)','EXECUTE')"
            )
        ).scalar_one()
    for role, statement in (
        ("atl_backtest_worker", "SELECT * FROM research.source_status"),
        ("atl_backtest_worker", "UPDATE backtest.license_receipts SET status='DECLARED_CONFIRMED'"),
        (
            "atl_backtest_worker",
            "INSERT INTO backtest.retrospective_assessments(assessment_id) "
            "VALUES(gen_random_uuid())",
        ),
        (
            "atl_control_api",
            "INSERT INTO backtest.retrospective_datasets(assessment_id) VALUES(gen_random_uuid())",
        ),
        ("atl_control_api", "UPDATE research.bar_revisions SET close=close"),
        (
            "atl_control_api",
            "INSERT INTO research.evidence_revisions(evidence_id) VALUES(gen_random_uuid())",
        ),
        ("atl_worker", "SELECT * FROM backtest.retrospective_datasets"),
        (
            "atl_execution",
            "INSERT INTO backtest.retrospective_reports(report_id) VALUES(gen_random_uuid())",
        ),
    ):
        try:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL ROLE " + role))
                connection.execute(text(statement))
        except DBAPIError:
            pass
        else:
            raise AssertionError("runtime role escaped retrospective boundary")


def test_retrospective_roles_actual_postgresql():
    from tests.integration.test_retrospective_storage import host

    assert host().endswith("/roles/restore")
