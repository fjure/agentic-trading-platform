"""Immutable Paper LIMIT policy and private-proof-derived worker inputs."""

from importlib import import_module

from alembic import op

revision = "0015_paper_limit_policy"
down_revision = "0014_paper_initial_basis"
branch_labels = None
depends_on = None


def upgrade():
    migration_function = import_module(
        "services.control_api.migrations.versions.0013_paper_commands"
    ).function

    def function(name, args, result, body, *, block=False):
        if args:
            migration_function(name, args, result, body, block=block)
        else:
            op.execute(
                f"CREATE FUNCTION public.{name}() RETURNS {result} LANGUAGE plpgsql "
                "SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN "
                + body
                + " END; $$"
            )
            op.execute(f"REVOKE ALL ON FUNCTION public.{name}() FROM PUBLIC")

    op.execute("""
    CREATE TABLE public.paper_limit_policies (
      version integer PRIMARY KEY CHECK(version>0), policy jsonb NOT NULL,
      actor_id text NOT NULL, trace_id varchar(32) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE public.paper_quote_observations (
      id text PRIMARY KEY, market_state_id varchar(255) UNIQUE NOT NULL
        REFERENCES public.market_states(id),
      account_id varchar(255) NOT NULL CHECK(account_id='paper'),
      instrument_id varchar(255) NOT NULL,
      context_version integer NOT NULL,
      quote jsonb NOT NULL, evidence_hash text NOT NULL CHECK(evidence_hash~'^[0-9a-f]{64}$'),
      actor_id text NOT NULL, trace_id varchar(32) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      FOREIGN KEY(account_id,instrument_id,context_version) REFERENCES
        public.broker_binding_proofs(account_id,instrument_id,context_version)
    );
    CREATE TABLE public.paper_limit_plans (
      proposal_id varchar(255) PRIMARY KEY REFERENCES public.trade_proposals(id),
      id text UNIQUE NOT NULL, inputs jsonb NOT NULL,
      input_hash text NOT NULL CHECK(input_hash~'^[0-9a-f]{64}$'),
      actor_id text NOT NULL, trace_id varchar(32) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    REVOKE ALL ON public.paper_limit_plans FROM PUBLIC;
    CREATE TRIGGER paper_limit_plans_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
      ON public.paper_limit_plans FOR EACH STATEMENT
      EXECUTE FUNCTION public.ledger_reject_mutation();
    CREATE CONSTRAINT TRIGGER paper_limit_plans_audit AFTER INSERT ON public.paper_limit_plans
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION public.paper_reconciliation_require_audit();
    REVOKE ALL ON public.paper_limit_policies,public.paper_quote_observations FROM PUBLIC;
    CREATE TRIGGER paper_limit_policy_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
      ON public.paper_limit_policies FOR EACH STATEMENT
      EXECUTE FUNCTION public.ledger_reject_mutation();
    CREATE TRIGGER paper_quote_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
      ON public.paper_quote_observations FOR EACH STATEMENT
      EXECUTE FUNCTION public.ledger_reject_mutation();
    CREATE CONSTRAINT TRIGGER paper_quote_audit AFTER INSERT ON public.paper_quote_observations
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION public.paper_reconciliation_require_audit();
    INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
      entity_version,trace_id) VALUES('paper-limit-policy-1-audit',clock_timestamp(),
      'migration','paper.limit_policy','["PAPER_LIMIT_POLICY_SEEDED"]',
      'paper-limit-policy-1',1,'00000000000000000000000000000015');
    INSERT INTO public.paper_limit_policies VALUES(1,
      '{"version": 1,"proposal_ttl_seconds": 30,"quote_age_seconds": 30,
        "capability_age_seconds": 120,"max_price_distance_fraction":"0.001",
        "quantity_decimals": 9,"cash_buffer_fraction":"0.01","max_position_fraction":"0.10",
        "order_type":"LIMIT","time_in_force":"DAY","session":"REGULAR","currency":"USD"}',
      'migration','00000000000000000000000000000015',clock_timestamp());
    """)
    function(
        "paper_limit_policy",
        "",
        "jsonb",
        """
      IF current_database()<>'lab_paper_runtime' OR NOT (
        (session_user='atl_paper_execution_login' AND current_setting('role')='atl_paper_execution')
        OR (session_user='atl_paper_worker_login' AND current_setting('role')='atl_paper_worker'))
      THEN RETURN NULL; END IF;
      RETURN (SELECT policy FROM public.paper_limit_policies ORDER BY version DESC LIMIT 1);
    """,
    )
    function(
        "paper_quote_append",
        "p_state text,p_context integer,p_owner text,p_fence bigint,p_quote jsonb",
        "jsonb",
        """
    DECLARE s public.market_states%ROWTYPE; i public.risk_instrument_context%ROWTYPE;
      b public.broker_binding_proofs%ROWTYPE; n timestamptz:=clock_timestamp();
      pt timestamptz; ot timestamptz; bid numeric; ask numeric; h text; qid text;
      prior public.paper_quote_observations%ROWTYPE;
    BEGIN
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
        OR current_setting('role')<>'atl_paper_execution' THEN RETURN NULL; END IF;
      PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
      n:=clock_timestamp();
      IF NOT FOUND OR p_owner IS NULL OR p_fence IS NULL OR p_fence<1
        OR NOT EXISTS(SELECT 1 FROM public.execution_owners WHERE account_id='paper'
          AND owner_id=p_owner AND fencing_token=p_fence
          AND heartbeat_at BETWEEN n-interval '30 seconds' AND n)
        THEN RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
      IF p_state IS NULL OR p_context IS NULL OR p_context<1
        OR jsonb_typeof(p_quote) IS DISTINCT FROM 'object'
        OR NOT p_quote ?& ARRAY['sourceId','bid','ask','providerTime','observedAt',
          'sourceCapabilityVersion']
        OR (p_quote-ARRAY['sourceId','bid','ask','providerTime','observedAt',
          'sourceCapabilityVersion'])<>'{}'::jsonb
        THEN RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
      IF EXISTS(SELECT 1 FROM jsonb_each(p_quote) e WHERE e.key<>'sourceCapabilityVersion'
          AND jsonb_typeof(e.value)<>'string')
        OR (p_quote->>'sourceId') !~ '^[A-Za-z0-9._:-]{1,255}$'
        OR (p_quote->>'sourceId') NOT IN ('alpaca-market-data:iex','alpaca-market-data:sip')
        OR (p_quote->>'bid') !~ '^(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$'
        OR (p_quote->>'ask') !~ '^(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$'
        OR jsonb_typeof(p_quote->'sourceCapabilityVersion')<>'number'
        OR (p_quote->>'sourceCapabilityVersion') !~ '^[1-9][0-9]{0,15}$'
        OR (p_quote->>'sourceCapabilityVersion')::numeric>9007199254740991
        OR (p_quote->>'providerTime') !~
 '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d{1,6})?Z$'
        OR (p_quote->>'observedAt') !~ '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d{1,6})?Z$'
        THEN RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
      bid:=(p_quote->>'bid')::numeric; ask:=(p_quote->>'ask')::numeric;
      pt:=(p_quote->>'providerTime')::timestamptz; ot:=(p_quote->>'observedAt')::timestamptz;
      IF bid<=0 OR ask<bid OR pt>ot OR ot>n OR pt<=n-interval '30 seconds'
        THEN RETURN jsonb_build_object('error','RECONCILIATION_REQUIRED'); END IF;
      SELECT * INTO s FROM public.market_states WHERE id=p_state;
      SELECT * INTO i FROM public.risk_instrument_context WHERE account_id='paper'
        AND instrument_id=s.instrument_id AND version=p_context;
      SELECT * INTO b FROM public.broker_binding_proofs WHERE account_id='paper'
        AND instrument_id=s.instrument_id AND context_version=p_context;
      IF s.id IS NULL OR i.version IS NULL OR b.context_version IS NULL
        OR s.instrument_id NOT IN ('SPY','QQQ') OR s.market<>'US_EQUITY' OR s.quality<>'VALID'
        OR s.spread<>ask-bid OR s.available_at>n OR s.as_of<=n-interval '120 seconds'
        OR NOT EXISTS(SELECT 1 FROM public.evidence_bundles e WHERE e.id=s.evidence_id
          AND e.instrument_id=s.instrument_id AND e.quality='VALID' AND e.neutral
          AND e.sources ? (p_quote->>'sourceId'))
        OR i.broker_id<>'alpaca-paper' OR NOT i.paper_confirmed
        OR NOT i.limit_order_confirmed OR NOT i.regular_session_confirmed
        OR NOT i.session_open OR i.valid_until<=n OR i.session_valid_until<=n
        OR i.capability_version<>b.trading_capability_version OR b.valid_until<=n
        OR b.source_capability_version<>(p_quote->>'sourceCapabilityVersion')::bigint
        OR b.session_start>n OR b.session_end<=n
        OR b.market_observed_at>n OR b.trade_observed_at>n
        OR least(b.market_observed_at,b.trade_observed_at)<=n-interval '120 seconds'
        OR least(b.market_provider_time,b.trade_provider_time)<=n-interval '120 seconds'
        OR greatest(b.market_provider_time,b.trade_provider_time)>n
        OR NOT EXISTS(SELECT 1 FROM public.broker_account_bindings a
          JOIN public.broker_asset_bindings v ON v.account_id=a.account_id
          WHERE a.account_id='paper' AND v.instrument_id=s.instrument_id
          AND a.provider_account_id=b.provider_account_id AND
 v.provider_asset_id=b.provider_asset_id)
        THEN RETURN jsonb_build_object('error','RECONCILIATION_REQUIRED'); END IF;

 h:=encode(sha256(convert_to(jsonb_build_array(p_state,p_context,p_quote)::text,'UTF8')),'hex');
      qid:='paper-quote-'||h;
      SELECT * INTO prior FROM public.paper_quote_observations WHERE market_state_id=p_state;
      IF FOUND THEN
        IF prior.context_version<>p_context OR prior.evidence_hash<>h THEN
          RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
        RETURN jsonb_build_object('quoteId',prior.id,'quoteHash',prior.evidence_hash);
      END IF;
      INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
        entity_version,trace_id) VALUES(qid||'-audit',n,'execution-quote','paper.quote',
        '["PAPER_QUOTE_OBSERVED"]',qid,1,s.trace_id);
      INSERT INTO public.paper_quote_observations VALUES(qid,p_state,'paper',s.instrument_id,
        p_context,p_quote,h,'execution-quote',s.trace_id,n);
      RETURN jsonb_build_object('quoteId',qid,'quoteHash',h);
    EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow OR numeric_value_out_of_range
      OR invalid_text_representation THEN RETURN jsonb_build_object('error','INVALID_REQUEST');
    END;
    """,
        block=True,
    )
    function("paper_planner_inputs", "p_state text", "jsonb", _PLANNER, block=True)
    function(
        "paper_planner_record",
        "p_proposal text,p_hash text,p_job text,p_owner text,p_fence bigint",
        "boolean",
        """
    DECLARE j public.jobs%ROWTYPE; p public.trade_proposals%ROWTYPE;
      proof jsonb; prior public.paper_limit_plans%ROWTYPE; row_id text;
    BEGIN
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_worker_login'
        OR current_setting('role')<>'atl_paper_worker' THEN RETURN false; END IF;
      PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
      IF NOT FOUND OR p_proposal IS NULL OR p_hash IS NULL OR p_job IS NULL
        OR p_owner IS NULL OR p_fence IS NULL OR p_fence<1
        OR p_hash !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
      SELECT * INTO j FROM public.jobs WHERE id=p_job FOR UPDATE;
      IF j.id IS NULL OR j.type<>'decision' OR j.status<>'LEASED'
        OR j.lease_owner IS DISTINCT FROM p_owner OR j.fencing_token IS DISTINCT FROM p_fence
        OR j.lease_until IS NULL OR j.lease_until<=clock_timestamp() THEN RETURN false; END IF;
      SELECT * INTO p FROM public.trade_proposals WHERE id=p_proposal;
      IF p.id IS NULL OR p.market_state_id<>j.input_ref OR p.trace_id<>j.trace_id
        OR p.limit_price IS NULL OR p.limit_price<=0 OR p.valid_until<=clock_timestamp()
        OR NOT p.rationale_codes ? ('PAPER_INPUT_SHA256:'||p_hash)
        OR NOT EXISTS(SELECT 1 FROM public.decisions d WHERE d.id=p.decision_id
          AND d.market_state_id=p.market_state_id AND d.trace_id=p.trace_id)
        THEN RETURN false; END IF;
      proof:=public.paper_planner_inputs(j.input_ref);
      IF proof IS NULL OR NOT p.rationale_codes ? ('PAPER_QUOTE:'||
        (proof->>'quote_evidence_id')) THEN RETURN false; END IF;
      SELECT * INTO prior FROM public.paper_limit_plans WHERE proposal_id=p_proposal;
      IF FOUND THEN RETURN prior.input_hash=p_hash AND prior.inputs=proof; END IF;
      row_id:='paper-limit-plan-'||encode(sha256(convert_to(p_proposal,'UTF8')),'hex');
      INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
        entity_version,trace_id) VALUES(row_id||'-audit',clock_timestamp(),'worker-planner',
        'paper.limit_plan','["PAPER_LIMIT_PLAN_RECORDED"]',row_id,1,p.trace_id);
      INSERT INTO public.paper_limit_plans VALUES(p_proposal,row_id,proof,p_hash,
        'worker-planner',p.trace_id,clock_timestamp());
      RETURN true;
    END;
    """,
        block=True,
    )
    function(
        "paper_limit_plan_read",
        "p_proposal text",
        "jsonb",
        """
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
        OR current_setting('role')<>'atl_paper_execution' THEN RETURN NULL; END IF;
      RETURN (SELECT jsonb_build_object('inputHash',input_hash,'inputs',inputs)
        FROM public.paper_limit_plans WHERE proposal_id=p_proposal);
    """,
    )
    op.execute("""
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_worker') THEN
        GRANT USAGE ON SCHEMA public TO atl_paper_worker;
        GRANT EXECUTE ON FUNCTION public.paper_planner_inputs(text),public.paper_limit_policy()
          TO atl_paper_worker;
        GRANT EXECUTE ON FUNCTION public.paper_planner_record(text,text,text,text,bigint)
          TO atl_paper_worker;
        GRANT SELECT ON public.instruments,public.market_bars,public.evidence_bundles,
          public.market_states,public.positions,public.position_versions,public.portfolio_snapshots,
          public.decisions,public.trade_proposals,public.agent_runs,public.audit_events,
          public.trading_profiles,public.jobs,public.job_recovery_checks,public.outbox_events,
          public.order_intents TO atl_paper_worker;
        GRANT INSERT ON public.jobs,public.job_recovery_checks,public.audit_events,
          public.decisions,public.trade_proposals,public.agent_runs,public.outbox_events TO
 atl_paper_worker;
        GRANT UPDATE(status,lease_owner,lease_until,fencing_token,attempt,not_before,heartbeat,
          error_code,completed_at) ON public.jobs TO atl_paper_worker;
        GRANT UPDATE(event_id) ON public.outbox_events TO atl_paper_worker;
      END IF;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_execution') THEN
        GRANT EXECUTE ON FUNCTION public.paper_quote_append(text,integer,text,bigint,jsonb),
          public.paper_planner_inputs(text),
          public.paper_limit_plan_read(text),
          public.paper_limit_policy() TO atl_paper_execution;
        GRANT SELECT ON public.risk_instrument_context TO atl_paper_execution;
        GRANT SELECT ON public.trade_proposals,public.decisions,public.market_states,
          public.market_bars,public.evidence_bundles,public.position_versions,
          public.risk_policies,public.trading_profiles TO atl_paper_execution;
      END IF;
    END $$;
    """)


_PLANNER = """
DECLARE s public.market_states%ROWTYPE; a public.risk_account_context%ROWTYPE;
 i public.risk_instrument_context%ROWTYPE; b public.broker_binding_proofs%ROWTYPE;
 q public.paper_quote_observations%ROWTYPE; w public.paper_workflow_state%ROWTYPE;
 p public.positions%ROWTYPE; snap public.portfolio_snapshots%ROWTYPE;
 profile public.trading_profiles%ROWTYPE; policy public.risk_policies%ROWTYPE;
 n timestamptz:=clock_timestamp(); held numeric:=0; instrument_held numeric:=0;
 reserved numeric:=0; instrument_reserved numeric:=0; exits numeric:=0;
 item record; mark numeric; bid numeric; ask numeric; pt timestamptz; ot timestamptz;
 qty numeric; result jsonb; lp jsonb;
BEGIN
 IF current_database()<>'lab_paper_runtime' OR NOT (
   (session_user='atl_paper_worker_login' AND current_setting('role')='atl_paper_worker')
   OR (session_user='atl_paper_execution_login' AND current_setting('role')='atl_paper_execution'))
  THEN RETURN NULL; END IF;
 SELECT * INTO s FROM public.market_states WHERE id=p_state;
 SELECT * INTO a FROM public.risk_account_context WHERE account_id='paper';
 SELECT * INTO p FROM public.positions WHERE id=s.position_id AND account_id='paper';
 SELECT * INTO snap FROM public.portfolio_snapshots WHERE id=a.portfolio_snapshot_id;
 SELECT * INTO i FROM public.risk_instrument_context WHERE account_id='paper'
  AND instrument_id=s.instrument_id;
 SELECT * INTO b FROM public.broker_binding_proofs WHERE account_id='paper'
  AND instrument_id=s.instrument_id AND context_version=i.version;
 SELECT * INTO q FROM public.paper_quote_observations WHERE market_state_id=s.id;
 SELECT * INTO w FROM public.paper_workflow_state WHERE singleton;
 SELECT * INTO profile FROM public.trading_profiles WHERE account_id='paper';
 SELECT * INTO policy FROM public.risk_policies ORDER BY version DESC LIMIT 1;
 lp:=public.paper_limit_policy();
 IF s.id IS NULL OR a.account_id IS NULL OR p.id IS NULL OR snap.id IS NULL
  OR i.version IS NULL OR b.context_version IS NULL OR q.id IS NULL
  OR profile.version IS NULL OR profile.version<1 OR profile.budget<=0
  OR profile.currency<>'USD' OR policy.version IS NULL OR policy.version<1
  OR policy.effective_at>n OR policy.currency<>'USD' OR lp IS NULL
  OR w.active_generation<1 OR w.account_generation<1 OR w.verified_at IS NULL
  OR w.saved_generation<>w.active_generation OR w.pending_generation IS NOT NULL
  OR s.instrument_id NOT IN ('SPY','QQQ') OR s.market<>'US_EQUITY' OR s.quality<>'VALID'
  OR s.portfolio_id<>snap.id OR s.position_ledger_version<>p.ledger_version
  OR p.instrument_id<>s.instrument_id OR p.currency<>'USD'
  OR snap.ledger_version<>a.portfolio_version OR snap.currency<>'USD'
  OR snap.quality<>'VALID' OR snap.reconciliation<>'CURRENT'
  OR NOT a.reconciliation_current OR a.valid_until<=n
  OR s.available_at>n OR s.as_of>n OR s.as_of<=n-interval '120 seconds'
  OR snap.as_of>n OR snap.as_of<=n-interval '120 seconds'
  OR q.context_version<>i.version OR q.instrument_id<>s.instrument_id
  OR i.capability_version<>b.trading_capability_version OR i.broker_id<>'alpaca-paper'
  OR NOT i.paper_confirmed OR NOT i.limit_order_confirmed
  OR NOT i.regular_session_confirmed OR NOT i.session_open
  OR i.valid_until<=n OR i.session_valid_until<=n OR b.valid_until<=n
  OR b.session_start>n OR b.session_end<=n
  OR least(b.market_observed_at,b.trade_observed_at)<=n-interval '120 seconds'
  OR greatest(b.market_observed_at,b.trade_observed_at)>n
  OR least(b.market_provider_time,b.trade_provider_time)<=n-interval '120 seconds'
  OR greatest(b.market_provider_time,b.trade_provider_time)>n
  OR b.source_capability_version<>(q.quote->>'sourceCapabilityVersion')::bigint
  OR NOT EXISTS(SELECT 1 FROM public.broker_account_bindings ab
   JOIN public.broker_asset_bindings av ON av.account_id=ab.account_id
   WHERE ab.account_id='paper' AND av.instrument_id=s.instrument_id
   AND ab.provider_account_id=b.provider_account_id AND av.provider_asset_id=b.provider_asset_id)
  OR NOT EXISTS(SELECT 1 FROM public.accounts WHERE id='paper' AND
 ledger_version=snap.ledger_version)
  OR EXISTS(SELECT 1 FROM public.positions WHERE account_id IS DISTINCT FROM 'paper'
   OR currency<>'USD' OR quantity<0)
  OR EXISTS(SELECT 1 FROM public.risk_reservations WHERE account_id IS DISTINCT FROM 'paper'
   OR currency<>'USD' OR state='HELD_UNKNOWN') THEN RETURN NULL; END IF;
 lp:=jsonb_set(lp,'{max_position_fraction}',to_jsonb(trim_scale(least(
  (lp->>'max_position_fraction')::numeric,policy.max_position_fraction))::text));
 bid:=(q.quote->>'bid')::numeric; ask:=(q.quote->>'ask')::numeric;
 pt:=(q.quote->>'providerTime')::timestamptz; ot:=(q.quote->>'observedAt')::timestamptz;
 IF bid<=0 OR ask<bid OR pt>ot OR ot>n OR pt<=n-interval '30 seconds'
  OR ask-bid<>s.spread THEN RETURN NULL; END IF;
 FOR item IN SELECT * FROM public.positions WHERE account_id='paper' AND quantity>0 LOOP
  SELECT bar.close INTO mark FROM public.market_states ms
   JOIN public.market_bars bar ON bar.id=ms.bar_id
   WHERE ms.instrument_id=item.instrument_id AND ms.position_id=item.id
    AND ms.position_ledger_version=item.ledger_version AND ms.portfolio_id=snap.id
    AND ms.quality='VALID' AND ms.available_at<=n AND ms.as_of<=n
    AND ms.as_of>n-interval '120 seconds'
    AND bar.quality='VALID' AND bar.closed AND bar.close>0
   ORDER BY ms.as_of DESC,ms.id DESC LIMIT 1;
  IF mark IS NULL THEN RETURN NULL; END IF;
  IF item.instrument_id=s.instrument_id THEN mark:=greatest(mark,ask);
   instrument_held:=item.quantity*mark; END IF;
  held:=held+item.quantity*mark;
 END LOOP;
 held:=greatest(held,snap.exposure);
 held:=ceil(held*1000000000000000000)*0.000000000000000001;
 instrument_held:=ceil(instrument_held*1000000000000000000)*0.000000000000000001;
 IF held>=100000000000000000000 OR instrument_held>=100000000000000000000
  THEN RETURN NULL; END IF;
 IF EXISTS(SELECT 1 FROM public.order_intents oi
  LEFT JOIN public.risk_reservations r ON r.proposal_id=oi.proposal_id
  LEFT JOIN public.broker_orders bo ON bo.intent_id=oi.id
  WHERE oi.account_id='paper' AND (r.id IS NULL OR bo.status='UNKNOWN'
   OR (bo.status IN ('CREATED','RISK_RESERVED','SUBMITTING','ACKNOWLEDGED',
    'PARTIALLY_FILLED','CANCEL_PENDING') AND r.state<>'ACTIVE'))) THEN RETURN NULL; END IF;
 FOR item IN SELECT r.id,r.reserved_notional,tp.instrument_id,tp.target,tp.quantity,
   oi.side,bo.filled_quantity,bo.status FROM public.risk_reservations r
  LEFT JOIN public.trade_proposals tp ON tp.id=r.proposal_id
  LEFT JOIN public.order_intents oi ON oi.proposal_id=tp.id
  LEFT JOIN public.broker_orders bo ON bo.intent_id=oi.id
  WHERE r.account_id='paper' AND r.state='ACTIVE' LOOP
  IF item.target IS NULL OR item.quantity<=0 OR item.reserved_notional<0
   OR item.target NOT IN ('LONG','FLAT')
   OR (item.side IS NOT NULL AND item.side<>CASE WHEN item.target='LONG' THEN 'BUY' ELSE 'SELL' END)
   THEN RETURN NULL; END IF;
  qty:=item.quantity-coalesce(item.filled_quantity,0);
  IF qty<0 THEN RETURN NULL; END IF;
  IF item.target='LONG' THEN reserved:=reserved+item.reserved_notional;
   IF item.instrument_id=s.instrument_id THEN
    instrument_reserved:=instrument_reserved+item.reserved_notional; END IF;
  ELSIF item.instrument_id=s.instrument_id THEN exits:=exits+qty; END IF;
 END LOOP;
 result:=jsonb_build_object('policy',lp,'market_state_id',s.id,'market_evidence_id',s.evidence_id,
  'trace_id',s.trace_id,'quote_source_id',q.quote->>'sourceId','quote_evidence_id',q.id,

 'quote_evidence_hash',q.evidence_hash,'context_version',i.version,'quote_context_version',q.context_version,
  'portfolio_version',snap.ledger_version,'account_generation',w.account_generation,
  'credential_generation',w.active_generation,'policy_version',policy.version,
  'profile_version',profile.version,'limit_policy_version',(lp->>'version')::integer,
  'budget_version',profile.version,'capability_version',i.capability_version,
  'fractionable',CASE WHEN i.fractional_confirmed THEN 'CONFIRMED' ELSE 'UNKNOWN' END,
  'fractional_limit_day',CASE WHEN i.fractional_confirmed AND i.limit_order_confirmed
   AND i.regular_session_confirmed THEN 'CONFIRMED' ELSE 'UNKNOWN' END,
  'bid',trim_scale(bid)::text,'ask',trim_scale(ask)::text,
  'risk_budget',trim_scale(profile.budget)::text,
  'remaining_cash',trim_scale(floor(greatest(0,snap.cash-reserved*1.01)*1000000000000000000)
   *0.000000000000000001)::text,
  'equity',trim_scale(snap.equity)::text,'held_exposure',trim_scale(held)::text,

 'reserved_exposure',trim_scale(reserved)::text,'instrument_exposure',trim_scale(instrument_held)::text,
  'instrument_reserved_exposure',trim_scale(instrument_reserved)::text,
  'held_quantity',trim_scale(p.quantity)::text,'open_exit_quantity',trim_scale(exits)::text,
  'session_open',i.session_open);
 RETURN result||jsonb_build_object(
  'quote_provider_time',q.quote->>'providerTime','quote_observed_at',q.quote->>'observedAt',
  'capability_provider_time',to_char(least(b.market_provider_time,b.trade_provider_time)
   AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'capability_observed_at',to_char(greatest(b.market_observed_at,b.trade_observed_at)
   AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'capability_valid_until',to_char(least(i.valid_until,b.valid_until)
   AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'approval_valid_until',to_char(a.valid_until AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'session_start',to_char(b.session_start AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'session_end',to_char(b.session_end AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'capability',jsonb_build_object('broker_id','alpaca-paper','instrument_id',i.instrument_id,
   'version',i.capability_version,'paper','CONFIRMED','regular_session','CONFIRMED',
   'continuous_session',CASE WHEN i.continuous_session_confirmed THEN 'CONFIRMED' ELSE
 'UNKNOWN' END,
   'fractional',CASE WHEN i.fractional_confirmed THEN 'CONFIRMED' ELSE 'UNKNOWN' END,
   'market_order',CASE WHEN i.market_order_confirmed THEN 'CONFIRMED' ELSE 'UNKNOWN' END,
   'limit_order','CONFIRMED','quantity_step',trim_scale(i.quantity_step)::text,
   'price_tick',trim_scale(i.price_tick)::text));
END;
"""


def downgrade():
    raise RuntimeError("Paper LIMIT observations and policy are forward-only")
