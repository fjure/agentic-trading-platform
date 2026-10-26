"""Research-only durable requests, private resume state and public projection."""

from alembic import op

revision = "0022_research_jobs"
down_revision = "0021_research_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE research.ingestion_jobs (
      job_id uuid PRIMARY KEY, actor text NOT NULL, idempotency_key text NOT NULL,
      request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
      instrument_id uuid NOT NULL REFERENCES research.instruments,
      start_at timestamptz NOT NULL, end_at timestamptz NOT NULL,
      interval text NOT NULL CHECK(interval IN ('15Min','1Day')),
      mode text NOT NULL CHECK(mode IN ('BACKFILL','UPDATE')),
      status text NOT NULL CHECK(status IN
        ('ACCEPTED','RUNNING','RETRY_WAIT','PARTIAL','BLOCKED','SUCCEEDED','FAILED')),
      error_code text, accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      retry_at timestamptz, started_at timestamptz,
      overlap_start_at timestamptz,
      pages integer NOT NULL DEFAULT 0 CHECK(pages>=0),
      run_pages integer NOT NULL DEFAULT 0 CHECK(run_pages BETWEEN 0 AND 1000),
      bars integer NOT NULL DEFAULT 0 CHECK(bars>=0),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
      owner uuid, fence bigint NOT NULL DEFAULT 0, lease_until timestamptz,
      private_cursor text CHECK(length(private_cursor)<=1024),
      partition integer NOT NULL DEFAULT 0 CHECK(partition>=0),
      cursor_generation integer NOT NULL DEFAULT 0 CHECK(cursor_generation>=0),
      cursor_restarts integer NOT NULL DEFAULT 0 CHECK(cursor_restarts BETWEEN 0 AND 3),
      trace_id text NOT NULL CHECK(trace_id ~ '^[0-9a-f]{32}$'),
      UNIQUE(actor,idempotency_key),
      CHECK(end_at>start_at AND end_at-start_at<=interval '366 days'));
    CREATE TABLE research.page_receipts (
      job_id uuid NOT NULL REFERENCES research.ingestion_jobs,
      partition integer NOT NULL, cursor_generation integer NOT NULL DEFAULT 0,
      cursor_hash text NOT NULL,
      content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
      received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(job_id,partition,cursor_generation,cursor_hash));
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON research.page_receipts
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    CREATE VIEW research.job_projection AS SELECT job_id,instrument_id,start_at,end_at,
      interval,mode,status,error_code,accepted_at,updated_at,retry_at,pages,bars,attempts,trace_id
      FROM research.ingestion_jobs;
    REVOKE ALL ON research.ingestion_jobs,research.page_receipts,research.job_projection
      FROM PUBLIC;
    GRANT SELECT ON research.job_projection TO atl_control_api,atl_worker,atl_execution;
    GRANT SELECT,INSERT,UPDATE ON research.ingestion_jobs TO atl_execution;
    GRANT SELECT,INSERT ON research.page_receipts TO atl_execution;
    CREATE FUNCTION research.request_job(p_id uuid,p_actor text,p_key text,p_hash text,
      p_instrument uuid,p_start timestamptz,p_end timestamptz,
      p_interval text,p_mode text,p_trace text)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,research,pg_temp AS $$
    DECLARE previous research.ingestion_jobs;
    BEGIN
      PERFORM set_config('lock_timeout','1s',true);
      IF p_actor IS NULL OR p_actor<>'local-operator' OR p_key IS NULL
        OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$' OR p_hash IS NULL
        OR p_hash !~ '^[0-9a-f]{64}$' OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$'
        OR p_start IS NULL OR p_end IS NULL OR p_end<=p_start
        OR p_end-p_start>interval '366 days' OR p_interval NOT IN ('15Min','1Day')
        OR p_mode NOT IN ('BACKFILL','UPDATE') OR NOT EXISTS (
          SELECT 1 FROM research.instruments i JOIN research.symbol_mappings m USING(instrument_id)
          WHERE i.instrument_id=p_instrument AND i.asset_class='ETF' AND i.currency='USD'
            AND m.provider='alpaca' AND m.symbol IN ('SPY','QQQ')
          GROUP BY i.instrument_id
          HAVING range_agg(tstzrange(m.valid_from,m.valid_to,'[)')) @>
            tstzrange(p_start,p_end,'[)')) THEN
        RAISE EXCEPTION 'invalid research request';
      END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended(p_actor||':'||p_key,0));
      SELECT * INTO previous FROM research.ingestion_jobs
        WHERE actor=p_actor AND idempotency_key=p_key;
      IF FOUND THEN
        IF previous.request_hash<>p_hash OR previous.instrument_id<>p_instrument
          OR previous.start_at<>p_start OR previous.end_at<>p_end
          OR previous.interval<>p_interval OR previous.mode<>p_mode THEN
          RAISE EXCEPTION 'research idempotency conflict';
        END IF;
        RETURN previous.job_id;
      END IF;
      INSERT INTO research.ingestion_jobs(job_id,actor,idempotency_key,request_hash,
        instrument_id,start_at,end_at,interval,mode,status,trace_id)
      VALUES(p_id,p_actor,p_key,p_hash,p_instrument,p_start,p_end,p_interval,p_mode,'ACCEPTED',p_trace);
      RETURN p_id;
    END $$;
    REVOKE ALL ON FUNCTION research.request_job(uuid,text,text,text,uuid,timestamptz,
      timestamptz,text,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION research.request_job(uuid,text,text,text,uuid,timestamptz,
      timestamptz,text,text,text) TO atl_control_api;
    """)


def downgrade():
    raise RuntimeError("Research rollback preserves durable jobs and data")
