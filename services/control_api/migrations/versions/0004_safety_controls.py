"""Role-restricted safety controls and immutable recovery evidence.

Revision ID: 0004_safety_controls
Revises: 0003_risk_context
"""

from alembic import op

revision = "0004_safety_controls"
down_revision = "0003_risk_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE public.execution_owners (
      account_id varchar(255) PRIMARY KEY REFERENCES public.accounts(id) ON DELETE RESTRICT,
      owner_id varchar(255) NOT NULL CHECK (length(owner_id)>0),
      fencing_token bigint NOT NULL CHECK (fencing_token>0),
      claimed_at timestamptz NOT NULL,
      heartbeat_at timestamptz NOT NULL,
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$')
    )
    """)
    op.execute("""
    CREATE TABLE public.safety_resume_requests (
      id varchar(255) PRIMARY KEY,
      account_id varchar(255) NOT NULL REFERENCES public.accounts(id) ON DELETE RESTRICT,
      requested_at timestamptz NOT NULL,
      actor_id varchar(255) NOT NULL CHECK (length(actor_id)>0),
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$'),
      operation varchar(20) NOT NULL CHECK (operation IN ('RESUME','ACTIVATE_PAPER')),
      expected_policy_version integer NOT NULL CHECK (expected_policy_version>0),
      pause_context_version integer NOT NULL CHECK (pause_context_version>0),
      expires_at timestamptz NOT NULL,
      UNIQUE (account_id,id)
    )
    """)
    op.execute("""
    CREATE TABLE public.safety_health_evidence (
      id varchar(255) PRIMARY KEY,
      request_id varchar(255) NOT NULL UNIQUE
        REFERENCES public.safety_resume_requests(id) ON DELETE RESTRICT,
      account_id varchar(255) NOT NULL REFERENCES public.accounts(id) ON DELETE RESTRICT,
      checked_at timestamptz NOT NULL,
      expires_at timestamptz NOT NULL,
      owner_id varchar(255) NOT NULL,
      fencing_token bigint NOT NULL CHECK (fencing_token>0),
      policy_version integer NOT NULL CHECK (policy_version>0),
      context_version integer NOT NULL CHECK (context_version>0),
      portfolio_snapshot_id varchar(255) NOT NULL
        REFERENCES public.portfolio_snapshots(id) ON DELETE RESTRICT,
      broker_order_count integer NOT NULL CHECK (broker_order_count>=0),
      broker_position_count integer NOT NULL CHECK (broker_position_count>=0),
      actor_id varchar(255) NOT NULL CHECK (actor_id='execution-recovery'),
      trace_id varchar(32) NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$'),
      CHECK (expires_at>checked_at)
    )
    """)
    for table in ("safety_resume_requests", "safety_health_evidence"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
            f"ON public.{table} FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.ledger_reject_mutation()"
        )
    op.execute("""
    CREATE FUNCTION public.safety_latch(
      p_account varchar, p_state varchar, p_actor varchar, p_trace varchar, p_reason varchar
    ) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE c public.risk_account_context%ROWTYPE;
            v integer; pv integer; now_at timestamptz;
    BEGIN
      IF p_state IS NULL OR p_state NOT IN ('PAUSE_NEW','HALTED')
         OR p_actor IS NULL OR length(p_actor)=0 OR length(p_actor)>255
         OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$'
         OR p_reason IS NULL OR p_reason NOT IN ('PAUSED_NEW','KILL_SWITCH_ACTIVE',
           'HEALTH_CHECK_FAILED','TEMPORARILY_UNAVAILABLE',
           'VERSION_CONFLICT','OK','INVALID_REQUEST') THEN
        RAISE EXCEPTION 'invalid safety transition';
      END IF;
      PERFORM id FROM public.accounts WHERE id=p_account FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'account missing'; END IF;
      SELECT * INTO c FROM public.risk_account_context
        WHERE account_id=p_account FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'risk context missing'; END IF;
      now_at := clock_timestamp();
      IF c.safety_state='HALTED' THEN
        RETURN c.version;
      END IF;
      SELECT version INTO pv FROM public.risk_policies
        ORDER BY version DESC LIMIT 1;
      v := c.version+1;
      INSERT INTO public.audit_events
        (id,occurred_at,actor_id,action,reason_codes,entity_id,
         entity_version,trace_id,policy_version,outcome)
      VALUES (gen_random_uuid()::text,now_at,p_actor,'safety.latch',
        jsonb_build_array(p_reason),p_account,v,p_trace,pv,p_state);
      UPDATE public.risk_account_context SET version=v,
        safety_state=p_state,reconciliation_current=false,
        actor_id=p_actor,trace_id=p_trace WHERE account_id=p_account;
      INSERT INTO public.outbox_events
        (event_id,event_type,occurred_at,aggregate_id,aggregate_version,
         trace_id,schema_version,payload)
      VALUES (gen_random_uuid()::text,'safety.changed.v1',now_at,p_account,v,
        p_trace,1,jsonb_build_object('account_id',p_account,
        'action',CASE WHEN p_state='HALTED' THEN 'KILL' ELSE 'PAUSE' END,
        'mode',c.mode,'safety_state',p_state,'reason_code',p_reason,
        'policy_version',pv,'request_id',NULL));
      RETURN v;
    END $$
    """)
    op.execute("""
    CREATE FUNCTION public.safety_request_resume(
      p_account varchar,p_actor varchar,p_trace varchar,p_policy integer,
      p_operation varchar
    ) RETURNS varchar LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE c public.risk_account_context%ROWTYPE;
            new_request_id varchar; now_at timestamptz;
            pv integer;
    BEGIN
      IF p_actor IS NULL OR length(p_actor)=0 OR length(p_actor)>255
         OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$'
         OR p_policy IS NULL OR p_policy<=0
         OR p_operation IS NULL OR p_operation NOT IN ('RESUME','ACTIVATE_PAPER') THEN
        RAISE EXCEPTION 'invalid resume request';
      END IF;
      PERFORM a.id FROM public.accounts a WHERE a.id=p_account FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'account missing'; END IF;
      SELECT * INTO c FROM public.risk_account_context
        WHERE account_id=p_account FOR UPDATE;
      IF NOT FOUND OR c.safety_state<>'PAUSE_NEW' THEN
        RAISE EXCEPTION 'resume requires PAUSE_NEW';
      END IF;
      IF (p_operation='RESUME' AND c.mode<>'PAPER') OR
         (p_operation='ACTIVATE_PAPER' AND c.mode NOT IN
           ('DISABLED','REPLAY','SHADOW')) THEN
        RAISE EXCEPTION 'mode does not match explicit operation';
      END IF;
      SELECT version INTO pv FROM public.risk_policies
        ORDER BY version DESC LIMIT 1;
      IF pv IS DISTINCT FROM p_policy THEN RAISE EXCEPTION 'policy version changed'; END IF;
      now_at := clock_timestamp();
      new_request_id := gen_random_uuid()::text;
      INSERT INTO public.safety_resume_requests
        (id,account_id,requested_at,actor_id,trace_id,operation,expected_policy_version,
         pause_context_version,expires_at)
      VALUES (new_request_id,p_account,now_at,p_actor,p_trace,p_operation,p_policy,c.version,
              now_at+interval '30 seconds');
      INSERT INTO public.audit_events
        (id,occurred_at,actor_id,action,reason_codes,entity_id,
         entity_version,trace_id,policy_version,outcome)
      VALUES (gen_random_uuid()::text,now_at,p_actor,
        CASE WHEN p_operation='RESUME' THEN 'safety.resume_requested'
             ELSE 'safety.paper_activation_requested' END,
        jsonb_build_array('OK'),new_request_id,1,p_trace,p_policy,'REQUESTED');
      INSERT INTO public.outbox_events
        (event_id,event_type,occurred_at,aggregate_id,aggregate_version,
         trace_id,schema_version,payload)
      VALUES (gen_random_uuid()::text,'safety.resume_requested.v1',now_at,new_request_id,1,
        p_trace,1,jsonb_build_object('account_id',p_account,
        'action',CASE WHEN p_operation='RESUME' THEN 'RESUME_REQUESTED'
                      ELSE 'PAPER_ACTIVATION_REQUESTED' END,
        'mode',c.mode,'safety_state',c.safety_state,
        'reason_code','OK','policy_version',p_policy,'request_id',new_request_id));
      RETURN new_request_id;
    END $$
    """)
    op.execute("""
    CREATE FUNCTION public.safety_watchdog_poll(p_account varchar,p_trace varchar)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE c public.risk_account_context%ROWTYPE;
            beat timestamptz; now_at timestamptz;
    BEGIN
      IF p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$' THEN
        RAISE EXCEPTION 'invalid watchdog trace';
      END IF;
      PERFORM a.id FROM public.accounts a WHERE a.id=p_account FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'account missing'; END IF;
      SELECT * INTO c FROM public.risk_account_context
        WHERE account_id=p_account FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'risk context missing'; END IF;
      SELECT heartbeat_at INTO beat FROM public.execution_owners
        WHERE account_id=p_account;
      now_at := clock_timestamp();
      IF beat IS NOT NULL AND beat>=now_at-interval '30 seconds' AND beat<=now_at THEN
        RETURN true;
      END IF;
      IF c.safety_state='RUNNING' THEN
        PERFORM public.safety_latch(p_account,'PAUSE_NEW','watchdog',p_trace,
                                    'HEALTH_CHECK_FAILED');
      END IF;
      RETURN false;
    END $$
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.safety_latch"
        "(varchar,varchar,varchar,varchar,varchar) FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.safety_request_resume"
        "(varchar,varchar,varchar,integer,varchar) FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.safety_watchdog_poll(varchar,varchar) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.safety_latch(varchar,varchar,varchar,varchar,varchar) "
        "TO atl_control_api,atl_watchdog,atl_execution"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.safety_request_resume"
        "(varchar,varchar,varchar,integer,varchar) "
        "TO atl_control_api"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.safety_watchdog_poll(varchar,varchar) "
        "TO atl_watchdog"
    )
    op.execute(
        "REVOKE ALL ON public.execution_owners, public.safety_resume_requests, "
        "public.safety_health_evidence FROM PUBLIC"
    )
    op.execute("GRANT SELECT,INSERT,UPDATE ON public.execution_owners TO atl_execution")
    op.execute("GRANT SELECT ON public.execution_owners TO atl_control_api,atl_watchdog")
    op.execute("GRANT SELECT ON public.safety_resume_requests TO atl_execution,atl_control_api")
    op.execute("GRANT SELECT,INSERT ON public.safety_health_evidence TO atl_execution")
    op.execute("GRANT SELECT ON public.safety_health_evidence TO atl_control_api")


def downgrade() -> None:
    raise RuntimeError("safety control downgrade would discard recovery evidence")
