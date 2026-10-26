"""Sanitized append-only Research job audit; no trading ledger changes."""

from alembic import op

revision = "0026_research_observability"
down_revision = "0025_research_operator_resume"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE research.job_audit (
      audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      job_id uuid NOT NULL REFERENCES research.ingestion_jobs(job_id),
      trace_id text NOT NULL CHECK(trace_id ~ '^[0-9a-f]{32}$'),
      status text NOT NULL CHECK(status IN
        ('ACCEPTED','RUNNING','RETRY_WAIT','PARTIAL','BLOCKED','SUCCEEDED','FAILED')),
      error_code text CHECK(error_code IN ('INVALID_REQUEST','UNAVAILABLE','NOT_IMPLEMENTED',
        'BLOCKED_CREDENTIALS','BLOCKED_LICENSE','BLOCKED_ENTITLEMENT','PROVIDER_TIMEOUT',
        'PROVIDER_RATE_LIMIT','PROVIDER_UNAVAILABLE','PROVIDER_RESPONSE_INVALID',
        'PAGINATION_CYCLE','BUDGET_EXHAUSTED','LEASE_LOST','CURSOR_INVALID')),
      pages integer NOT NULL CHECK(pages>=0),
      bars integer NOT NULL CHECK(bars>=0),
      attempts integer NOT NULL CHECK(attempts>=0),
      fence bigint NOT NULL CHECK(fence>=0),
      accepted_at timestamptz NOT NULL,
      updated_at timestamptz NOT NULL,
      retry_at timestamptz,
      recorded_at timestamptz NOT NULL DEFAULT clock_timestamp());
    CREATE INDEX job_audit_job_order ON research.job_audit(job_id,audit_id);
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON research.job_audit
      FOR EACH STATEMENT EXECUTE FUNCTION research.reject_mutation();
    CREATE FUNCTION research.audit_job_transition() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,research,pg_temp AS $$
    BEGIN
      IF TG_TABLE_SCHEMA<>'research' OR TG_TABLE_NAME<>'ingestion_jobs'
        OR TG_OP NOT IN ('INSERT','UPDATE') THEN
        RAISE EXCEPTION 'invalid research audit trigger';
      END IF;
      IF TG_OP='UPDATE' AND
        ROW(NEW.status,NEW.error_code,NEW.pages,NEW.bars,NEW.attempts,NEW.fence)
        IS NOT DISTINCT FROM
        ROW(OLD.status,OLD.error_code,OLD.pages,OLD.bars,OLD.attempts,OLD.fence) THEN
        RETURN NEW;
      END IF;
      INSERT INTO research.job_audit(job_id,trace_id,status,error_code,pages,bars,attempts,fence,
        accepted_at,updated_at,retry_at)
      VALUES(NEW.job_id,NEW.trace_id,NEW.status,
        CASE WHEN NEW.error_code IS NULL THEN NULL
          WHEN NEW.error_code IN ('INVALID_REQUEST','UNAVAILABLE','NOT_IMPLEMENTED',
            'BLOCKED_CREDENTIALS','BLOCKED_LICENSE','BLOCKED_ENTITLEMENT','PROVIDER_TIMEOUT',
            'PROVIDER_RATE_LIMIT','PROVIDER_UNAVAILABLE','PROVIDER_RESPONSE_INVALID',
            'PAGINATION_CYCLE','BUDGET_EXHAUSTED','LEASE_LOST','CURSOR_INVALID')
          THEN NEW.error_code ELSE 'UNAVAILABLE' END,
        NEW.pages,NEW.bars,NEW.attempts,NEW.fence,NEW.accepted_at,NEW.updated_at,NEW.retry_at);
      RETURN NEW;
    END $$;
    CREATE TRIGGER research_job_audit AFTER INSERT OR UPDATE ON research.ingestion_jobs
      FOR EACH ROW EXECUTE FUNCTION research.audit_job_transition();
    REVOKE ALL ON research.job_audit FROM PUBLIC,
      atl_control_api,atl_execution,atl_worker,atl_watchdog;
    REVOKE ALL ON SEQUENCE research.job_audit_audit_id_seq FROM PUBLIC,
      atl_control_api,atl_execution,atl_worker,atl_watchdog;
    REVOKE ALL ON FUNCTION research.audit_job_transition() FROM PUBLIC,
      atl_control_api,atl_execution,atl_worker,atl_watchdog;
    GRANT SELECT ON research.job_audit TO atl_control_api,atl_execution,atl_worker;
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        REVOKE ALL ON research.job_audit FROM
          atl_control_api,atl_execution,atl_worker,atl_watchdog,
          atl_paper_control_api,atl_paper_execution,atl_paper_worker,atl_paper_watchdog;
        REVOKE ALL ON SEQUENCE research.job_audit_audit_id_seq FROM
          atl_paper_control_api,atl_paper_execution,atl_paper_worker,atl_paper_watchdog;
        REVOKE ALL ON FUNCTION research.audit_job_transition() FROM
          atl_paper_control_api,atl_paper_execution,atl_paper_worker,atl_paper_watchdog;
        GRANT SELECT ON research.job_audit TO
          atl_paper_control_api,atl_paper_execution,atl_paper_worker;
      END IF;
    END $$;
    """)


def downgrade():
    raise RuntimeError("Research rollback preserves durable audit evidence")
