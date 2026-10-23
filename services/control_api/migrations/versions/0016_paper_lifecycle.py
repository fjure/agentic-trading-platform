"""Explicit immutable Paper profile, fee scope and short-lived lifecycle authority."""

from alembic import op

revision = "0016_paper_lifecycle"
down_revision = "0015_paper_limit_policy"
branch_labels = None
depends_on = None


def _function(name, args, result, body):
    op.execute(
        f"CREATE OR REPLACE FUNCTION public.{name}({args}) RETURNS {result} "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp "
        "AS $$ " + body + " $$"
    )
    signature = ",".join(arg.strip().split()[-1] for arg in args.split(",")) if args else ""
    op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC")


def upgrade():
    op.execute("""
    ALTER TABLE public.paper_workflow_state DROP CONSTRAINT
      paper_workflow_state_operating_state_check;
    ALTER TABLE public.paper_workflow_state ADD CHECK
      (operating_state IN ('PAUSED','STOPPED','HALTED','RUNNING'));
    ALTER TABLE public.trading_profiles DROP CONSTRAINT trading_profiles_strategy_check;
    ALTER TABLE public.trading_profiles ADD CHECK
      (strategy IN ('mock-neutral','neutral-baseline','jev'));
    CREATE TABLE public.paper_profile_versions (
      id text PRIMARY KEY,version integer UNIQUE NOT NULL CHECK(version>0),
      credential_generation bigint NOT NULL CHECK(credential_generation>0),
      account_generation bigint NOT NULL CHECK(account_generation>0),
      risk_budget numeric(38,18) NOT NULL CHECK(risk_budget>0 AND risk_budget<=1000000),
      strategy text NOT NULL CHECK(strategy IN ('mock-neutral','neutral-baseline','jev')),
      basis_hash text NOT NULL CHECK(basis_hash~'^[0-9a-f]{64}$'),
      fee_scope text NOT NULL CHECK(fee_scope='alpaca-paper-direct-retail-us-equity-zero-v1'),
      actor_id text NOT NULL,trace_id varchar(32) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE public.paper_profile_receipts (
      id text PRIMARY KEY,actor_id text NOT NULL,idempotency_key text NOT NULL,
      request jsonb NOT NULL,response jsonb NOT NULL,trace_id varchar(32) NOT NULL,
      UNIQUE(actor_id,idempotency_key)
    );
    CREATE TABLE public.paper_activations (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),operation_id uuid UNIQUE NOT NULL
        REFERENCES public.paper_commands(id),
      epoch bigint NOT NULL,credential_generation bigint NOT NULL,account_generation bigint
 NOT NULL,
      profile_version integer NOT NULL REFERENCES public.paper_profile_versions(version),
      policy_version integer NOT NULL,limit_policy_version integer NOT NULL,
      owner_id text NOT NULL,fencing_token bigint NOT NULL CHECK(fencing_token>0),
      symbols jsonb NOT NULL,issued_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
      receipt_id text NOT NULL REFERENCES public.paper_reconciliation_receipts(id),
      actor_id text NOT NULL,trace_id varchar(32) NOT NULL,
      CHECK(expires_at>issued_at AND expires_at<=issued_at+interval '30 seconds')
    );
    CREATE TABLE public.paper_proposal_activations (
      id text PRIMARY KEY,proposal_id varchar(255) UNIQUE NOT NULL
        REFERENCES public.trade_proposals(id),activation_id uuid NOT NULL
        REFERENCES public.paper_activations(id),actor_id text NOT NULL,trace_id varchar(32) NOT NULL
    );
    CREATE TABLE public.paper_worker_health (
      singleton boolean PRIMARY KEY CHECK(singleton),state text NOT NULL
        CHECK(state IN ('READY','UNAVAILABLE')),observed_at timestamptz NOT NULL
    );
    REVOKE ALL ON public.paper_worker_health FROM PUBLIC;
    """)
    for table in (
        "paper_profile_versions",
        "paper_profile_receipts",
        "paper_activations",
        "paper_proposal_activations",
    ):
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
            f"ON public.{table} FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.ledger_reject_mutation()"
        )
    for table in ("paper_profile_versions", "paper_profile_receipts", "paper_proposal_activations"):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {table}_audit AFTER INSERT ON public.{table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION public.paper_reconciliation_require_audit()"
        )
    _function("paper_activation_require_audit", "", "trigger", """
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM public.audit_events WHERE entity_id=NEW.id::text
        AND entity_version=1 AND actor_id=NEW.actor_id AND trace_id=NEW.trace_id
        AND transaction_id=txid_current()) THEN
        RAISE EXCEPTION 'Paper activation requires matching transaction audit'; END IF;
      RETURN NULL;
    END;
    """)
    op.execute("CREATE CONSTRAINT TRIGGER paper_activation_audit AFTER INSERT "
               "ON public.paper_activations DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
               "EXECUTE FUNCTION public.paper_activation_require_audit()")
    _function("paper_trading_profile_get", "p_trace text", "jsonb", _PROFILE_GET)
    _function(
        "paper_trading_profile_put",
        "p_actor text,p_key text,p_trace text,p_body jsonb",
        "jsonb",
        _PROFILE_PUT,
    )
    _function("paper_fee_authority", "", "jsonb", _FEE)
    _function("paper_recovery_fee_authority", "", "jsonb", _RECOVERY_FEE)
    _function(
        "paper_worker_heartbeat",
        "p_state text",
        "boolean",
        """
    BEGIN
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_worker_login'
        OR current_setting('role')<>'atl_paper_worker' OR p_state IS NULL
        OR p_state NOT IN ('READY','UNAVAILABLE') THEN RETURN false; END IF;
      INSERT INTO public.paper_worker_health VALUES(true,p_state,clock_timestamp())
        ON CONFLICT(singleton) DO UPDATE SET state=excluded.state,observed_at=excluded.observed_at;
      RETURN true;
    END;
    """,
    )
    _function("paper_activation_guard", "p_account text,p_proposal text", "jsonb", _GUARD)
    _function(
        "paper_planning_active",
        "",
        "boolean",
        """
    BEGIN
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_worker_login'
        OR current_setting('role')<>'atl_paper_worker' THEN RETURN false; END IF;
      RETURN public.paper_activation_guard('paper',NULL) IS NOT NULL;
    END;
    """,
    )
    _function(
        "paper_activation_begin",
        "p_id uuid,p_owner uuid,p_fence bigint,p_proof jsonb",
        "jsonb",
        _ACTIVATE,
    )
    _function("paper_activation_read", "", "jsonb", _ACTIVATION_READ)
    _function("paper_restart_close", "p_owner text,p_fence bigint", "boolean", _RESTART)
    _function("paper_watchdog_poll", "p_trace text", "boolean", _WATCHDOG)
    _function("paper_daily_reference", "", "jsonb", _DAILY_REFERENCE)
    _function(
        "paper_daily_append",
        "p_owner text,p_fence bigint,p_portfolio integer,p_body jsonb",
        "boolean",
        _DAILY_APPEND,
    )
    _replace_control_ports()
    _replace_plan_record()
    _replace_status()
    op.execute("""
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_control_api') THEN
        GRANT EXECUTE ON FUNCTION public.paper_trading_profile_get(text),
          public.paper_trading_profile_put(text,text,text,jsonb) TO atl_paper_control_api;
      END IF;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_execution') THEN
        GRANT EXECUTE ON FUNCTION
 public.paper_fee_authority(),public.paper_recovery_fee_authority(),
          public.paper_activation_read(),
          public.paper_activation_guard(text,text),public.paper_activation_begin(uuid,uuid,bigint,jsonb),
          public.paper_restart_close(text,bigint),public.paper_daily_reference(),
          public.paper_daily_append(text,bigint,integer,jsonb),public.paper_trading_profile_get(text)
          TO atl_paper_execution;
        GRANT SELECT(account_id,ready,portfolio_snapshot_id,portfolio_version,owner_id,
          fencing_token,observed_at) ON public.paper_reconciliation_receipts
          TO atl_paper_execution;
        GRANT SELECT(cursor,event_id),INSERT(cursor,event_id) ON public.publish_log
          TO atl_paper_execution;
        GRANT SELECT(singleton,next_cursor),UPDATE(next_cursor) ON public.publish_cursor
          TO atl_paper_execution;
        GRANT UPDATE(published_at) ON public.outbox_events TO atl_paper_execution;
        GRANT INSERT ON public.market_bars,public.evidence_bundles,public.market_states,
          public.broker_account_bindings,public.broker_asset_bindings,public.broker_binding_proofs,
          public.risk_instrument_context,public.broker_orders,public.order_intents,
          public.risk_reservations,public.risk_decisions TO atl_paper_execution;
        GRANT UPDATE(valid_until) ON public.risk_account_context TO atl_paper_execution;
        GRANT UPDATE(account_id) ON public.risk_reservations,public.order_intents
          TO atl_paper_execution;
        GRANT UPDATE(version,capability_version,broker_id,paper_confirmed,regular_session_confirmed,
          continuous_session_confirmed,market_order_confirmed,limit_order_confirmed,
          fractional_confirmed,quantity_step,price_tick,session_open,session_version,
          session_valid_until,valid_until,actor_id,trace_id)
          ON public.risk_instrument_context TO atl_paper_execution;
      END IF;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_worker') THEN
        GRANT EXECUTE ON FUNCTION public.paper_planning_active(),public.paper_worker_heartbeat(text)
          TO atl_paper_worker;
      END IF;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_watchdog') THEN
        GRANT USAGE ON SCHEMA public TO atl_paper_watchdog;
        GRANT EXECUTE ON FUNCTION public.paper_watchdog_poll(text) TO atl_paper_watchdog;
      END IF;
    END $$;
    """)


_PROFILE_GET = """
DECLARE p public.paper_profile_versions%ROWTYPE; pv integer; lv integer;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR NOT (
  (session_user='atl_paper_control_api_login' AND current_setting('role')='atl_paper_control_api')
  OR (session_user='atl_paper_execution_login' AND current_setting('role')='atl_paper_execution'))
  THEN RETURN NULL; END IF;
 IF p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$' THEN RETURN NULL; END IF;
 SELECT * INTO p FROM public.paper_profile_versions ORDER BY version DESC LIMIT 1;
 SELECT version INTO pv FROM public.risk_policies ORDER BY version DESC LIMIT 1;
 SELECT version INTO lv FROM public.paper_limit_policies ORDER BY version DESC LIMIT 1;
 RETURN jsonb_build_object('namespace','alpaca-paper','configured',p.id IS NOT NULL,
  'currency','USD','riskBudget',CASE WHEN p.id IS NULL THEN NULL ELSE
 trim_scale(p.risk_budget)::text END,
  'strategy',p.strategy,'strategyVersion',CASE WHEN p.id IS NULL THEN NULL ELSE
 p.strategy||'.v1' END,
  'credentialGeneration',coalesce(p.credential_generation,0),
  'accountGeneration',coalesce(p.account_generation,0),'profileVersion',coalesce(p.version,0),
  'budgetVersion',coalesce(p.version,0),'policyVersion',coalesce(pv,0),'limitPolicyVersion',lv,
  'feeAttestation',CASE WHEN p.id IS NULL THEN NULL ELSE jsonb_build_object(
    'scope',p.fee_scope,'confirmed',true) END,'traceId',p_trace);
END;
"""

_PROFILE_PUT = """
DECLARE s public.paper_workflow_state%ROWTYPE; p public.paper_profile_versions%ROWTYPE;
 r public.paper_profile_receipts%ROWTYPE; basis text; v integer; result jsonb; row_id text;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_control_api_login'
  OR current_setting('role')<>'atl_paper_control_api' THEN RETURN NULL; END IF;
 IF p_actor IS NULL OR p_actor !~ '^[A-Za-z0-9._:-]{1,255}$'
  OR p_key IS NULL OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$'
  OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$'
  OR jsonb_typeof(p_body) IS DISTINCT FROM 'object' OR pg_column_size(p_body)>4096
  OR NOT p_body ?& ARRAY['riskBudget','currency','strategy','credentialGeneration',
    'accountGeneration','expectedProfileVersion','confirmation','feeAttestation']
  OR (p_body-ARRAY['riskBudget','currency','strategy','credentialGeneration',
    'accountGeneration','expectedProfileVersion','confirmation','feeAttestation'])<>'{}'::jsonb
  THEN RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
 IF jsonb_typeof(p_body->'riskBudget') IS DISTINCT FROM 'string'
  OR p_body->>'riskBudget' !~ '^(0|[1-9][0-9]{0,6})(\\.[0-9]{1,18})?$'
  OR (p_body->>'riskBudget')::numeric<=0 OR (p_body->>'riskBudget')::numeric>1000000
  OR p_body->>'currency' IS DISTINCT FROM 'USD'
  OR p_body->>'strategy' IS NULL OR p_body->>'strategy' NOT IN
 ('mock-neutral','neutral-baseline','jev')
  OR p_body->>'confirmation' IS DISTINCT FROM 'SAVE_PAPER_PROFILE'
  OR p_body->'feeAttestation' IS DISTINCT FROM
    '{"scope": "alpaca-paper-direct-retail-us-equity-zero-v1","confirmed": true}'::jsonb
  OR EXISTS(SELECT 1 FROM jsonb_each(p_body) e WHERE e.key IN
    ('credentialGeneration','accountGeneration','expectedProfileVersion') AND (
     jsonb_typeof(e.value)<>'number' OR e.value::text !~ '^(0|[1-9][0-9]{0,15})$'
     OR e.value::text::numeric>9007199254740991))
  OR (p_body->>'credentialGeneration')::numeric<1 OR (p_body->>'accountGeneration')::numeric<1
  THEN RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
 SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
 SELECT * INTO r FROM public.paper_profile_receipts WHERE actor_id=p_actor AND
 idempotency_key=p_key;
 IF FOUND THEN
  IF r.request<>p_body THEN RETURN jsonb_build_object('error','IDEMPOTENCY_CONFLICT'); END IF;
  RETURN r.response; END IF;
 PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
 SELECT evidence_hash INTO basis FROM public.paper_initial_bases WHERE account_id='paper';
 IF basis IS NULL THEN RETURN jsonb_build_object('error','LEDGER_NOT_PREPARED'); END IF;
 IF s.pending_generation IS NOT NULL OR s.verified_at IS NULL
  OR s.active_generation<>s.saved_generation OR s.active_generation<1
  OR s.active_generation<>(p_body->>'credentialGeneration')::bigint
  OR s.account_generation<>(p_body->>'accountGeneration')::bigint
  THEN RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
 SELECT * INTO p FROM public.paper_profile_versions ORDER BY version DESC LIMIT 1;
 IF coalesce(p.version,0)<>(p_body->>'expectedProfileVersion')::bigint
  OR coalesce(p.version,0)>=2147483647 OR s.epoch>=9007199254740991
  THEN RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
 v:=coalesce(p.version,0)+1; row_id:='paper-profile-'||v::text;
 PERFORM public.safety_latch('paper','PAUSE_NEW',p_actor,p_trace,'PAUSED_NEW');
 UPDATE public.paper_workflow_state SET epoch=epoch+1,
  operating_state=CASE WHEN operating_state='HALTED' THEN 'HALTED' ELSE 'PAUSED' END,
  changed_at=clock_timestamp() WHERE singleton;
 INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
  entity_version,trace_id) VALUES(row_id||'-audit',clock_timestamp(),p_actor,'paper.profile',
  '["PAPER_PROFILE_SAVED"]',row_id,1,p_trace);
 INSERT INTO public.paper_profile_versions VALUES(row_id,v,s.active_generation,s.account_generation,
  (p_body->>'riskBudget')::numeric,p_body->>'strategy',basis,
  'alpaca-paper-direct-retail-us-equity-zero-v1',p_actor,p_trace,clock_timestamp());
 INSERT INTO public.trading_profiles VALUES('paper',(p_body->>'riskBudget')::numeric,'USD',
  p_body->>'strategy',(p_body->>'strategy')||'.v1',v,clock_timestamp(),p_actor,row_id||'-audit')
 ON CONFLICT(account_id) DO UPDATE SET budget=excluded.budget,strategy=excluded.strategy,
  strategy_version=excluded.strategy_version,version=excluded.version,changed_at=excluded.changed_at,
  actor_id=excluded.actor_id,audit_event_id=excluded.audit_event_id;
 result:=public.paper_trading_profile_get(p_trace);
 INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
  entity_version,trace_id)
 VALUES(row_id||'-receipt-audit',clock_timestamp(),p_actor,'paper.profile',
  '["PAPER_PROFILE_SAVED"]',row_id||'-receipt',1,p_trace);
 INSERT INTO public.paper_profile_receipts
 VALUES(row_id||'-receipt',p_actor,p_key,p_body,result,p_trace);
 RETURN result;
EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation
 THEN RETURN jsonb_build_object('error','INVALID_REQUEST');
END;
"""

_FEE = """
DECLARE p public.paper_profile_versions%ROWTYPE; s public.paper_workflow_state%ROWTYPE;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
  OR current_setting('role')<>'atl_paper_execution' THEN RETURN NULL; END IF;
 SELECT * INTO p FROM public.paper_profile_versions ORDER BY version DESC LIMIT 1;
 SELECT * INTO s FROM public.paper_workflow_state WHERE singleton;
 IF p.id IS NULL OR s.pending_generation IS NOT NULL OR s.verified_at IS NULL
  OR p.credential_generation<>s.active_generation OR s.active_generation<>s.saved_generation
  OR p.account_generation<>s.account_generation OR NOT EXISTS(
    SELECT 1 FROM public.paper_initial_bases b WHERE b.account_id='paper'
    AND b.evidence_hash=p.basis_hash
    AND (b.evidence->>'providerAccountId')::uuid=s.pinned_account_id) THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('profile','alpaca-paper-us-retail-equity-zero-v1',
  'profileVersion',p.version,'credentialGeneration',p.credential_generation,
  'accountGeneration',p.account_generation,'basisHash',p.basis_hash);
END;
"""

_RECOVERY_FEE = """
DECLARE p public.paper_profile_versions%ROWTYPE; s public.paper_workflow_state%ROWTYPE;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
  OR current_setting('role')<>'atl_paper_execution' THEN RETURN NULL; END IF;
 SELECT * INTO s FROM public.paper_workflow_state WHERE singleton;
 SELECT * INTO p FROM public.paper_profile_versions WHERE credential_generation<=s.active_generation
  AND account_generation=s.account_generation ORDER BY version DESC LIMIT 1;
 IF p.id IS NULL OR s.active_generation<1 OR NOT EXISTS(
  SELECT 1 FROM public.paper_initial_bases b WHERE b.account_id='paper'
   AND b.evidence_hash=p.basis_hash AND
 (b.evidence->>'providerAccountId')::uuid=s.pinned_account_id)
  THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('profile','alpaca-paper-us-retail-equity-zero-v1',
  'profileVersion',p.version,'credentialGeneration',s.active_generation,
  'attestedCredentialGeneration',p.credential_generation,'accountGeneration',p.account_generation,
  'basisHash',p.basis_hash);
END;
"""


def downgrade():
    raise RuntimeError("Paper profile and lifecycle authority are forward-only")


_GUARD = """
DECLARE x public.paper_activations%ROWTYPE; s public.paper_workflow_state%ROWTYPE;
 p public.paper_profile_versions%ROWTYPE; a public.risk_account_context%ROWTYPE;
 risk public.risk_policies%ROWTYPE; daily public.paper_daily_risk_evidence%ROWTYPE;
 n timestamptz:=clock_timestamp(); symbol text; state_id text; facts jsonb;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR p_account IS DISTINCT FROM 'paper'
  OR NOT ((session_user='atl_paper_execution_login' AND
 current_setting('role')='atl_paper_execution')
   OR (session_user='atl_paper_worker_login' AND current_setting('role')='atl_paper_worker')
   OR (session_user='atl_paper_watchdog_login' AND current_setting('role')='atl_paper_watchdog')
   OR (session_user='atl_paper_control_api_login' AND
 current_setting('role')='atl_paper_control_api'))
  THEN RETURN NULL; END IF;
 SELECT * INTO x FROM public.paper_activations ORDER BY issued_at DESC,id DESC LIMIT 1;
 SELECT * INTO s FROM public.paper_workflow_state WHERE singleton;
 SELECT * INTO a FROM public.risk_account_context WHERE account_id='paper';
 SELECT * INTO p FROM public.paper_profile_versions ORDER BY version DESC LIMIT 1;
 SELECT * INTO risk FROM public.risk_policies ORDER BY version DESC LIMIT 1;
 SELECT * INTO daily FROM public.paper_daily_risk_evidence WHERE account_id='paper'
  AND portfolio_version=a.portfolio_version ORDER BY observed_at DESC,id DESC LIMIT 1;
 IF x.id IS NULL OR x.issued_at>n OR x.expires_at<=n OR s.operating_state<>'RUNNING'
  OR s.epoch>=9007199254740991
  OR x.epoch<>s.epoch OR x.credential_generation<>s.active_generation
  OR s.active_generation<>s.saved_generation OR s.pending_generation IS NOT NULL
  OR s.verified_at IS NULL OR x.account_generation<>s.account_generation
  OR p.version IS DISTINCT FROM x.profile_version OR p.credential_generation<>s.active_generation
  OR p.account_generation<>s.account_generation OR a.mode IS DISTINCT FROM 'PAPER'
  OR a.safety_state IS DISTINCT FROM 'RUNNING' OR NOT a.reconciliation_current OR a.valid_until<=n
  OR EXISTS(SELECT 1 FROM public.broker_orders bo
    JOIN public.order_intents oi ON oi.id=bo.intent_id
    WHERE oi.account_id='paper' AND bo.status IN ('SUBMITTING','UNKNOWN'))
  OR EXISTS(SELECT 1 FROM public.risk_reservations rr
    WHERE rr.account_id='paper' AND rr.state='HELD_UNKNOWN')
  OR x.policy_version IS DISTINCT FROM (SELECT version FROM public.risk_policies
    ORDER BY version DESC LIMIT 1)
  OR x.limit_policy_version IS DISTINCT FROM (SELECT version FROM public.paper_limit_policies
    ORDER BY version DESC LIMIT 1)
  OR NOT EXISTS(SELECT 1 FROM public.trading_profiles t WHERE t.account_id='paper'
    AND t.version=p.version AND t.budget=p.risk_budget AND t.strategy=p.strategy)
  OR NOT EXISTS(SELECT 1 FROM public.execution_owners o WHERE o.account_id='paper'
    AND o.owner_id=x.owner_id AND o.fencing_token=x.fencing_token
    AND o.heartbeat_at BETWEEN n-interval '30 seconds' AND n)
  OR NOT EXISTS(SELECT 1 FROM public.paper_worker_health h WHERE h.singleton AND h.state='READY'
    AND h.observed_at BETWEEN n-interval '30 seconds' AND n)
  OR NOT EXISTS(SELECT 1 FROM public.paper_daily_risk_evidence d
    JOIN public.portfolio_snapshots ps ON ps.id=a.portfolio_snapshot_id
    WHERE d.account_id='paper' AND d.portfolio_version=a.portfolio_version
    AND d.current_equity=ps.equity AND ps.ledger_version=a.portfolio_version
    AND d.observed_at BETWEEN n-interval '30 seconds' AND n AND d.expires_at>n)
  OR NOT EXISTS(SELECT 1 FROM public.paper_initial_bases ib WHERE ib.account_id='paper'
    AND ib.evidence_hash=p.basis_hash AND
 (ib.evidence->>'providerAccountId')::uuid=s.pinned_account_id)
  THEN RETURN NULL; END IF;
 IF daily.id IS NULL OR daily.observed_at<=n-interval '30 seconds' OR daily.observed_at>n
  OR daily.expires_at<=n OR
 greatest(0,daily.opening_upper+daily.external_funding-daily.current_equity)
   >=daily.capital_lower*risk.max_daily_loss_fraction
  OR (SELECT count(*) FROM (
    SELECT instrument_id FROM public.positions WHERE account_id='paper' AND quantity>0
    UNION SELECT tp.instrument_id FROM public.risk_reservations rr
     JOIN public.trade_proposals tp ON tp.id=rr.proposal_id
     WHERE rr.account_id='paper' AND rr.state='ACTIVE' AND tp.target='LONG'
      AND rr.reserved_notional>0) held_or_reserved)>risk.max_positions_total
  OR EXISTS(SELECT 1 FROM public.positions WHERE account_id='paper' AND quantity>0
    GROUP BY instrument_id HAVING count(*)>risk.max_positions_per_instrument)
  THEN RETURN NULL; END IF;
 IF p_proposal IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.paper_proposal_activations l
  JOIN public.trade_proposals tp ON tp.id=l.proposal_id
  WHERE l.proposal_id=p_proposal AND l.activation_id=x.id
  AND x.symbols ? tp.instrument_id) THEN RETURN NULL; END IF;
 FOR symbol IN SELECT jsonb_array_elements_text(x.symbols)
  UNION SELECT instrument_id FROM public.positions WHERE account_id='paper' AND quantity>0
  UNION SELECT tp.instrument_id FROM public.risk_reservations rr
   JOIN public.trade_proposals tp ON tp.id=rr.proposal_id
   WHERE rr.account_id='paper' AND rr.state='ACTIVE' AND tp.target='LONG'
    AND rr.reserved_notional>0 LOOP
  SELECT ms.id INTO state_id FROM public.market_states ms WHERE ms.instrument_id=symbol
    AND ms.portfolio_id=a.portfolio_snapshot_id ORDER BY ms.as_of DESC,ms.id DESC LIMIT 1;
  facts:=public.paper_planner_inputs_v15(state_id);
  IF state_id IS NULL OR facts IS NULL
    OR (facts->>'held_exposure')::numeric+(facts->>'reserved_exposure')::numeric*1.01>p.risk_budget
    OR
 ((facts->>'instrument_exposure')::numeric+
  (facts->>'instrument_reserved_exposure')::numeric*1.01)>(facts->>'equity')::numeric*risk.max_position_fraction
    THEN RETURN NULL; END IF;
 END LOOP;
 RETURN jsonb_build_object('activationId',x.id,'epoch',x.epoch,
  'credentialGeneration',x.credential_generation,'accountGeneration',x.account_generation,
  'policyVersion',x.policy_version,'profileVersion',x.profile_version,'budgetVersion',x.profile_version,
  'limitPolicyVersion',x.limit_policy_version,'riskBudget',trim_scale(p.risk_budget)::text,
  'strategy',p.strategy,'symbols',x.symbols,'ownerId',x.owner_id,'fencingToken',x.fencing_token,
  'issuedAt',to_char(x.issued_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'expiresAt',to_char(x.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'operatingState','RUNNING','mode','PAPER','safetyState','RUNNING',
  'proposalLinked',p_proposal IS NOT NULL);
END;
"""

_ACTIVATE = """
DECLARE c public.paper_commands%ROWTYPE; s public.paper_workflow_state%ROWTYPE;
 p public.paper_profile_versions%ROWTYPE; a public.risk_account_context%ROWTYPE;
 r public.paper_reconciliation_receipts%ROWTYPE; x public.paper_activations%ROWTYPE;
 risk public.risk_policies%ROWTYPE; daily public.paper_daily_risk_evidence%ROWTYPE;
 n timestamptz; expiry timestamptz; pv integer; lv integer; symbol text; state_id text; facts jsonb;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
  OR current_setting('role')<>'atl_paper_execution' THEN RETURN NULL; END IF;
 SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
 PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
 n:=clock_timestamp();
 SELECT * INTO c FROM public.paper_commands WHERE id=p_id FOR UPDATE;
 IF c.id IS NULL OR c.operation_type<>'START' OR p_owner IS NULL OR p_fence IS NULL
  OR p_fence<1 OR c.owner IS DISTINCT FROM p_owner OR c.fence IS DISTINCT FROM p_fence
  THEN RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
 SELECT * INTO x FROM public.paper_activations WHERE operation_id=p_id;
 IF FOUND THEN RETURN jsonb_build_object('activationId',x.id,
  'expiresAt',to_char(x.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')); END IF;
 IF c.status<>'RUNNING' OR c.lease_until IS NULL OR c.lease_until<=n
  OR s.epoch>=9007199254740991
  OR c.accepted_generation<>s.epoch OR c.credential_generation<>s.active_generation
  OR s.active_generation<>s.saved_generation OR s.pending_generation IS NOT NULL
  OR s.verified_at IS NULL
  THEN RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
 IF s.operating_state='HALTED' THEN
  RETURN jsonb_build_object('error','KILL_SWITCH_ACTIVE'); END IF;
 IF jsonb_typeof(p_proof) IS DISTINCT FROM 'object' OR NOT p_proof ?& ARRAY[
  'executionOwnerId','executionFencingToken','reconciliationReceiptId']
  OR
 (p_proof-ARRAY['executionOwnerId','executionFencingToken','reconciliationReceiptId'])<>'{}'::jsonb
  OR pg_column_size(p_proof)>1024 OR jsonb_typeof(p_proof->'executionOwnerId')<>'string'
  OR p_proof->>'executionOwnerId' !~ '^[A-Za-z0-9._:-]{1,255}$'
  OR jsonb_typeof(p_proof->'executionFencingToken')<>'number'
  OR p_proof->>'executionFencingToken' !~ '^[1-9][0-9]{0,15}$'
  OR (p_proof->>'executionFencingToken')::numeric>9007199254740991
  OR jsonb_typeof(p_proof->'reconciliationReceiptId')<>'string'
  OR p_proof->>'reconciliationReceiptId' !~ '^[A-Za-z0-9._:-]{1,255}$'
  THEN RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
 SELECT * INTO p FROM public.paper_profile_versions ORDER BY version DESC LIMIT 1;
 SELECT version INTO pv FROM public.risk_policies ORDER BY version DESC LIMIT 1;
 SELECT * INTO risk FROM public.risk_policies ORDER BY version DESC LIMIT 1;
 SELECT version INTO lv FROM public.paper_limit_policies ORDER BY version DESC LIMIT 1;
 SELECT * INTO a FROM public.risk_account_context WHERE account_id='paper' FOR UPDATE;
 SELECT * INTO r FROM public.paper_reconciliation_receipts
  WHERE id=p_proof->>'reconciliationReceiptId';
 expiry:=(c.payload->>'expiresAt')::timestamptz;
 IF expiry<=n THEN
  RETURN jsonb_build_object('error','APPROVAL_EXPIRED'); END IF;
 IF expiry>n+interval '30 seconds' OR expiry>c.accepted_at+interval '30 seconds' THEN
  RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
 IF p.id IS NULL OR public.paper_fee_authority() IS NULL OR pv IS NULL OR lv IS NULL
  OR (c.payload->>'accountGeneration')::bigint<>s.account_generation
  OR (c.payload->>'profileVersion')::bigint<>p.version
  OR (c.payload->>'budgetVersion')::bigint<>p.version
  OR (c.payload->>'policyVersion')::bigint<>pv
  OR (c.payload->>'limitPolicyVersion')::bigint<>lv
  OR (c.payload->>'riskBudget')::numeric<>p.risk_budget OR c.payload->>'strategy'<>p.strategy
  OR a.account_id IS NULL OR a.safety_state='HALTED'
  OR EXISTS(SELECT 1 FROM public.broker_orders bo
    JOIN public.order_intents oi ON oi.id=bo.intent_id
    WHERE oi.account_id='paper' AND bo.status IN ('SUBMITTING','UNKNOWN'))
  OR EXISTS(SELECT 1 FROM public.risk_reservations rr
    WHERE rr.account_id='paper' AND rr.state='HELD_UNKNOWN')
  OR a.valid_until<=n OR r.id IS NULL OR NOT r.ready OR r.account_id<>'paper'
  OR NOT EXISTS(SELECT 1 FROM public.audit_events receipt_audit
    WHERE receipt_audit.entity_id=r.id AND receipt_audit.entity_version=1
     AND receipt_audit.action='execution.paper_receipt')
  OR EXISTS(SELECT 1 FROM public.broker_orders bo
    JOIN public.order_intents oi ON oi.id=bo.intent_id
    WHERE oi.account_id='paper' AND NOT EXISTS(
     SELECT 1 FROM public.audit_events order_audit,public.audit_events receipt_audit
     WHERE order_audit.entity_id=bo.id AND order_audit.entity_version=bo.version
      AND order_audit.action LIKE 'execution.%' AND receipt_audit.entity_id=r.id
      AND receipt_audit.entity_version=1 AND receipt_audit.action='execution.paper_receipt'
      AND order_audit.transaction_id<=receipt_audit.transaction_id))
  OR r.portfolio_snapshot_id<>a.portfolio_snapshot_id OR r.portfolio_version<>a.portfolio_version
  OR r.context_version>a.version OR r.owner_id<>p_proof->>'executionOwnerId'
  OR r.fencing_token<>(p_proof->>'executionFencingToken')::bigint
  OR r.observed_at>n OR r.observed_at<=n-interval '30 seconds'
  OR NOT EXISTS(SELECT 1 FROM public.execution_owners o WHERE o.account_id='paper'
   AND o.owner_id=r.owner_id AND o.fencing_token=r.fencing_token
   AND o.heartbeat_at BETWEEN n-interval '30 seconds' AND n)
  OR NOT EXISTS(SELECT 1 FROM public.paper_worker_health h WHERE h.singleton AND h.state='READY'
   AND h.observed_at BETWEEN n-interval '30 seconds' AND n)
  OR NOT EXISTS(SELECT 1 FROM public.paper_daily_risk_evidence d
   JOIN public.portfolio_snapshots ps ON ps.id=a.portfolio_snapshot_id
   WHERE d.account_id='paper' AND d.portfolio_version=a.portfolio_version
   AND d.current_equity=ps.equity AND ps.ledger_version=a.portfolio_version
   AND d.observed_at BETWEEN n-interval '30 seconds' AND n AND d.expires_at>n)
  THEN RETURN jsonb_build_object('error','RECONCILIATION_REQUIRED'); END IF;
 SELECT * INTO daily FROM public.paper_daily_risk_evidence WHERE account_id='paper'
  AND portfolio_version=a.portfolio_version ORDER BY observed_at DESC,id DESC LIMIT 1;
 IF daily.id IS NULL OR daily.observed_at<=n-interval '30 seconds' OR daily.observed_at>n
  OR daily.expires_at<=n OR
 greatest(0,daily.opening_upper+daily.external_funding-daily.current_equity)
    >=daily.capital_lower*risk.max_daily_loss_fraction
  OR (SELECT count(*) FROM (
    SELECT instrument_id FROM public.positions WHERE account_id='paper' AND quantity>0
    UNION SELECT tp.instrument_id FROM public.risk_reservations rr
     JOIN public.trade_proposals tp ON tp.id=rr.proposal_id
     WHERE rr.account_id='paper' AND rr.state='ACTIVE' AND tp.target='LONG'
      AND rr.reserved_notional>0) held_or_reserved)>risk.max_positions_total
  OR EXISTS(SELECT 1 FROM public.positions WHERE account_id='paper' AND quantity>0
    GROUP BY instrument_id HAVING count(*)>risk.max_positions_per_instrument)
  THEN RETURN jsonb_build_object('error','RECONCILIATION_REQUIRED'); END IF;
 BEGIN
 INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
  entity_version,trace_id) VALUES(gen_random_uuid()::text,n,c.actor_id,'paper.activate',
  '["PAPER_ACTIVATED"]','paper',a.version+1,c.trace_id);
 UPDATE public.risk_account_context SET version=version+1,mode='PAPER',safety_state='RUNNING',
  reconciliation_current=true,valid_until=least(valid_until,expiry),actor_id=c.actor_id,
  trace_id=c.trace_id WHERE account_id='paper';
 FOR symbol IN SELECT jsonb_array_elements_text(c.payload->'symbols')
  UNION SELECT instrument_id FROM public.positions WHERE account_id='paper' AND quantity>0
  UNION SELECT tp.instrument_id FROM public.risk_reservations rr
   JOIN public.trade_proposals tp ON tp.id=rr.proposal_id
   WHERE rr.account_id='paper' AND rr.state='ACTIVE' AND tp.target='LONG'
    AND rr.reserved_notional>0 LOOP
  SELECT ms.id INTO state_id FROM public.market_states ms WHERE ms.instrument_id=symbol
   AND ms.portfolio_id=a.portfolio_snapshot_id ORDER BY ms.as_of DESC,ms.id DESC LIMIT 1;
  facts:=public.paper_planner_inputs_v15(state_id);
  IF state_id IS NULL OR facts IS NULL THEN
   RAISE EXCEPTION USING ERRCODE='PAP01',MESSAGE='Paper sources unavailable'; END IF;
  IF
 (facts->>'held_exposure')::numeric+(facts->>'reserved_exposure')::numeric*1.01>p.risk_budget THEN
   RAISE EXCEPTION USING ERRCODE='PAP02',MESSAGE='Paper budget exceeded'; END IF;
  IF
 ((facts->>'instrument_exposure')::numeric+
  (facts->>'instrument_reserved_exposure')::numeric*1.01)>
  (facts->>'equity')::numeric*risk.max_position_fraction THEN
   RAISE EXCEPTION USING ERRCODE='PAP03',MESSAGE='Paper policy mismatch'; END IF;
 END LOOP;
 INSERT INTO public.paper_activations(operation_id,epoch,credential_generation,account_generation,
  profile_version,policy_version,limit_policy_version,owner_id,fencing_token,symbols,issued_at,
  expires_at,receipt_id,actor_id,trace_id) VALUES(c.id,s.epoch,s.active_generation,
  s.account_generation,p.version,pv,lv,r.owner_id,r.fencing_token,c.payload->'symbols',n,expiry,
  r.id,c.actor_id,c.trace_id) RETURNING * INTO x;
 INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
  entity_version,trace_id) VALUES(x.id::text||'-activation-audit',n,c.actor_id,
  'paper.activation_record',
  '["PAPER_ACTIVATED"]',x.id::text,1,c.trace_id);
 UPDATE public.paper_workflow_state SET operating_state='RUNNING',blocker='OK',
  changed_at=n WHERE singleton;
 UPDATE public.paper_commands SET status='SUCCEEDED',lease_until=NULL,error_code=NULL,
  updated_at=n WHERE id=c.id;
 INSERT INTO public.paper_command_events(operation_id,event_type,epoch,fence)
  VALUES(c.id,'SUCCEEDED',s.epoch,p_fence);
 RETURN jsonb_build_object('activationId',x.id,'expiresAt',
  to_char(expiry AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
 EXCEPTION WHEN SQLSTATE 'PAP01' THEN
  RETURN jsonb_build_object('error','RECONCILIATION_REQUIRED');
 WHEN SQLSTATE 'PAP02' THEN RETURN jsonb_build_object('error','BUDGET_EXCEEDED');
 WHEN SQLSTATE 'PAP03' THEN RETURN jsonb_build_object('error','POLICY_MISMATCH');
 END;
END;
"""

_ACTIVATION_READ = """
DECLARE s public.paper_workflow_state%ROWTYPE; p public.paper_profile_versions%ROWTYPE;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
  OR current_setting('role')<>'atl_paper_execution' THEN RETURN NULL; END IF;
 SELECT * INTO s FROM public.paper_workflow_state WHERE singleton;
 IF s.active_generation<1 OR s.pinned_account_id IS NULL OR NOT EXISTS(
  SELECT 1 FROM public.paper_initial_bases WHERE account_id='paper') THEN RETURN NULL; END IF;
 SELECT * INTO p FROM public.paper_profile_versions ORDER BY version DESC LIMIT 1;
 RETURN jsonb_build_object('credentialGeneration',s.active_generation,
  'accountGeneration',s.account_generation,'pinnedAccountId',s.pinned_account_id,'epoch',s.epoch,
  'profileVersion',coalesce(p.version,0),'budgetVersion',coalesce(p.version,0),
  'policyVersion',coalesce((SELECT version FROM public.risk_policies ORDER BY version DESC
 LIMIT 1),0),
  'limitPolicyVersion',(SELECT version FROM public.paper_limit_policies ORDER BY version DESC
 LIMIT 1),
  'riskBudget',CASE WHEN p.id IS NULL THEN NULL ELSE trim_scale(p.risk_budget)::text END,
  'strategy',p.strategy,'feeAuthority',public.paper_fee_authority(),
  'recoveryFeeAuthority',public.paper_recovery_fee_authority(),
  'activation',public.paper_activation_guard('paper',NULL));
END;
"""

_RESTART = """
DECLARE s public.paper_workflow_state%ROWTYPE; n timestamptz; trace text;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
  OR current_setting('role')<>'atl_paper_execution' THEN RETURN false; END IF;
 SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
 PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 n:=clock_timestamp();
 SELECT trace_id INTO trace FROM public.execution_owners WHERE account_id='paper'
  AND owner_id=p_owner AND fencing_token=p_fence
  AND heartbeat_at BETWEEN n-interval '30 seconds' AND n;
 IF trace IS NULL OR p_owner IS NULL OR p_fence IS NULL OR p_fence<1 THEN RETURN false; END IF;
 PERFORM public.safety_latch('paper','PAUSE_NEW','execution-restart',trace,'PAUSED_NEW');
 IF s.epoch>=9007199254740991 THEN
  UPDATE public.paper_workflow_state SET operating_state=CASE WHEN operating_state='HALTED'
   THEN 'HALTED' ELSE 'PAUSED' END,changed_at=n WHERE singleton;
 ELSE UPDATE public.paper_workflow_state SET epoch=epoch+1,
  operating_state=CASE WHEN operating_state='HALTED' THEN 'HALTED' ELSE 'PAUSED' END,
  changed_at=n WHERE singleton; END IF;
 RETURN true;
END;
"""

_WATCHDOG = """
DECLARE s public.paper_workflow_state%ROWTYPE;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_watchdog_login'
  OR current_setting('role')<>'atl_paper_watchdog' OR p_trace IS NULL
  OR p_trace !~ '^[0-9a-f]{32}$' THEN RETURN false; END IF;
 SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM public.accounts WHERE id='paper') THEN RETURN true; END IF;
 IF s.operating_state='RUNNING' AND public.paper_activation_guard('paper',NULL) IS NULL THEN
  PERFORM public.safety_latch('paper','PAUSE_NEW','paper-watchdog',p_trace,'HEALTH_CHECK_FAILED');
  UPDATE public.paper_workflow_state SET epoch=least(epoch+1,9007199254740991),
   operating_state=CASE WHEN operating_state='HALTED' THEN 'HALTED' ELSE 'PAUSED' END,
   blocker='RECONCILIATION_REQUIRED',changed_at=clock_timestamp() WHERE singleton;
 END IF;
 RETURN true;
END;
"""

_DAILY_REFERENCE = """
DECLARE d public.paper_daily_risk_evidence%ROWTYPE;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
  OR current_setting('role')<>'atl_paper_execution' THEN RETURN NULL; END IF;
 SELECT * INTO d FROM public.paper_daily_risk_evidence WHERE account_id='paper'
  ORDER BY starts_at DESC,observed_at DESC,id DESC LIMIT 1;
 IF d.id IS NULL THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('kind',d.reference_kind,
  'startsAt',to_char(d.starts_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'expiresAt',to_char(d.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'openingLower',trim_scale(d.opening_lower)::text,'openingUpper',trim_scale(d.opening_upper)::text,
  'capitalLower',trim_scale(d.capital_lower)::text,'referenceHash',d.reference_hash,
  'accountCreatedAt',CASE WHEN d.account_created_at IS NULL THEN NULL ELSE to_char(
   d.account_created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END);
END;
"""

_DAILY_APPEND = """
DECLARE a public.risk_account_context%ROWTYPE; ps public.portfolio_snapshots%ROWTYPE;
 prior public.paper_daily_risk_evidence%ROWTYPE; d jsonb; n timestamptz; observed timestamptz;
 starts timestamptz; expires timestamptz; k text; v jsonb; row_id text; trace text;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
  OR current_setting('role')<>'atl_paper_execution' THEN RETURN false; END IF;
 IF p_owner IS NULL OR p_fence IS NULL OR p_fence<1 OR p_portfolio IS NULL OR p_portfolio<1
  OR jsonb_typeof(p_body) IS DISTINCT FROM 'object' OR pg_column_size(p_body)>4096
  OR NOT p_body ?& ARRAY['kind','startsAt','expiresAt','openingLower','openingUpper','capitalLower',
   'referenceHash','accountCreatedAt','observedAt','currentEquity','externalFunding','observationHash']
  OR (p_body-ARRAY['kind','startsAt','expiresAt','openingLower','openingUpper','capitalLower',
   'referenceHash','accountCreatedAt','observedAt','currentEquity','externalFunding',
   'observationHash'])<>'{}'::jsonb THEN RETURN false; END IF;
 FOR k,v IN SELECT * FROM jsonb_each(p_body) LOOP
  IF k='accountCreatedAt' AND v='null'::jsonb THEN CONTINUE; END IF;
  IF jsonb_typeof(v)<>'string' THEN RETURN false; END IF;
  IF k IN ('startsAt','expiresAt','observedAt','accountCreatedAt') THEN
   IF v#>>'{}' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]{8,18}Z$'
    OR length(v#>>'{}')>30 THEN RETURN false; END IF;
  ELSIF k IN ('openingLower','openingUpper','capitalLower','currentEquity','externalFunding') THEN
   IF v#>>'{}' !~ '^-?(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$' THEN RETURN false; END IF;
  ELSIF k IN ('referenceHash','observationHash') THEN
   IF v#>>'{}' !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
  ELSIF k='kind' AND v#>>'{}' NOT IN ('UTC_DAY','FIRST_DAY') THEN RETURN false; END IF;
 END LOOP;
 PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 n:=clock_timestamp();
 SELECT trace_id INTO trace FROM public.execution_owners WHERE account_id='paper'
  AND owner_id=p_owner AND fencing_token=p_fence AND heartbeat_at BETWEEN n-interval
 '30 seconds' AND n;
 IF trace IS NULL THEN RETURN false; END IF;
 SELECT * INTO a FROM public.risk_account_context WHERE account_id='paper' FOR UPDATE;
 SELECT * INTO ps FROM public.portfolio_snapshots WHERE id=a.portfolio_snapshot_id;
 observed:=(p_body->>'observedAt')::timestamptz;
 starts:=(p_body->>'startsAt')::timestamptz; expires:=(p_body->>'expiresAt')::timestamptz;
 IF ps.id IS NULL OR ps.ledger_version<>p_portfolio OR a.portfolio_version<>p_portfolio
  OR ps.currency<>'USD' OR ps.quality<>'VALID' OR ps.reconciliation<>'CURRENT'
  OR ps.equity<>(p_body->>'currentEquity')::numeric OR ps.equity<=0
  OR observed>n OR observed<=n-interval '30 seconds' OR observed<ps.as_of
  OR starts>observed OR expires<=observed
  OR expires<>(date_trunc('day',n AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')+interval '1 day'
  OR (p_body->>'openingLower')::numeric<=0 OR (p_body->>'capitalLower')::numeric<=0
  OR (p_body->>'openingUpper')::numeric<(p_body->>'openingLower')::numeric
  OR (p_body->>'openingUpper')::numeric-(p_body->>'openingLower')::numeric>.02
  OR (p_body->>'capitalLower')::numeric>(p_body->>'openingLower')::numeric
  THEN RETURN false; END IF;
 SELECT * INTO prior FROM public.paper_daily_risk_evidence WHERE account_id='paper'
  ORDER BY starts_at DESC,observed_at DESC,id DESC LIMIT 1;
 IF prior.id IS NULL THEN RETURN false; END IF;
 IF prior.expires_at>n THEN
  IF starts<>prior.starts_at OR expires<>prior.expires_at OR p_body->>'kind'<>prior.reference_kind
   OR (p_body->>'openingLower')::numeric<>prior.opening_lower
   OR (p_body->>'openingUpper')::numeric<>prior.opening_upper
   OR (p_body->>'capitalLower')::numeric<>prior.capital_lower
   OR p_body->>'referenceHash'<>prior.reference_hash
   OR (p_body->>'accountCreatedAt')::timestamptz IS DISTINCT FROM prior.account_created_at
   THEN RETURN false; END IF;
 ELSE
  IF p_body->>'kind'<>'UTC_DAY' OR starts<>date_trunc('day',n AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
   OR p_body->'accountCreatedAt'<>'null'::jsonb OR starts<prior.expires_at THEN RETURN false;
 END IF;
 END IF;
 row_id:='paper-daily-'||encode(sha256(convert_to(p_body::text,'UTF8')),'hex');
 IF EXISTS(SELECT 1 FROM public.paper_daily_risk_evidence WHERE id=row_id) THEN RETURN true; END IF;
 INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
  entity_version,trace_id) VALUES(row_id||'-audit',n,'execution-daily','paper.daily',
  '["PAPER_DAILY_OBSERVED"]',row_id,1,trace);
 INSERT INTO public.paper_daily_risk_evidence VALUES(row_id,'paper',p_portfolio,
  'paper-utc-equity-risk.v1',p_body->>'kind',starts,expires,(p_body->>'openingLower')::numeric,
  (p_body->>'openingUpper')::numeric,(p_body->>'capitalLower')::numeric,
  (p_body->>'externalFunding')::numeric,observed,(p_body->>'currentEquity')::numeric,
  p_body->>'referenceHash',p_body->>'observationHash',(p_body->>'accountCreatedAt')::timestamptz,
  'execution-daily',trace);
 INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
  entity_version,trace_id) VALUES(row_id||'-context-audit',n,'execution-daily','paper.daily',
  '["PAPER_DAILY_OBSERVED"]','paper',a.version+1,trace);
 UPDATE public.risk_account_context SET version=version+1,
  valid_until=least(observed+interval
 '120 seconds',expires),actor_id='execution-daily',trace_id=trace
  WHERE account_id='paper';
 RETURN true;
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow OR numeric_value_out_of_range
 OR invalid_text_representation THEN RETURN false;
END;
"""


def _rename(name, args, roles):
    op.execute(f"ALTER FUNCTION public.{name}({args}) RENAME TO {name}_v15")
    op.execute(f"REVOKE ALL ON FUNCTION public.{name}_v15({args}) FROM PUBLIC")
    for role in roles.split(","):
        op.execute(
            "DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='" + role + "') THEN "
            f"REVOKE ALL ON FUNCTION public.{name}_v15({args}) FROM {role}; "
            "END IF; END $$;"
        )


def _replace_control_ports():
    _rename("paper_enqueue", "text,jsonb,text,text,text", "atl_paper_control_api")
    _function(
        "paper_enqueue",
        "p_kind text,p_body jsonb,p_actor text,p_key text,p_trace text",
        "jsonb",
        """
    DECLARE existed boolean; result jsonb; s public.paper_workflow_state%ROWTYPE; command_id uuid;
    BEGIN
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_control_api_login'
       OR current_setting('role')<>'atl_paper_control_api' THEN RETURN NULL; END IF;
      PERFORM singleton FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
      SELECT EXISTS(SELECT 1 FROM public.paper_commands WHERE actor_id=p_actor
       AND idempotency_key=p_key) INTO existed;
      result:=public.paper_enqueue_v15(p_kind,p_body,p_actor,p_key,p_trace);
      IF NOT existed AND p_kind IN ('PAUSE','STOP','KILL')
        AND result->>'error'='VERSION_CONFLICT' THEN
        SELECT * INTO s FROM public.paper_workflow_state WHERE singleton;
        IF s.epoch=9007199254740991 THEN
          UPDATE public.paper_workflow_state SET operating_state=CASE
            WHEN p_kind='KILL' OR operating_state='HALTED' THEN 'HALTED'
            WHEN p_kind='STOP' THEN 'STOPPED' ELSE 'PAUSED' END,changed_at=clock_timestamp()
            WHERE singleton;
          INSERT INTO
 public.paper_commands(actor_id,idempotency_key,operation_type,payload,trace_id,
            accepted_generation,credential_generation,status)
            VALUES(p_actor,p_key,p_kind,p_body,p_trace,s.epoch,s.saved_generation,'ACCEPTED')
            RETURNING id INTO command_id;
          INSERT INTO public.paper_command_events(operation_id,event_type,epoch)
            VALUES(command_id,'LATCH_CLOSED',s.epoch);
          result:=public.paper_operation(command_id);
        END IF;
      END IF;
      IF NOT existed AND result ? 'operationId' AND p_kind IN ('PAUSE','STOP','KILL')
       AND EXISTS(SELECT 1 FROM public.risk_account_context WHERE account_id='paper') THEN
        SELECT * INTO s FROM public.paper_workflow_state WHERE singleton;
        PERFORM public.safety_latch('paper',CASE WHEN s.operating_state='HALTED' THEN 'HALTED'
         ELSE 'PAUSE_NEW' END,p_actor,p_trace,CASE WHEN s.operating_state='HALTED'
         THEN 'KILL_SWITCH_ACTIVE' ELSE 'PAUSED_NEW' END);
      END IF;
      RETURN result;
    END;
    """,
    )
    _rename("paper_rotation_begin", "text,text,text", "atl_paper_control_api")
    _function(
        "paper_rotation_begin",
        "p_actor text,p_key text,p_trace text",
        "bigint",
        """
    DECLARE existed boolean; generation bigint;
    BEGIN
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_control_api_login'
       OR current_setting('role')<>'atl_paper_control_api' THEN RETURN NULL; END IF;
      PERFORM singleton FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
      SELECT EXISTS(SELECT 1 FROM public.paper_rotation_receipts WHERE actor_id=p_actor
       AND idempotency_key=p_key) INTO existed;
      generation:=public.paper_rotation_begin_v15(p_actor,p_key,p_trace);
      IF generation IS NOT NULL AND NOT existed AND EXISTS(SELECT 1 FROM public.risk_account_context
       WHERE account_id='paper') THEN
        PERFORM public.safety_latch('paper','PAUSE_NEW',p_actor,p_trace,'PAUSED_NEW'); END IF;
      RETURN generation;
    END;
    """,
    )
    _rename("paper_finish", "uuid,uuid,bigint,jsonb,uuid,text", "atl_paper_execution")
    _function(
        "paper_finish",
        "p_id uuid,p_owner uuid,p_fence bigint,p_public jsonb,p_private uuid,p_error text",
        "boolean",
        """
    BEGIN
      IF EXISTS(SELECT 1 FROM public.paper_commands WHERE id=p_id AND operation_type='START')
       AND p_error IS NULL THEN RETURN false; END IF;
      RETURN public.paper_finish_v15(p_id,p_owner,p_fence,p_public,p_private,p_error);
    END;
    """,
    )
    op.execute("""
    DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_control_api') THEN
    GRANT EXECUTE ON FUNCTION public.paper_enqueue(text,jsonb,text,text,text),
      public.paper_rotation_begin(text,text,text) TO atl_paper_control_api;
    END IF;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_execution') THEN
    GRANT EXECUTE ON FUNCTION public.paper_finish(uuid,uuid,bigint,jsonb,uuid,text)
      TO atl_paper_execution;
    END IF; END $$;
    """)


def _replace_plan_record():
    _rename("paper_planner_inputs", "text", "atl_paper_worker,atl_paper_execution")
    # Status/watchdog may call the owner-only internal source predicate. Neither
    # receives EXECUTE on the raw projection; their public ports return a gate.
    op.execute("""
    DO $$ DECLARE definition text; name text; BEGIN
      FOREACH name IN ARRAY ARRAY['public.paper_planner_inputs_v15(text)',
        'public.paper_limit_policy()'] LOOP
        SELECT pg_get_functiondef(name::regprocedure) INTO definition;
        IF position('session_user=''atl_paper_execution_login''' IN definition)=0 THEN
          RAISE EXCEPTION 'unexpected Paper proof identity definition'; END IF;
        definition:=replace(definition,
          '(session_user=''atl_paper_execution_login'' AND '
 'current_setting(''role'')=''atl_paper_execution'')',
          '((session_user=''atl_paper_execution_login'' AND '
 'current_setting(''role'')=''atl_paper_execution'')'
          ||' OR (session_user=''atl_paper_watchdog_login'' AND '
 'current_setting(''role'')=''atl_paper_watchdog'')'
          ||' OR (session_user=''atl_paper_control_api_login'' AND '
 'current_setting(''role'')=''atl_paper_control_api''))');
        EXECUTE definition;
      END LOOP;
    END $$;
    """)
    _function(
        "paper_planner_inputs",
        "p_state text",
        "jsonb",
        """
    DECLARE result jsonb; activation jsonb; expiry timestamptz;
    BEGIN
      result:=public.paper_planner_inputs_v15(p_state);
      IF result IS NULL THEN RETURN NULL; END IF;
      activation:=public.paper_activation_guard('paper',NULL);
      IF activation IS NOT NULL THEN
       expiry:=least((result->>'approval_valid_until')::timestamptz,
        (activation->>'expiresAt')::timestamptz);
       result:=jsonb_set(result,'{approval_valid_until}',to_jsonb(to_char(expiry AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))); END IF;
      RETURN result;
    END;
    """,
    )
    _rename("paper_planner_record", "text,text,text,text,bigint", "atl_paper_worker")
    _function(
        "paper_planner_record",
        "p_proposal text,p_hash text,p_job text,p_owner text,p_fence bigint",
        "boolean",
        """
    DECLARE activation jsonb; p public.trade_proposals%ROWTYPE; row_id text;
    BEGIN
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_worker_login'
       OR current_setting('role')<>'atl_paper_worker' THEN RETURN false; END IF;
      PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
      activation:=public.paper_activation_guard('paper',NULL);
      SELECT * INTO p FROM public.trade_proposals WHERE id=p_proposal;
      IF activation IS NULL OR p.id IS NULL OR NOT (activation->'symbols') ? p.instrument_id
       OR NOT EXISTS(SELECT 1 FROM public.decisions d WHERE d.id=p.decision_id
        AND d.config_version='runtime-decision-v1:'||(activation->>'strategy')||'.v1')
       OR p.valid_until>(activation->>'expiresAt')::timestamptz THEN RETURN false; END IF;
      IF NOT public.paper_planner_record_v15(p_proposal,p_hash,p_job,p_owner,p_fence)
       THEN RETURN false; END IF;
      IF EXISTS(SELECT 1 FROM public.paper_proposal_activations WHERE proposal_id=p_proposal) THEN
       RETURN EXISTS(SELECT 1 FROM public.paper_proposal_activations WHERE proposal_id=p_proposal
        AND activation_id=(activation->>'activationId')::uuid); END IF;
      row_id:='paper-activation-link-'||encode(sha256(convert_to(p_proposal,'UTF8')),'hex');
      INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
       entity_version,trace_id) VALUES(row_id||'-audit',clock_timestamp(),'worker-planner',
       'paper.proposal_activation','["PAPER_PROPOSAL_BOUND"]',row_id,1,p.trace_id);
      INSERT INTO public.paper_proposal_activations VALUES(row_id,p_proposal,
       (activation->>'activationId')::uuid,'worker-planner',p.trace_id);
      RETURN true;
    END;
    """,
    )
    op.execute("""
    DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_worker') THEN
      GRANT EXECUTE ON FUNCTION public.paper_planner_inputs(text),
        public.paper_planner_record(text,text,text,text,bigint) TO atl_paper_worker;
    END IF;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_execution') THEN
      GRANT EXECUTE ON FUNCTION public.paper_planner_inputs(text) TO atl_paper_execution;
    END IF; END $$;
    """)


def _replace_status():
    _function("paper_status", "p_trace text", "jsonb", _STATUS)


_STATUS = """
DECLARE s public.paper_workflow_state%ROWTYPE; gate jsonb; n timestamptz:=clock_timestamp();
 effective text; blockers jsonb; reconciled timestamptz; budget numeric;
BEGIN
 SELECT * INTO s FROM public.paper_workflow_state WHERE singleton;
 gate:=public.paper_activation_guard('paper',NULL);
 effective:=CASE WHEN s.operating_state='RUNNING' AND gate IS NULL THEN 'PAUSED'
  ELSE s.operating_state END;
 blockers:=CASE WHEN s.operating_state='RUNNING' AND gate IS NULL THEN jsonb_build_array(
  CASE WHEN EXISTS(SELECT 1 FROM public.paper_activations WHERE epoch=s.epoch AND expires_at<=n)
   THEN 'APPROVAL_EXPIRED' ELSE 'RECONCILIATION_REQUIRED' END)
  WHEN s.blocker='OK' THEN '[]'::jsonb ELSE jsonb_build_array(s.blocker) END;
 SELECT observed_at INTO reconciled FROM public.paper_reconciliation_receipts
  WHERE account_id='paper' AND ready ORDER BY observed_at DESC,id DESC LIMIT 1;
 SELECT risk_budget INTO budget FROM public.paper_profile_versions ORDER BY version DESC LIMIT 1;
 RETURN jsonb_build_object('namespace','alpaca-paper','environment','PAPER',
  'setupState',CASE WHEN s.verified_at IS NOT NULL AND EXISTS(SELECT 1 FROM
 public.paper_initial_bases)
   THEN 'PREPARED' WHEN s.verified_at IS NOT NULL THEN 'VERIFIED'
   WHEN s.saved_generation>0 THEN 'SAVED' ELSE 'NOT_CONFIGURED' END,
  'operatingState',effective,'entryGateOpen',gate IS NOT NULL,
  'credentialGeneration',coalesce(s.pending_generation,s.active_generation,s.saved_generation),
  'accountGeneration',s.account_generation,'accountLabel',s.account_label,'currency','USD',
  'verifiedAt',CASE WHEN s.verified_at IS NULL THEN NULL ELSE to_char(s.verified_at AT TIME
 ZONE 'UTC',
   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
  'reconciledAt',CASE WHEN reconciled IS NULL THEN NULL ELSE to_char(reconciled AT TIME ZONE 'UTC',
   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
  'observedAt',to_char(n AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'marketDataFeed',CASE WHEN s.verified_at IS NULL THEN 'UNVERIFIED' ELSE 'IEX' END,
  'providerVerification',CASE WHEN s.verified_at IS NULL THEN 'UNVERIFIED' ELSE 'CONFIRMED' END,
  'blockers',blockers,'traceId',p_trace)
  || CASE WHEN s.cash IS NULL THEN '{}'::jsonb ELSE
 jsonb_build_object('cash',trim_scale(s.cash)::text) END
  || CASE WHEN budget IS NULL THEN '{}'::jsonb ELSE
 jsonb_build_object('riskBudget',trim_scale(budget)::text) END;
END;
"""
