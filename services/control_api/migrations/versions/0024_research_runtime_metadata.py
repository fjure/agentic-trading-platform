"""Non-secret provider status and execution-only generation metadata."""

from alembic import op

revision = "0024_research_runtime_metadata"
down_revision = "0023_research_paper_roles"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE research.source_status (
      singleton boolean PRIMARY KEY CHECK(singleton),
      state text NOT NULL CHECK(state IN ('DISABLED','BLOCKED','IMPLEMENTED')),
      license_status text NOT NULL CHECK(license_status IN ('UNKNOWN','CONFIRMED','BLOCKED')),
      policy_version text CHECK(policy_version ~ '^[A-Za-z0-9_.-]{1,64}$'),
      provider_verification text NOT NULL
        CHECK(provider_verification IN ('UNVERIFIED','VERIFIED','BLOCKED')),
      observed_at timestamptz NOT NULL DEFAULT clock_timestamp());
    INSERT INTO research.source_status(singleton,state,license_status,provider_verification)
      VALUES(true,'DISABLED','UNKNOWN','UNVERIFIED');
    REVOKE ALL ON research.source_status FROM PUBLIC;
    GRANT SELECT ON research.source_status TO atl_control_api,atl_worker,atl_execution;
    GRANT UPDATE ON research.source_status TO atl_execution;
    CREATE FUNCTION research.credential_generation() RETURNS bigint
      LANGUAGE plpgsql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,public,research,pg_temp AS $$
    BEGIN
      IF current_database()<>'lab_paper_runtime'
        OR session_user<>'atl_paper_execution_login'
        OR current_setting('role')<>'atl_paper_execution' THEN RETURN NULL; END IF;
      RETURN (SELECT nullif(saved_generation,0) FROM public.paper_workflow_state WHERE singleton);
    END $$;
    REVOKE ALL ON FUNCTION research.credential_generation() FROM PUBLIC;
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        REVOKE ALL ON research.source_status FROM atl_control_api,atl_worker,atl_execution;
        GRANT SELECT ON research.source_status
          TO atl_paper_control_api,atl_paper_worker,atl_paper_execution;
        GRANT UPDATE ON research.source_status TO atl_paper_execution;
        GRANT EXECUTE ON FUNCTION research.credential_generation() TO atl_paper_execution;
      END IF;
    END $$;
    """)


def downgrade():
    raise RuntimeError("Research rollback preserves source audit metadata")
