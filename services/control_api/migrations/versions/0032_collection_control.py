"""RDR-06: kontrollierter Planport unter derselben dauerhaften Dispatchbarriere."""

from alembic import op

from packages.research.registry import definitions

revision = "0032_collection_control"
down_revision = "0031_readiness_jobs"
branch_labels = None
depends_on = None


def upgrade():
    allowed = ",".join(f"'{instrument.instrument_id}'::uuid" for instrument, _ in definitions())
    op.execute("""
    CREATE TABLE research.collection_commands (
      actor text NOT NULL CHECK(actor='local-operator'),instrument_id uuid NOT NULL,
      idempotency_key text NOT NULL,request_body jsonb NOT NULL,response jsonb NOT NULL,
      trace_id text NOT NULL CHECK(trace_id ~ '^[0-9a-f]{32}$'),
      recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(actor,instrument_id,idempotency_key));
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON research.collection_commands
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    REVOKE ALL ON research.collection_commands FROM PUBLIC;
    CREATE INDEX collection_receipt_scope ON research.observation_receipts
      (instrument_id,state,ingested_at);
    CREATE INDEX collection_receipt_dispatch ON research.observation_receipts(dispatch_id,state);
    CREATE INDEX collection_receipt_bars ON research.observation_receipts
      (instrument_id,event_at,bar_close_at,commit_version);
    CREATE INDEX collection_due_scope ON research.due_jobs(instrument_id,state,due_at);
    CREATE FUNCTION research.collection_plan_projection(p_id uuid) RETURNS jsonb LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,research,backtest,pg_temp AS $$
    DECLARE plan research.collection_plans; source research.source_status; symbol text;
      result jsonb; gaps jsonb; gap_count integer; active_count integer; last_attempt timestamptz;
      last_job uuid; enabled_global boolean; plan_status text; reasons text[]:='{}';
    BEGIN
      IF p_id NOT IN (""" + allowed + """) OR NOT EXISTS(
        SELECT FROM research.instruments WHERE instrument_id=p_id) THEN
        RAISE EXCEPTION USING ERRCODE='P0002',MESSAGE='NOT_FOUND'; END IF;
      SELECT base_unit INTO symbol FROM research.instruments WHERE instrument_id=p_id;
      SELECT * INTO plan FROM research.collection_plans WHERE instrument_id=p_id;
      SELECT * INTO source FROM research.source_status WHERE singleton;
      enabled_global:=COALESCE(source.state<>'DISABLED',false);
      plan_status:=COALESCE(plan.status,'DISABLED');
      IF plan.enabled THEN
        IF NOT enabled_global THEN plan_status:='BLOCKED';
          reasons:=array_append(reasons,'GLOBAL_DISABLED'); END IF;
        IF source.license_status IS DISTINCT FROM 'CONFIRMED' THEN plan_status:='BLOCKED';
          reasons:=array_append(reasons,'RIGHTS_UNVERIFIED'); END IF;
        IF source.state='BLOCKED' THEN plan_status:='BLOCKED';
          reasons:=array_append(reasons,'SOURCE_BLOCKED'); END IF;
      END IF;
      IF plan.reason IS NOT NULL AND NOT plan.reason=ANY(reasons)
        AND (plan.reason<>'OK' OR cardinality(reasons)=0) THEN
        reasons:=array_append(reasons,plan.reason); END IF;
      IF plan.instrument_id IS NULL THEN reasons:=ARRAY['DEFAULT_DISABLED']; END IF;
      SELECT count(*) INTO active_count FROM research.observation_receipts r
        WHERE r.instrument_id=p_id AND r.state='STARTED' AND NOT EXISTS(
          SELECT FROM research.observation_receipts t WHERE t.dispatch_id=r.receipt_id
            AND t.state IN ('COMPLETED','UNKNOWN','BLOCKED'));
      SELECT max(ingested_at) INTO last_attempt FROM research.observation_receipts
        WHERE instrument_id=p_id AND state='STARTED';
      SELECT job_id INTO last_job FROM research.observation_receipts
        WHERE instrument_id=p_id ORDER BY ingested_at DESC,receipt_id DESC LIMIT 1;
      SELECT count(*) INTO gap_count FROM research.due_jobs
        WHERE instrument_id=p_id AND state IN ('BLOCKED','UNKNOWN');
      SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'period',jsonb_build_object('start',to_char(start_at AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'end',to_char(end_at AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')),
        'reason',CASE WHEN reason IN ('RECOVERY_WINDOW_EXCEEDED','OUTSIDE_RECOVERY_WINDOW')
          THEN 'OUTSIDE_RECOVERY_WINDOW' WHEN reason IN ('CALENDAR_UNKNOWN','BAR_MISSING',
          'OBSERVED_TOO_LATE','DAILY_BUDGET_EXHAUSTED') THEN reason ELSE 'UNKNOWN_DISPATCH' END,
        'references','[]'::jsonb) ORDER BY due_at,due_id),'[]'::jsonb) INTO gaps
        FROM (SELECT * FROM research.due_jobs WHERE instrument_id=p_id
          AND state IN ('BLOCKED','UNKNOWN') ORDER BY due_at,due_id LIMIT 200) bounded;
      result:=jsonb_build_object('instrumentId',p_id::text,'symbol',symbol,'provider','alpaca',
        'feed','iex','interval','15Min','adjustment','raw','enabled',COALESCE(plan.enabled,false),
        'version',COALESCE(plan.version,0),'generation',COALESCE(plan.generation,0),
        'status',plan_status,'reasons',to_jsonb(reasons),'providerRights',jsonb_build_object(
          'licenseStatus',COALESCE(source.license_status,'UNKNOWN'),'providerVerification','UNVERIFIED',
          'attestationRef',NULL,'reason',CASE WHEN source.license_status='CONFIRMED'
            THEN 'LOCAL_OPERATOR_ATTESTATION' ELSE 'RIGHTS_UNVERIFIED' END),
        'globalEnabled',enabled_global,'nextDueAt',CASE WHEN plan.next_due_at IS NULL THEN NULL
          ELSE to_char(plan.next_due_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
        'lastAttemptAt',CASE WHEN last_attempt IS NULL THEN NULL ELSE to_char(last_attempt
          AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,'lastJobId',last_job::text,
        'outstandingStartedDispatches',active_count,'gaps',gaps,'gapsComplete',gap_count<=200,
        'updatedAt',CASE WHEN plan.updated_at IS NULL THEN NULL ELSE to_char(plan.updated_at
          AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
        'schedule',jsonb_build_object('calendarRefreshUtc','00:05:00Z','calendarLookaheadDays',7,
          'firstBarDelaySeconds',5,'reobserveDelaySeconds',240,'tickSeconds',5,'recoveryHours',24,
          'overlapMinutes',30,'maxDuePerUtcDay',100,'maxOpenJobsTotal',2,'maxConcurrentHttpGets',1));
      RETURN result;
    END $$;
    CREATE FUNCTION research.set_collection_plan(p_id uuid,p_enabled boolean,p_expected bigint,
      p_actor text,p_key text,p_trace text,p_stats jsonb) RETURNS jsonb LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,research,backtest,pg_temp AS $$
    DECLARE previous research.collection_commands; plan research.collection_plans;
      source research.source_status; requested jsonb; result jsonb;
      next_status text; next_reason text;
    BEGIN
      PERFORM set_config('lock_timeout','1s',true);
      IF (CASE current_setting('role') WHEN 'none' THEN session_user
        ELSE current_setting('role') END) NOT IN ('atl_control_api','atl_paper_control_api')
        OR p_actor IS DISTINCT FROM 'local-operator' THEN
        RAISE EXCEPTION USING ERRCODE='42501',MESSAGE='FORBIDDEN'; END IF;
      PERFORM research.collection_plan_projection(p_id);
      IF p_enabled IS NULL OR p_expected IS NULL OR p_expected NOT BETWEEN 0 AND 9007199254740991
        OR p_key IS NULL OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$'
        OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$'
        OR jsonb_typeof(p_stats) IS DISTINCT FROM 'object'
        OR NOT p_stats ?& ARRAY['metrics','windows'] OR octet_length(p_stats::text)>12582912
        OR EXISTS(SELECT FROM jsonb_object_keys(p_stats) k
          WHERE k NOT IN ('metrics','windows')) THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='INVALID_REQUEST'; END IF;
      requested:=jsonb_build_object('enabled',p_enabled,'expectedVersion',p_expected);
      INSERT INTO research.collection_plans(instrument_id) VALUES(p_id) ON CONFLICT DO NOTHING;
      SELECT * INTO plan FROM research.collection_plans WHERE instrument_id=p_id FOR UPDATE;
      SELECT * INTO previous FROM research.collection_commands
        WHERE actor=p_actor AND instrument_id=p_id AND idempotency_key=p_key;
      IF FOUND THEN
        IF previous.request_body<>requested THEN
          RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='IDEMPOTENCY_CONFLICT'; END IF;
        RETURN previous.response;
      END IF;
      IF plan.version<>p_expected OR plan.version>=9007199254740991
        OR plan.generation>=9007199254740991 THEN
        RAISE EXCEPTION USING ERRCODE='P0001',MESSAGE='VERSION_CONFLICT'; END IF;
      SELECT * INTO source FROM research.source_status WHERE singleton;
      next_status:='PAUSED';next_reason:='OPERATOR_PAUSED';
      IF p_enabled THEN
        next_status:='ACTIVE';next_reason:='OK';
        IF source.state IS NULL OR source.state='DISABLED' THEN
          next_status:='BLOCKED';next_reason:='GLOBAL_DISABLED';
        ELSIF source.license_status IS DISTINCT FROM 'CONFIRMED' THEN
          next_status:='BLOCKED';next_reason:='RIGHTS_UNVERIFIED';
        ELSIF source.state='BLOCKED' THEN
          next_status:='BLOCKED';next_reason:='SOURCE_BLOCKED'; END IF;
      END IF;
      UPDATE research.collection_plans SET enabled=p_enabled,version=version+1,
        generation=generation+1,status=next_status,reason=next_reason,
        next_due_at=CASE WHEN p_enabled AND next_status='ACTIVE'
          THEN clock_timestamp() ELSE NULL END,
        updated_at=clock_timestamp(),scheduled_through_at=NULL
        WHERE instrument_id=p_id;
      SELECT * INTO plan FROM research.collection_plans WHERE instrument_id=p_id;
      UPDATE research.due_jobs SET state='BLOCKED',reason='PLAN_GENERATION_RETIRED',
        owner=NULL,lease_until=NULL WHERE instrument_id=p_id AND generation<plan.generation
        AND state='PENDING';
      INSERT INTO research.plan_revisions(revision_id,instrument_id,version,generation,enabled,
        status,reason,next_due_at,idempotency_key,request_hash) VALUES(gen_random_uuid(),p_id,
        plan.version,plan.generation,p_enabled,next_status,next_reason,plan.next_due_at,p_key,
        encode(sha256(convert_to(requested::text,'UTF8')),'hex'));
      result:=research.collection_plan_projection(p_id)||p_stats;
      INSERT INTO research.collection_commands(actor,instrument_id,idempotency_key,request_body,
        response,trace_id) VALUES(p_actor,p_id,p_key,requested,result,p_trace);
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION research.collection_plan_projection(uuid),
      research.set_collection_plan(uuid,boolean,bigint,text,text,text,jsonb) FROM PUBLIC;
    GRANT SELECT ON research.collection_commands TO atl_control_api;
    GRANT EXECUTE ON FUNCTION research.collection_plan_projection(uuid),
      research.set_collection_plan(uuid,boolean,bigint,text,text,text,jsonb) TO atl_control_api;
    DO $$ BEGIN IF current_database()='lab_paper_runtime' THEN
      REVOKE ALL ON research.collection_commands FROM atl_control_api;
      REVOKE EXECUTE ON FUNCTION research.collection_plan_projection(uuid),
        research.set_collection_plan(uuid,boolean,bigint,text,text,text,jsonb) FROM atl_control_api;
      GRANT SELECT ON research.collection_commands TO atl_paper_control_api;
      GRANT EXECUTE ON FUNCTION research.collection_plan_projection(uuid),
        research.set_collection_plan(uuid,boolean,bigint,text,text,text,jsonb)
        TO atl_paper_control_api;
    END IF; END $$;
    """)


def downgrade():
    raise RuntimeError("Dauerhafte Planentscheidungen und Replayantworten müssen erhalten bleiben")
