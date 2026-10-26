"""Isolated durable simulations; no grant to financial schemas or provider consumers."""

import secrets

from alembic import op
from sqlalchemy import text

revision = "0029_backtest_jobs"
down_revision = "0028_backtest_coverage"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='atl_backtest_worker') THEN
        CREATE ROLE atl_backtest_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
      END IF;
    END $$;
    CREATE SCHEMA IF NOT EXISTS backtest;
    REVOKE ALL ON SCHEMA backtest FROM PUBLIC;
    CREATE TABLE backtest.strategy_revisions (
      strategy_id text NOT NULL, version text NOT NULL CHECK(version='1'),
      parameters jsonb NOT NULL, PRIMARY KEY(strategy_id,version));
    INSERT INTO backtest.strategy_revisions VALUES
      ('flat-v1','1','{}'),('buy-hold-v1','1','{}'),
      ('sma-cross-v1','1','{"fastMinimum": 2,"slowMaximum": 100,"ordering": "fast < slow"}');
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.strategy_revisions
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    CREATE TABLE backtest.runs (
      run_id uuid PRIMARY KEY, actor text NOT NULL, idempotency_key text NOT NULL,
      request jsonb NOT NULL, request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
      snapshot_version bigint NOT NULL, snapshot_at timestamptz NOT NULL,
      code_version text NOT NULL CHECK(code_version ~ '^[0-9a-f]{40}$'),
      status text NOT NULL DEFAULT 'ACCEPTED' CHECK(status IN
        ('ACCEPTED','RUNNING','SUCCEEDED','BLOCKED','FAILED')),
      accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      error_code text, trace_id text NOT NULL CHECK(trace_id ~ '^[0-9a-f]{32}$'),
      owner uuid, fence bigint NOT NULL DEFAULT 0, attempts integer NOT NULL DEFAULT 0
        CHECK(attempts BETWEEN 0 AND 3),
      lease_until timestamptz, started_at timestamptz,
      checkpoint integer NOT NULL DEFAULT 0 CHECK(checkpoint>=0),
      UNIQUE(actor,idempotency_key));
    CREATE TABLE backtest.manifests (
      run_id uuid PRIMARY KEY REFERENCES backtest.runs,
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'), body jsonb NOT NULL);
    CREATE TABLE backtest.fold_results (
      run_id uuid NOT NULL REFERENCES backtest.runs, fold_index integer NOT NULL,
      body jsonb NOT NULL, PRIMARY KEY(run_id,fold_index));
    CREATE TABLE backtest.results (
      run_id uuid PRIMARY KEY REFERENCES backtest.runs,
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'), body jsonb NOT NULL);
    CREATE TABLE backtest.audit (
      event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      run_id uuid NOT NULL REFERENCES backtest.runs, event text NOT NULL
        CHECK(event IN ('ACCEPTED','RUNNING','CHECKPOINT','SUCCEEDED','BLOCKED','FAILED')),
      fence bigint NOT NULL, trace_id text NOT NULL CHECK(trace_id ~ '^[0-9a-f]{32}$'),
      observed_at timestamptz NOT NULL DEFAULT clock_timestamp());
    CREATE INDEX backtest_queue ON backtest.runs(status,accepted_at,run_id);
    CREATE TABLE backtest.cursor_state (
      singleton boolean PRIMARY KEY CHECK(singleton),
      signing_key bytea NOT NULL CHECK(octet_length(signing_key)>=32));
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.manifests
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.fold_results
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.results
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.audit
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    REVOKE ALL ON ALL TABLES IN SCHEMA backtest FROM PUBLIC;
    GRANT USAGE ON SCHEMA research,backtest TO atl_backtest_worker;
    GRANT SELECT ON research.version_state,research.instruments,research.symbol_mappings,
      research.bar_revisions,research.calendar_versions,research.quality_observations
      TO atl_backtest_worker;
    GRANT SELECT ON ALL TABLES IN SCHEMA backtest TO atl_backtest_worker;
    GRANT INSERT,UPDATE ON backtest.runs TO atl_backtest_worker;
    GRANT INSERT ON backtest.manifests,backtest.fold_results,backtest.results,backtest.audit
      TO atl_backtest_worker;
    GRANT USAGE ON ALL SEQUENCES IN SCHEMA backtest TO atl_backtest_worker;
    GRANT USAGE ON SCHEMA backtest TO atl_control_api;
    GRANT SELECT ON backtest.runs,backtest.manifests,backtest.fold_results,backtest.results
      TO atl_control_api;
    REVOKE ALL ON backtest.cursor_state FROM PUBLIC,atl_backtest_worker;
    GRANT SELECT ON backtest.cursor_state TO atl_control_api;
    CREATE FUNCTION backtest.request_run(p_id uuid,p_actor text,p_key text,p_request jsonb,
      p_hash text,p_code text,p_trace text) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE previous backtest.runs;
    BEGIN
      PERFORM set_config('lock_timeout','1s',true);
      IF p_actor IS NULL OR p_actor<>'local-operator' OR p_key IS NULL
        OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$' OR p_hash IS NULL
        OR p_hash !~ '^[0-9a-f]{64}$' OR p_code IS NULL OR p_code !~ '^[0-9a-f]{40}$'
        OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$'
        OR jsonb_typeof(p_request)<>'object' THEN
        RAISE EXCEPTION 'invalid backtest request';
      END IF;
      PERFORM pg_advisory_xact_lock(894217345);
      SELECT * INTO previous FROM backtest.runs WHERE actor=p_actor AND idempotency_key=p_key;
      IF FOUND THEN
        IF previous.request_hash<>p_hash OR previous.request<>p_request THEN
          RAISE EXCEPTION 'backtest idempotency conflict';
        END IF;
        RETURN previous.run_id;
      END IF;
      IF (SELECT count(*) FROM backtest.runs WHERE status IN ('ACCEPTED','RUNNING'))>=100 THEN
        RAISE EXCEPTION 'backtest queue full';
      END IF;
      INSERT INTO backtest.runs(run_id,actor,idempotency_key,request,request_hash,
        snapshot_version,snapshot_at,code_version,trace_id)
      SELECT p_id,p_actor,p_key,p_request,p_hash,version,clock_timestamp(),p_code,p_trace
        FROM research.version_state WHERE singleton;
      INSERT INTO backtest.audit(run_id,event,fence,trace_id) VALUES(p_id,'ACCEPTED',0,p_trace);
      RETURN p_id;
    END $$;
    REVOKE ALL ON FUNCTION backtest.request_run(uuid,text,text,jsonb,text,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION backtest.request_run(uuid,text,text,jsonb,text,text,text)
      TO atl_control_api;
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        REVOKE ALL ON SCHEMA backtest FROM atl_control_api;
        REVOKE ALL ON ALL TABLES IN SCHEMA backtest FROM atl_control_api;
        REVOKE EXECUTE ON FUNCTION backtest.request_run(uuid,text,text,jsonb,text,text,text)
          FROM atl_control_api;
        GRANT USAGE ON SCHEMA backtest TO atl_paper_control_api;
        GRANT SELECT ON backtest.runs,backtest.manifests,backtest.fold_results,backtest.results
          TO atl_paper_control_api;
        GRANT SELECT ON backtest.cursor_state TO atl_paper_control_api;
        GRANT EXECUTE ON FUNCTION backtest.request_run(uuid,text,text,jsonb,text,text,text)
          TO atl_paper_control_api;
      END IF;
    END $$;
    """)
    op.get_bind().execute(
        text("INSERT INTO backtest.cursor_state(singleton,signing_key) VALUES(true,:key)"),
        {"key": secrets.token_bytes(32)},
    )


def downgrade():
    raise RuntimeError("Disable simulation dispatch; preserve immutable experiment evidence")
