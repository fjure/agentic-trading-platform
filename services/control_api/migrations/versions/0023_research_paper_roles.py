"""Confine Research in the isolated Paper deployment to its existing runtime roles."""

from alembic import op

revision = "0023_research_paper_roles"
down_revision = "0022_research_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        REVOKE ALL ON SCHEMA research FROM atl_execution,atl_worker,atl_control_api,atl_watchdog;
        REVOKE ALL ON ALL TABLES IN SCHEMA research
          FROM atl_execution,atl_worker,atl_control_api,atl_watchdog;
        REVOKE ALL ON ALL FUNCTIONS IN SCHEMA research
          FROM atl_execution,atl_worker,atl_control_api,atl_watchdog;
        GRANT USAGE ON SCHEMA research
          TO atl_paper_execution,atl_paper_worker,atl_paper_control_api;
        GRANT SELECT ON research.version_state,research.instruments,research.symbol_mappings,
          research.bar_revisions,research.evidence,research.calendar_versions,
          research.job_projection TO atl_paper_execution,atl_paper_worker,atl_paper_control_api;
        GRANT INSERT ON research.instruments,research.symbol_mappings,research.bar_revisions,
          research.evidence,research.calendar_versions TO atl_paper_execution;
        GRANT SELECT,INSERT,UPDATE ON research.ingestion_jobs TO atl_paper_execution;
        GRANT SELECT,INSERT ON research.page_receipts TO atl_paper_execution;
        GRANT EXECUTE ON FUNCTION research.reserve_version() TO atl_paper_execution;
        GRANT EXECUTE ON FUNCTION research.request_job(uuid,text,text,text,uuid,timestamptz,
          timestamptz,text,text,text) TO atl_paper_control_api;
      END IF;
    END $$;
    """)


def downgrade():
    raise RuntimeError("Research rollback disables dispatch and preserves data")
