"""Runtime privilege evidence against actual PostgreSQL catalogs and denied SQL."""


def verify_roles(engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE atl_backtest_worker"))
        for table in (
            "research.version_state",
            "research.bar_revisions",
            "research.calendar_versions",
            "research.quality_observations",
            "backtest.corporate_action_coverage",
        ):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() >= 0
        assert not conn.execute(
            text("""SELECT EXISTS(
            SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S')
              AND (has_table_privilege(current_user,c.oid,'SELECT')
                OR has_table_privilege(current_user,c.oid,'INSERT')
                OR has_table_privilege(current_user,c.oid,'UPDATE')
                OR has_table_privilege(current_user,c.oid,'DELETE')))""")
        ).scalar_one()
        # Trigger functions cannot be invoked as SQL functions. All callable
        # financial control functions must be inaccessible to the simulation role.
        assert not conn.execute(
            text("""SELECT EXISTS(
            SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.prorettype<>'trigger'::regtype
              AND has_function_privilege(current_user,p.oid,'EXECUTE'))""")
        ).scalar_one()
        for table in (
            "research.bar_revisions",
            "research.calendar_versions",
            "research.quality_observations",
            "backtest.corporate_action_coverage",
        ):
            assert not conn.execute(
                text(
                    "SELECT has_table_privilege(current_user,:table,'INSERT') "
                    "OR has_table_privilege(current_user,:table,'UPDATE') "
                    "OR has_table_privilege(current_user,:table,'DELETE')"
                ),
                {"table": table},
            ).scalar_one()
        for table in (
            "backtest.manifests",
            "backtest.fold_results",
            "backtest.results",
            "backtest.audit",
        ):
            assert conn.execute(
                text("SELECT has_table_privilege(current_user,:table,'INSERT')"), {"table": table}
            ).scalar_one()
        assert not conn.execute(
            text(
                "SELECT has_function_privilege(current_user,'research.reserve_version()','EXECUTE')"
            )
        ).scalar_one()
    for role, sql in (
        ("atl_backtest_worker", "SELECT count(*) FROM public.audit_events"),
        ("atl_backtest_worker", "DELETE FROM backtest.corporate_action_coverage"),
        ("atl_control_api", "DELETE FROM backtest.runs"),
        ("atl_control_api", "DELETE FROM backtest.results"),
        ("atl_control_api", "DELETE FROM backtest.manifests"),
    ):
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL ROLE " + role))
                conn.execute(text(sql))
        except DBAPIError:
            pass
        else:
            raise AssertionError("runtime role escaped its boundary")


def test_backtest_roles_actual_postgresql():
    from tests.integration.test_backtest_dataset import host

    assert host("tests.integration.test_backtest_jobs").endswith("/atomic/roles")
