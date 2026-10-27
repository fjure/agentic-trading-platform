"""RDR-05: begrenzte Diagnosequeue und gemeinsame gefencte Workerzulassung."""

from alembic import op

revision = "0031_readiness_jobs"
down_revision = "0030_readiness_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE backtest.readiness_jobs ADD CONSTRAINT readiness_job_versions CHECK(
      code_version ~ '^[0-9a-f]{40}$' AND snapshot_version>=0 AND trust_version>=0
      AND fence>=0 AND (status NOT IN ('SUCCEEDED','FAILED') OR owner IS NULL));
    CREATE TABLE backtest.readiness_audit (
      event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      assessment_id uuid NOT NULL REFERENCES backtest.readiness_jobs,
      event text NOT NULL CHECK(event IN ('ACCEPTED','RUNNING','TRUST_BOUND','SUCCEEDED','FAILED')),
      fence bigint NOT NULL,trace_id text NOT NULL CHECK(trace_id ~ '^[0-9a-f]{32}$'),
      observed_at timestamptz NOT NULL DEFAULT clock_timestamp());
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.readiness_audit
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    REVOKE ALL ON backtest.readiness_audit FROM PUBLIC;
    GRANT SELECT,INSERT ON backtest.readiness_audit TO atl_backtest_worker;
    GRANT USAGE ON SEQUENCE backtest.readiness_audit_event_id_seq TO atl_backtest_worker;
    CREATE TABLE backtest.readiness_bindings (
      assessment_id uuid NOT NULL REFERENCES backtest.readiness_reports,
      kind text NOT NULL,reference text NOT NULL CHECK(length(reference)<=128),
      PRIMARY KEY(assessment_id,kind,reference));
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.readiness_bindings
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    REVOKE ALL ON backtest.readiness_bindings FROM PUBLIC;
    GRANT SELECT ON backtest.readiness_bindings TO atl_control_api,atl_backtest_worker;
    GRANT INSERT ON backtest.readiness_bindings TO atl_backtest_worker;

    CREATE FUNCTION backtest.request_readiness(p_id uuid,p_actor text,p_key text,
      p_request jsonb,p_hash text,p_code text,p_policy text,p_trace text)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE previous backtest.readiness_jobs;
    BEGIN
      PERFORM set_config('lock_timeout','1s',true);
      IF p_actor IS NULL OR p_actor<>'local-operator' OR p_key IS NULL
        OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$' OR p_hash IS NULL
        OR p_hash !~ '^[0-9a-f]{64}$' OR p_code IS NULL OR p_code !~ '^[0-9a-f]{40}$'
        OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$'
        OR p_policy IS DISTINCT FROM 'backtest-data-readiness-v1'
        OR jsonb_typeof(p_request) IS DISTINCT FROM 'object'
        OR octet_length(p_request::text)>16777216 THEN
        RAISE EXCEPTION 'invalid readiness request';
      END IF;
      PERFORM pg_advisory_xact_lock(894217347);
      SELECT * INTO previous FROM backtest.readiness_jobs
        WHERE actor=p_actor AND idempotency_key=p_key;
      IF FOUND THEN
        IF previous.request_hash<>p_hash OR previous.request<>p_request THEN
          RAISE EXCEPTION 'readiness idempotency conflict';
        END IF;
        RETURN previous.assessment_id;
      END IF;
      IF (SELECT count(*) FROM backtest.readiness_jobs WHERE status='ACCEPTED')>=32 THEN
        RAISE EXCEPTION 'readiness queue full';
      END IF;
      INSERT INTO backtest.readiness_jobs(assessment_id,actor,idempotency_key,request,
        request_hash,policy_version,code_version,snapshot_version,snapshot_at,
        trust_version,trust_policy_version,trace_id)
      SELECT p_id,p_actor,p_key,p_request,p_hash,p_policy,p_code,v.version,clock_timestamp(),
        t.version,t.policy_version,p_trace
        FROM research.version_state v CROSS JOIN research.trust_version_state t
        WHERE v.singleton AND t.singleton;
      IF NOT FOUND THEN RAISE EXCEPTION 'readiness state unavailable'; END IF;
      INSERT INTO backtest.readiness_audit(assessment_id,event,fence,trace_id)
        VALUES(p_id,'ACCEPTED',0,p_trace);
      RETURN p_id;
    END $$;
    REVOKE ALL ON FUNCTION backtest.request_readiness(uuid,text,text,jsonb,text,text,text,text)
      FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION backtest.request_readiness(uuid,text,text,jsonb,text,text,text,text)
      TO atl_control_api;
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        REVOKE EXECUTE ON FUNCTION
          backtest.request_readiness(uuid,text,text,jsonb,text,text,text,text) FROM atl_control_api;
        GRANT EXECUTE ON FUNCTION
          backtest.request_readiness(uuid,text,text,jsonb,text,text,text,text)
          TO atl_paper_control_api;
        REVOKE SELECT ON backtest.readiness_bindings FROM atl_control_api;
        GRANT SELECT ON backtest.readiness_bindings TO atl_paper_control_api;
      END IF;
    END $$;

    CREATE FUNCTION backtest.guard_shared_worker() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE self_id uuid;
    BEGIN
      IF NEW.status<>'RUNNING' THEN RETURN NEW; END IF;
      IF TG_OP='UPDATE' AND OLD.status='RUNNING' AND NEW.owner IS NOT DISTINCT FROM OLD.owner
        AND NEW.fence=OLD.fence THEN RETURN NEW; END IF;
      PERFORM pg_advisory_xact_lock(894217346);
      IF TG_TABLE_NAME='runs' THEN self_id:=NEW.run_id;
      ELSE self_id:=NEW.assessment_id; END IF;
      IF EXISTS(SELECT FROM backtest.runs WHERE status='RUNNING'
        AND lease_until>clock_timestamp()
        AND (TG_TABLE_NAME<>'runs' OR run_id<>self_id))
        OR EXISTS(SELECT FROM backtest.readiness_jobs WHERE status='RUNNING'
        AND lease_until>clock_timestamp()
        AND (TG_TABLE_NAME<>'readiness_jobs' OR assessment_id<>self_id)) THEN
        RAISE EXCEPTION 'shared backtest worker busy';
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION backtest.guard_shared_worker() FROM PUBLIC;
    CREATE TRIGGER shared_worker BEFORE INSERT OR UPDATE ON backtest.runs
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_shared_worker();
    CREATE TRIGGER shared_worker BEFORE INSERT OR UPDATE ON backtest.readiness_jobs
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_shared_worker();
    """)


def downgrade():
    raise RuntimeError("Disable readiness/backtest dispatch; preserve immutable evidence")
