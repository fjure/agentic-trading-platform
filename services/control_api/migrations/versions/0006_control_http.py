"""Durable HTTP control receipts and narrow operator mutations.

Revision ID: 0006_control_http
Revises: 0005_decision_usage
"""

from alembic import op

revision = "0006_control_http"
down_revision = "0005_decision_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE public.control_http_receipts (
      actor_id varchar(255) NOT NULL CHECK (length(actor_id)>0),
      operation varchar(40) NOT NULL,
      idempotency_key varchar(255) NOT NULL CHECK (length(idempotency_key)>0),
      account_id varchar(255) NOT NULL REFERENCES public.accounts(id) ON DELETE RESTRICT,
      payload_hash varchar(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
      status integer NOT NULL CHECK (status BETWEEN 200 AND 599),
      response_body jsonb NOT NULL,
      response_headers jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY (actor_id,operation,idempotency_key)
    )
    """)
    op.execute("""
    CREATE TRIGGER control_http_receipts_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
      ON public.control_http_receipts FOR EACH STATEMENT
      EXECUTE FUNCTION public.ledger_reject_mutation()
    """)
    op.execute("REVOKE ALL ON public.control_http_receipts FROM PUBLIC")
    op.execute("GRANT SELECT,INSERT ON public.control_http_receipts TO atl_control_api")
    op.execute("""
    CREATE FUNCTION public.control_lock_account(p_account varchar) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    BEGIN
      PERFORM id FROM public.accounts WHERE id=p_account FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'account missing'; END IF;
    END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.control_lock_account(varchar) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.control_lock_account(varchar) TO atl_control_api")
    op.execute("""
    CREATE FUNCTION public.control_set_safe_mode(
      p_account varchar,p_mode varchar,p_actor varchar,p_trace varchar
    ) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE c public.risk_account_context%ROWTYPE;
            v integer; pv integer; now_at timestamptz;
    BEGIN
      IF p_mode IS NULL OR p_mode NOT IN ('DISABLED','REPLAY','SHADOW')
         OR p_actor IS NULL OR length(p_actor)=0 OR length(p_actor)>255
         OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$' THEN
        RAISE EXCEPTION 'invalid safe mode transition';
      END IF;
      PERFORM id FROM public.accounts WHERE id=p_account FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'account missing'; END IF;
      SELECT * INTO c FROM public.risk_account_context WHERE account_id=p_account FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'risk context missing'; END IF;
      IF c.mode=p_mode AND c.safety_state IN ('PAUSE_NEW','HALTED')
        THEN RETURN c.version; END IF;
      SELECT version INTO pv FROM public.risk_policies ORDER BY version DESC LIMIT 1;
      IF pv IS NULL THEN RAISE EXCEPTION 'risk policy missing'; END IF;
      v := c.version+1;
      now_at := clock_timestamp();
      INSERT INTO public.audit_events
        (id,occurred_at,actor_id,action,reason_codes,entity_id,
         entity_version,trace_id,policy_version,outcome)
      VALUES (gen_random_uuid()::text,now_at,p_actor,'control.mode_set',
        jsonb_build_array('OK'),p_account,v,p_trace,pv,p_mode);
      UPDATE public.risk_account_context SET version=v,mode=p_mode,
        safety_state=CASE WHEN c.safety_state='HALTED' THEN 'HALTED' ELSE 'PAUSE_NEW' END,
        reconciliation_current=false,actor_id=p_actor,trace_id=p_trace
        WHERE account_id=p_account;
      INSERT INTO public.outbox_events
        (event_id,event_type,occurred_at,aggregate_id,aggregate_version,
         trace_id,schema_version,payload)
      VALUES (gen_random_uuid()::text,'control.mode.changed.v1',now_at,p_account,v,
        p_trace,1,jsonb_build_object('account_id',p_account,'action','MODE_CHANGED',
        'mode',p_mode,'reason_code','OK',
        'safety_state',CASE WHEN c.safety_state='HALTED' THEN 'HALTED' ELSE 'PAUSE_NEW' END,
        'policy_version',pv));
      RETURN v;
    END $$
    """)
    op.execute("""
    CREATE FUNCTION public.control_replace_risk_policy(
      p_account varchar,p_actor varchar,p_trace varchar,
      p_per_instrument integer,p_total integer,p_position_fraction numeric,
      p_daily_loss_fraction numeric
    ) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
    SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE old_policy public.risk_policies%ROWTYPE;
            c public.risk_account_context%ROWTYPE;
            new_version integer; now_at timestamptz; audit_id varchar;
    BEGIN
      IF p_actor IS NULL OR length(p_actor)=0 OR length(p_actor)>255
         OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$'
         OR p_per_instrument IS NULL OR p_per_instrument<1
         OR p_total IS NULL OR p_total<1
         OR p_position_fraction IS NULL OR p_position_fraction<0 OR p_position_fraction>1
         OR p_daily_loss_fraction IS NULL OR p_daily_loss_fraction<0
         OR p_daily_loss_fraction>1
         OR p_position_fraction<>trunc(p_position_fraction,18)
         OR p_daily_loss_fraction<>trunc(p_daily_loss_fraction,18) THEN
        RAISE EXCEPTION 'invalid risk policy';
      END IF;
      PERFORM id FROM public.accounts WHERE id=p_account FOR UPDATE;
      IF NOT FOUND THEN RAISE EXCEPTION 'account missing'; END IF;
      SELECT * INTO c FROM public.risk_account_context WHERE account_id=p_account;
      IF NOT FOUND THEN RAISE EXCEPTION 'risk context missing'; END IF;
      SELECT * INTO old_policy FROM public.risk_policies ORDER BY version DESC LIMIT 1;
      IF NOT FOUND THEN RAISE EXCEPTION 'risk policy missing'; END IF;
      new_version := old_policy.version+1;
      now_at := clock_timestamp();
      audit_id := gen_random_uuid()::text;
      INSERT INTO public.audit_events
        (id,occurred_at,actor_id,action,reason_codes,entity_id,
         entity_version,trace_id,policy_version,outcome)
      VALUES (audit_id,now_at,p_actor,'control.risk_policy_updated',
        jsonb_build_array('OK'),'control-policy-'||new_version,new_version,p_trace,
        new_version,'UPDATED');
      INSERT INTO public.risk_policies
        (id,version,currency,max_positions_per_instrument,max_positions_total,
         max_position_fraction,max_daily_loss_fraction,effective_at,actor_id,
         audit_event_id,simulation)
      VALUES ('control-policy-'||new_version,new_version,old_policy.currency,
        p_per_instrument,p_total,p_position_fraction,p_daily_loss_fraction,
        now_at,p_actor,audit_id,true);
      INSERT INTO public.outbox_events
        (event_id,event_type,occurred_at,aggregate_id,aggregate_version,
         trace_id,schema_version,payload)
      VALUES (gen_random_uuid()::text,'control.policy.changed.v1',now_at,'risk-policy:'||p_account,
        new_version,p_trace,1,jsonb_build_object('account_id',p_account,
        'action','POLICY_CHANGED','mode',c.mode,'safety_state',c.safety_state,
        'policy_version',new_version,'reason_code','OK'));
      RETURN new_version;
    END $$
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION public."
        "control_set_safe_mode(varchar,varchar,varchar,varchar) FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public."
        "control_replace_risk_policy(varchar,varchar,varchar,integer,integer,numeric,numeric) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public."
        "control_set_safe_mode(varchar,varchar,varchar,varchar) TO atl_control_api"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public."
        "control_replace_risk_policy(varchar,varchar,varchar,integer,integer,numeric,numeric) "
        "TO atl_control_api"
    )


def downgrade() -> None:
    raise RuntimeError("control receipts and audited mutations are irreversible")
