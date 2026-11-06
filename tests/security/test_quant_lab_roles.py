"""Real PostgreSQL privileges, including direct SQL escape attempts."""


def verify_roles(engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from tests.integration.test_quant_lab_storage import TABLES

    for role in ("atl_control_api", "atl_backtest_worker"):
        with engine.begin() as c:
            c.execute(text("SET LOCAL ROLE " + role))
            for table in TABLES:
                if "experiment_" in table or table.endswith("test_exposures"):
                    assert c.execute(
                        text("SELECT has_table_privilege(current_user,:table,'SELECT')"),
                        {"table": table},
                    ).scalar_one() is (
                        role == "atl_control_api"
                        or table != "backtest.experiment_validation_requests"
                    )
                    assert not c.execute(
                        text(
                            "SELECT has_table_privilege(current_user,:table,'DELETE') "
                            "OR has_table_privilege(current_user,:table,'TRUNCATE')"
                        ),
                        {"table": table},
                    ).scalar_one()
            assert not c.execute(
                text("SELECT has_table_privilege(current_user,'backtest.test_exposures','INSERT')")
            ).scalar_one()
    for role, statement in (
        ("atl_control_api", "UPDATE backtest.experiment_batches SET status='SUCCEEDED'"),
        (
            "atl_control_api",
            "INSERT INTO backtest.experiment_reports(batch_id) VALUES(gen_random_uuid())",
        ),
        (
            "atl_backtest_worker",
            "INSERT INTO backtest.experiment_definitions(definition_id) VALUES(gen_random_uuid())",
        ),
        ("atl_backtest_worker", "UPDATE backtest.experiment_batches SET publication_bytes=0"),
        ("atl_backtest_worker", "UPDATE backtest.experiment_candidates SET role='SEARCH'"),
        (
            "atl_backtest_worker",
            "SELECT backtest.legacy_request_run(NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
        ),
        ("atl_backtest_worker", "SELECT * FROM public.broker_orders"),
        (
            "atl_backtest_worker",
            "INSERT INTO backtest.readiness_jobs(assessment_id) VALUES(gen_random_uuid())",
        ),
        ("atl_worker", "SELECT * FROM backtest.experiment_batches"),
        ("atl_execution", "SELECT * FROM backtest.experiment_batches"),
    ):
        try:
            with engine.begin() as c:
                c.execute(text("SET LOCAL ROLE " + role))
                c.execute(text(statement))
        except DBAPIError as error:
            assert error.orig.sqlstate == "42501"
        else:
            raise AssertionError("role escaped QSL privilege boundary: " + role)

    from services.runtime.provision_paper import _dormant_privilege_manifest

    old = _dormant_privilege_manifest([("0034_retrospective_gate",)])
    new = _dormant_privilege_manifest([("0035_quant_lab",)])
    with_validation = _dormant_privilege_manifest([("0036_quant_validation_attempts",)])
    assert old < new < with_validation
    with engine.connect() as c:
        for kind, name, role, privilege, grantable in with_validation - old:
            assert role == "atl_paper_control_api" and not grantable
            if kind == "relation":
                assert privilege == "SELECT"
                assert c.execute(
                    text(
                        "SELECT has_table_privilege('atl_control_api',:name,'SELECT') "
                        "AND NOT has_table_privilege('atl_watchdog',:name,'SELECT')"
                    ),
                    {"name": name},
                ).scalar_one()
            else:
                assert kind == "function" and privilege == "EXECUTE"
                assert c.execute(
                    text(
                        "SELECT has_function_privilege('atl_control_api',:name,'EXECUTE') "
                        "AND NOT has_function_privilege('atl_watchdog',:name,'EXECUTE')"
                    ),
                    {"name": name},
                ).scalar_one()
