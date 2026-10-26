"""Idempotent local-operator resume requests and source-status admission gates."""

from alembic import op

revision = "0025_research_operator_resume"
down_revision = "0024_research_runtime_metadata"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE research.resume_requests (
      actor text NOT NULL CHECK(actor='local-operator'),
      idempotency_key text NOT NULL CHECK(idempotency_key ~ '^[A-Za-z0-9._:-]{16,128}$'),
      job_id uuid NOT NULL REFERENCES research.ingestion_jobs,
      trace_id text NOT NULL CHECK(trace_id ~ '^[0-9a-f]{32}$'),
      requested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(actor,idempotency_key));
    REVOKE ALL ON research.resume_requests FROM PUBLIC;
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON research.resume_requests
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();

    CREATE FUNCTION research.gate_operator_job() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,research,pg_temp AS $$
    DECLARE status_row research.source_status;
    BEGIN
      IF TG_OP='INSERT' AND current_setting('role',true) IN
        ('atl_control_api','atl_paper_control_api') AND EXISTS (
          SELECT 1 FROM research.resume_requests r
          WHERE r.actor=NEW.actor AND r.idempotency_key=NEW.idempotency_key) THEN
        RAISE EXCEPTION 'research idempotency conflict';
      END IF;
      IF NEW.status<>'ACCEPTED' OR current_setting('role',true) NOT IN
        ('atl_control_api','atl_paper_control_api') THEN RETURN NEW; END IF;
      SELECT * INTO status_row FROM research.source_status WHERE singleton;
      IF NOT FOUND OR status_row.state='DISABLED' THEN
        NEW.status:='BLOCKED'; NEW.error_code:='UNAVAILABLE';
      ELSIF status_row.license_status<>'CONFIRMED' THEN
        NEW.status:='BLOCKED'; NEW.error_code:='BLOCKED_LICENSE';
      ELSIF status_row.state='BLOCKED' THEN
        NEW.status:='BLOCKED'; NEW.error_code:='BLOCKED_CREDENTIALS';
      ELSE
        NEW.error_code:=NULL;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION research.gate_operator_job() FROM PUBLIC;
    CREATE TRIGGER operator_admission BEFORE INSERT OR UPDATE ON research.ingestion_jobs
      FOR EACH ROW EXECUTE FUNCTION research.gate_operator_job();

    CREATE FUNCTION research.resume_job(p_job uuid,p_actor text,p_key text,p_trace text)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,research,pg_temp AS $$
    DECLARE previous research.resume_requests; current_job research.ingestion_jobs;
    BEGIN
      PERFORM set_config('lock_timeout','1s',true);
      IF p_actor IS NULL OR p_actor<>'local-operator' OR p_key IS NULL
        OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$' OR p_trace IS NULL
        OR p_trace !~ '^[0-9a-f]{32}$' OR p_job IS NULL THEN
        RAISE EXCEPTION 'invalid research request';
      END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended(p_actor||':'||p_key,0));
      IF EXISTS(SELECT 1 FROM research.ingestion_jobs
        WHERE actor=p_actor AND idempotency_key=p_key) THEN
        RAISE EXCEPTION 'research idempotency conflict';
      END IF;
      SELECT * INTO previous FROM research.resume_requests
        WHERE actor=p_actor AND idempotency_key=p_key;
      IF FOUND THEN
        IF previous.job_id<>p_job THEN RAISE EXCEPTION 'research idempotency conflict'; END IF;
        RETURN previous.job_id;
      END IF;
      SELECT * INTO current_job FROM research.ingestion_jobs WHERE job_id=p_job FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'research job not found'; END IF;
      IF current_job.status NOT IN ('PARTIAL','BLOCKED') THEN
        RAISE EXCEPTION 'research job cannot be resumed';
      END IF;
      INSERT INTO research.resume_requests(actor,idempotency_key,job_id,trace_id)
        VALUES(p_actor,p_key,p_job,p_trace);
      UPDATE research.ingestion_jobs SET status='ACCEPTED',attempts=0,error_code=NULL,
        started_at=NULL,run_pages=0,retry_at=NULL,owner=NULL,lease_until=NULL,
        updated_at=clock_timestamp() WHERE job_id=p_job;
      RETURN p_job;
    END $$;
    REVOKE ALL ON FUNCTION research.resume_job(uuid,text,text,text) FROM PUBLIC;
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        REVOKE ALL ON FUNCTION research.resume_job(uuid,text,text,text) FROM atl_control_api;
        GRANT EXECUTE ON FUNCTION research.resume_job(uuid,text,text,text) TO atl_paper_control_api;
      ELSE
        GRANT EXECUTE ON FUNCTION research.resume_job(uuid,text,text,text) TO atl_control_api;
      END IF;
    END $$;
    """)


def downgrade():
    raise RuntimeError("Research rollback preserves durable resume audit and state")
