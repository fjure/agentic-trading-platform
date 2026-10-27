"""RDR-03: echte SQLverweigerung und Privilegienkataloge, keine Finanzrechteausweitung."""


def verify_roles(engine):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    immutable = (
        "research.evidence_revisions",
        "research.trust_decisions",
        "research.import_receipts",
        "research.commit_confirmations",
        "research.import_rejections",
    )
    for role in (
        "atl_execution",
        "atl_worker",
        "atl_control_api",
        "atl_watchdog",
        "atl_backtest_worker",
    ):
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE " + role))
            for table in immutable:
                assert not connection.execute(
                    text("""
                    SELECT has_table_privilege(current_user,c.oid,'INSERT') OR
                      has_table_privilege(current_user,c.oid,'UPDATE') OR
                      has_table_privilege(current_user,c.oid,'DELETE')
                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname||'.'||c.relname=:table
                """),
                    {"table": table},
                ).scalar_one()
            assert not connection.execute(
                text("""
                  SELECT has_function_privilege(current_user,p.oid,'EXECUTE')
                  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                  WHERE n.nspname='research' AND p.proname='append_evidence_bundle'
            """)
            ).scalar_one()
            assert not connection.execute(
                text("""
              SELECT has_function_privilege(current_user,p.oid,'EXECUTE')
              FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
              WHERE n.nspname='research' AND p.proname='record_import_rejection'
            """)
            ).scalar_one()
            assert not connection.execute(
                text("""
                  SELECT has_function_privilege(current_user,p.oid,'EXECUTE')
                  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                  WHERE n.nspname='research' AND p.proname='append_trust_decision'
            """)
            ).scalar_one()
        try:
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL ROLE " + role))
                connection.execute(
                    text("SELECT research.append_evidence_bundle(NULL,NULL,NULL,NULL,NULL)")
                )
        except DBAPIError:
            pass
        else:
            raise AssertionError("runtime certified evidence write allowed")
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_evidence_importer"))
        assert not connection.execute(
            text("""
          SELECT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S') AND (
              has_table_privilege(current_user,c.oid,'SELECT') OR
              has_table_privilege(current_user,c.oid,'INSERT') OR
              has_table_privilege(current_user,c.oid,'UPDATE') OR
              has_table_privilege(current_user,c.oid,'DELETE')))
        """)
        ).scalar_one()
        assert not connection.execute(
            text("""
          SELECT EXISTS(SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.prorettype<>'trigger'::regtype
              AND has_function_privilege(current_user,p.oid,'EXECUTE'))
        """)
        ).scalar_one()
        for table in (
            "research.collection_plans",
            "research.due_jobs",
            "research.ingestion_jobs",
            "backtest.readiness_jobs",
            "backtest.readiness_reports",
        ):
            assert not connection.execute(
                text("""
              SELECT has_table_privilege(current_user,c.oid,'INSERT') OR
                has_table_privilege(current_user,c.oid,'UPDATE') OR
                has_table_privilege(current_user,c.oid,'DELETE')
              FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname||'.'||c.relname=:table
            """),
                {"table": table},
            ).scalar_one()
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atl_backtest_worker"))
        assert not connection.execute(
            text("""
          SELECT has_table_privilege(current_user,'research.collection_plans','INSERT') OR
            has_table_privilege(current_user,'research.collection_plans','UPDATE')
        """)
        ).scalar_one()


def test_readiness_roles_actual_postgresql():
    from tests.integration.test_readiness_storage import host

    assert host().endswith("/roles/restore")
