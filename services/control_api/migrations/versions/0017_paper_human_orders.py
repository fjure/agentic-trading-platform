"""Immutable human provenance on the shared, unchanged model proposal path."""

from alembic import op

revision = "0017_paper_human_orders"
down_revision = "0016_paper_lifecycle"
branch_labels = None
depends_on = None


def function(name, args, result, body):
    op.execute(f"CREATE OR REPLACE FUNCTION public.{name}({args}) RETURNS {result} "
               "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp "
               "AS $$ " + body + " $$")
    signature = ",".join(value.split()[-1] for value in args.split(",")) if args else ""
    op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC")


def upgrade():
    op.execute("""
    CREATE TABLE public.paper_human_requests (
      id text PRIMARY KEY,operation_id uuid UNIQUE NOT NULL REFERENCES public.paper_commands(id),
      terms jsonb NOT NULL CHECK(jsonb_typeof(terms)='object'),actor_id text NOT NULL,
      trace_id varchar(32) NOT NULL
    );
    REVOKE ALL ON public.paper_human_requests FROM PUBLIC;
    CREATE TRIGGER paper_human_requests_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
      ON public.paper_human_requests FOR EACH STATEMENT
      EXECUTE FUNCTION public.ledger_reject_mutation();
    CREATE CONSTRAINT TRIGGER paper_human_request_audit AFTER INSERT
      ON public.paper_human_requests DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION public.paper_reconciliation_require_audit();
    ALTER TABLE public.trade_proposals ALTER COLUMN decision_id DROP NOT NULL;
    ALTER TABLE public.trade_proposals ADD COLUMN human_request_id text UNIQUE
      REFERENCES public.paper_human_requests(id);
    ALTER TABLE public.trade_proposals ADD CONSTRAINT paper_proposal_source_xor
      CHECK((decision_id IS NULL)<>(human_request_id IS NULL));
    """)
    function("paper_human_source_guard", "", "trigger", _SOURCE_GUARD)
    function("paper_human_source_caller", "", "trigger", """
    BEGIN
      IF NEW.human_request_id IS NOT NULL AND NOT (
       current_database()='lab_paper_runtime' AND session_user='atl_paper_execution_login'
       AND current_setting('role')='atl_paper_execution') THEN
       RAISE EXCEPTION 'human proposal requires Paper Execution' USING ERRCODE='42501';
      END IF;
      RETURN NEW;
    END;
    """)
    op.execute("CREATE TRIGGER paper_human_source_caller BEFORE INSERT "
               "ON public.trade_proposals FOR EACH ROW "
               "EXECUTE FUNCTION public.paper_human_source_caller()")
    op.execute("CREATE CONSTRAINT TRIGGER paper_human_source_guard AFTER INSERT "
               "ON public.trade_proposals DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
               "EXECUTE FUNCTION public.paper_human_source_guard()")
    function("paper_human_request_read", "p_request text", "jsonb", _REQUEST_READ)
    function("paper_human_proposal_read", "p_proposal text", "jsonb", _PROPOSAL_READ)
    function("paper_human_proposal_create",
             "p_operation uuid,p_owner uuid,p_fence bigint,p_state text,p_hash text,"
             "p_until timestamptz", "jsonb", _CREATE)
    function("paper_human_operation_read", "p_operation uuid", "jsonb", _METADATA)
    function("paper_human_finish", "p_operation uuid,p_owner uuid,p_fence bigint",
             "boolean", _FINISH)
    for name, args in (
        ("paper_operation", "uuid"), ("paper_claim", "uuid,integer"),
        ("paper_finish", "uuid,uuid,bigint,jsonb,uuid,text"),
    ):
        op.execute(f"ALTER FUNCTION public.{name}({args}) RENAME TO {name}_v16")
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}_v16({args}) FROM PUBLIC")
        op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE "
                   "rolname='atl_paper_execution') THEN "
                   f"REVOKE ALL ON FUNCTION public.{name}_v16({args}) "
                   "FROM atl_paper_execution; END IF; "
                   "IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_control_api') THEN "
                   f"REVOKE ALL ON FUNCTION public.{name}_v16({args}) "
                   "FROM atl_paper_control_api; END IF; END $$")
    function("paper_operation", "p_id uuid", "jsonb", _OPERATION)
    function("paper_claim", "p_owner uuid,p_seconds integer", "jsonb", _CLAIM)
    function("paper_finish", "p_id uuid,p_owner uuid,p_fence bigint,p_public jsonb,"
             "p_private uuid,p_error text", "boolean", """
    BEGIN
      IF EXISTS(SELECT 1 FROM public.paper_human_requests WHERE operation_id=p_id)
        THEN RETURN false; END IF;
      RETURN public.paper_finish_v16(p_id,p_owner,p_fence,p_public,p_private,p_error);
    END;
    """)
    op.execute("""
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_execution') THEN
        GRANT EXECUTE ON FUNCTION public.paper_human_request_read(text),
          public.paper_human_proposal_read(text),
          public.paper_human_proposal_create(uuid,uuid,bigint,text,text,timestamptz),
          public.paper_human_operation_read(uuid),public.paper_human_finish(uuid,uuid,bigint),
          public.paper_claim(uuid,integer),public.paper_finish(uuid,uuid,bigint,jsonb,uuid,text)
          TO atl_paper_execution;
      END IF;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_control_api') THEN
        GRANT EXECUTE ON FUNCTION public.paper_operation(uuid) TO atl_paper_control_api;
      END IF;
    END $$;
    """)


_IDENTITY = """
 current_database()='lab_paper_runtime' AND session_user='atl_paper_execution_login'
 AND current_setting('role')='atl_paper_execution'
"""

_CLAIM = """
DECLARE s public.paper_workflow_state%ROWTYPE; c public.paper_commands%ROWTYPE;
 recovery boolean;
BEGIN
 IF NOT (""" + _IDENTITY + """) THEN RETURN NULL; END IF;
 IF p_owner IS NULL OR p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 120 THEN
  RAISE EXCEPTION 'invalid lease'; END IF;
 SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
 IF EXISTS(SELECT 1 FROM public.paper_commands WHERE status='RUNNING'
  AND lease_until>clock_timestamp()) THEN RETURN NULL; END IF;
 SELECT * INTO c FROM public.paper_commands WHERE status IN ('ACCEPTED','RUNNING')
  ORDER BY accepted_at,id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 recovery:=c.operation_type='PLACE_LIMIT_ORDER' AND EXISTS(
  SELECT 1 FROM public.paper_human_requests WHERE operation_id=c.id);
 IF c.operation_type NOT IN ('PAUSE','STOP','KILL') AND NOT recovery AND
  (c.accepted_generation<>s.epoch OR c.credential_generation<>s.saved_generation
   OR c.credential_generation<>coalesce(s.pending_generation,s.active_generation)) THEN
  UPDATE public.paper_commands SET status='BLOCKED',error_code='VERSION_CONFLICT',
   updated_at=clock_timestamp() WHERE id=c.id;
  INSERT INTO public.paper_command_events(operation_id,event_type,epoch,fence)
   VALUES(c.id,'BLOCKED',s.epoch,c.fence);
  RETURN NULL; END IF;
 IF c.fence>=9007199254740991 THEN RETURN NULL; END IF;
 UPDATE public.paper_commands SET status='RUNNING',owner=p_owner,fence=fence+1,
  lease_until=clock_timestamp()+make_interval(secs=>p_seconds),updated_at=clock_timestamp()
  WHERE id=c.id RETURNING * INTO c;
 INSERT INTO public.paper_command_events(operation_id,event_type,epoch,fence)
  VALUES(c.id,'ATTEMPT_STARTED',s.epoch,c.fence);
 RETURN jsonb_build_object('operationId',c.id,'operationType',c.operation_type,'payload',c.payload,
  'owner',c.owner,'fence',c.fence,'epoch',c.accepted_generation,
  'credentialGeneration',c.credential_generation,'activeGeneration',s.active_generation,
  'pendingGeneration',s.pending_generation,'pinnedAccountId',s.pinned_account_id,
  'traceId',c.trace_id,'actorId',c.actor_id,'acceptedAt',
  to_char(c.accepted_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
END;
"""

_REQUEST_READ = """
BEGIN
 IF NOT (""" + _IDENTITY + """) THEN RETURN NULL; END IF;
 RETURN (SELECT terms FROM public.paper_human_requests WHERE id=p_request);
END;
"""

_PROPOSAL_READ = """
DECLARE p public.trade_proposals%ROWTYPE; r public.paper_human_requests%ROWTYPE;
BEGIN
 IF NOT (""" + _IDENTITY + """) THEN RETURN NULL; END IF;
 SELECT * INTO p FROM public.trade_proposals WHERE id=p_proposal AND decision_id IS NULL;
 SELECT * INTO r FROM public.paper_human_requests WHERE id=p.human_request_id;
 IF p.id IS NULL OR r.id IS NULL THEN RETURN NULL; END IF;
 RETURN (r.terms-ARRAY['id','created_at','valid_until'])||jsonb_build_object(
  'id',p.id,'market_state_id',p.market_state_id,'proposal_source','HUMAN_REQUEST',
  'decision_id',NULL,'human_request_id',r.id,
  'created_at',to_char(p.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'valid_until',to_char(p.valid_until AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
END;
"""

_SOURCE_GUARD = """
DECLARE r public.paper_human_requests%ROWTYPE;
BEGIN
 IF NEW.human_request_id IS NULL THEN RETURN NULL; END IF;
 IF NOT (""" + _IDENTITY + """) THEN
  RAISE EXCEPTION 'human proposal requires Paper Execution'; END IF;
 SELECT * INTO r FROM public.paper_human_requests WHERE id=NEW.human_request_id;
 IF NEW.decision_id IS NOT NULL OR r.id IS NULL OR NEW.trace_id<>r.trace_id
  OR NEW.instrument_id<>r.terms->>'symbol'
  OR NEW.target<>(CASE r.terms->>'side' WHEN 'BUY' THEN 'LONG' ELSE 'FLAT' END)
  OR NEW.quantity<>(r.terms->>'quantity')::numeric
  OR NEW.limit_price IS DISTINCT FROM (r.terms->>'limit_price')::numeric
  OR NEW.created_at<(r.terms->>'created_at')::timestamptz
  OR NEW.valid_until>(r.terms->>'valid_until')::timestamptz
  OR NOT EXISTS(SELECT 1 FROM public.paper_limit_plans WHERE proposal_id=NEW.id)
  OR NOT EXISTS(SELECT 1 FROM public.paper_proposal_activations WHERE proposal_id=NEW.id)
  THEN RAISE EXCEPTION 'human proposal provenance mismatch'; END IF;
 RETURN NULL;
END;
"""

_CREATE = """
DECLARE c public.paper_commands%ROWTYPE; s public.paper_workflow_state%ROWTYPE;
 proof jsonb; activation jsonb; terms jsonb; n timestamptz;
 proposal_id text; plan_id text; link_id text; key text;
BEGIN
 IF NOT (""" + _IDENTITY + """) THEN RETURN NULL; END IF;
 SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
 PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
 SELECT * INTO c FROM public.paper_commands WHERE id=p_operation FOR UPDATE;
 n:=clock_timestamp();
 IF c.id IS NULL OR c.operation_type<>'PLACE_LIMIT_ORDER' OR p_owner IS NULL
  OR p_fence IS NULL OR p_fence<1 OR c.status<>'RUNNING'
  OR c.owner IS DISTINCT FROM p_owner OR c.fence IS DISTINCT FROM p_fence
  OR c.lease_until IS NULL OR c.lease_until<=n THEN
  RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
 proposal_id:=md5('paper-human-proposal:'||c.id::text)::uuid::text;
 IF EXISTS(SELECT 1 FROM public.paper_human_requests WHERE operation_id=c.id) THEN
  RETURN public.paper_human_proposal_read(proposal_id); END IF;
 IF c.accepted_generation<>s.epoch OR c.credential_generation<>s.active_generation THEN
  RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
 IF s.operating_state='HALTED' THEN
  RETURN jsonb_build_object('error','KILL_SWITCH_ACTIVE'); END IF;
 IF p_until IS NULL OR p_until<=n OR (c.payload->>'expiresAt')::timestamptz<=n THEN
  RETURN jsonb_build_object('error','APPROVAL_EXPIRED'); END IF;
 IF p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
  OR (c.payload->>'expiresAt')::timestamptz>c.accepted_at+interval '30 seconds'
  OR p_until>(c.payload->>'expiresAt')::timestamptz THEN
  RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
 activation:=public.paper_activation_guard('paper',NULL);
 proof:=public.paper_planner_inputs(p_state);
 IF activation IS NULL THEN RETURN jsonb_build_object('error','ENTRY_PAUSED'); END IF;
 IF proof IS NULL OR NOT EXISTS(SELECT 1 FROM public.market_states WHERE id=p_state
  AND instrument_id=c.payload->>'symbol') THEN
  RETURN jsonb_build_object('error','STALE_QUOTE'); END IF;
 IF NOT (activation->'symbols') ? (c.payload->>'symbol') THEN
  RETURN jsonb_build_object('error','POLICY_MISMATCH'); END IF;
 FOREACH key IN ARRAY ARRAY['credential_generation','account_generation','policy_version',
  'profile_version','budget_version','limit_policy_version'] LOOP
  IF (proof->>key)::numeric IS DISTINCT FROM (c.payload->>CASE key
   WHEN 'credential_generation' THEN 'credentialGeneration'
   WHEN 'account_generation' THEN 'accountGeneration' WHEN 'policy_version' THEN 'policyVersion'
   WHEN 'profile_version' THEN 'profileVersion' WHEN 'budget_version' THEN 'budgetVersion'
   ELSE 'limitPolicyVersion' END)::numeric THEN
   RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
 END LOOP;
 IF (proof->>'risk_budget')::numeric<>(c.payload->>'riskBudget')::numeric THEN
  RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
 IF c.payload->>'side'='BUY' AND
  (c.payload->>'quantity')::numeric*(c.payload->>'limitPrice')::numeric>
   (c.payload->>'riskBudget')::numeric THEN
  RETURN jsonb_build_object('error','BUDGET_EXCEEDED'); END IF;
 IF p_until>least(n+interval '30 seconds',(activation->>'expiresAt')::timestamptz,
  (proof->>'approval_valid_until')::timestamptz,(proof->>'session_end')::timestamptz,
  (proof->>'quote_provider_time')::timestamptz+interval '30 seconds',
  (proof->>'quote_observed_at')::timestamptz+interval '30 seconds',
  (proof->>'capability_provider_time')::timestamptz+interval '120 seconds',
  (proof->>'capability_observed_at')::timestamptz+interval '120 seconds',
  (proof->>'capability_valid_until')::timestamptz) THEN
  RETURN jsonb_build_object('error','APPROVAL_EXPIRED'); END IF;
 terms:=jsonb_build_object('id',c.id::text,'operation_id',c.id::text,'account_id','paper',
  'account_generation',(c.payload->>'accountGeneration')::bigint,
  'credential_generation',(c.payload->>'credentialGeneration')::bigint,
  'policy_version',(c.payload->>'policyVersion')::bigint,
  'profile_version',(c.payload->>'profileVersion')::bigint,
  'limit_policy_version',(c.payload->>'limitPolicyVersion')::bigint,
  'budget_version',(c.payload->>'budgetVersion')::bigint,
  'risk_budget',trim_scale((c.payload->>'riskBudget')::numeric)::text,
  'symbol',c.payload->>'symbol','side',c.payload->>'side',
  'quantity',trim_scale((c.payload->>'quantity')::numeric)::text,
  'limit_price',trim_scale((c.payload->>'limitPrice')::numeric)::text,
  'created_at',to_char(c.accepted_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'valid_until',c.payload->>'expiresAt','trace_id',c.trace_id,'currency','USD',
  'order_type','LIMIT','time_in_force','DAY','session','REGULAR');
 plan_id:='paper-human-plan-'||c.id::text;
 link_id:='paper-human-activation-'||c.id::text;
 FOREACH key IN ARRAY ARRAY[c.id::text,proposal_id,plan_id,link_id] LOOP
  INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
   entity_version,trace_id) VALUES(gen_random_uuid()::text,n,c.actor_id,'paper.human_proposal',
   '["PAPER_HUMAN_LIMIT"]',key,1,c.trace_id);
 END LOOP;
 INSERT INTO public.paper_human_requests VALUES(c.id::text,c.id,terms,c.actor_id,c.trace_id);
 INSERT INTO public.trade_proposals(id,decision_id,human_request_id,market_state_id,
  instrument_id,target,quantity,created_at,valid_until,trace_id,rationale_codes,limit_price)
  VALUES(proposal_id,NULL,c.id::text,p_state,c.payload->>'symbol',
   CASE c.payload->>'side' WHEN 'BUY' THEN 'LONG' ELSE 'FLAT' END,
   (c.payload->>'quantity')::numeric,n,p_until,c.trace_id,
   jsonb_build_array('PAPER_HUMAN_LIMIT','HUMAN_REQUEST:'||c.id::text),
   (c.payload->>'limitPrice')::numeric);
 INSERT INTO public.paper_limit_plans VALUES(proposal_id,plan_id,proof,p_hash,
  c.actor_id,c.trace_id,n);
 INSERT INTO public.paper_proposal_activations VALUES(link_id,proposal_id,
  (activation->>'activationId')::uuid,c.actor_id,c.trace_id);
 RETURN public.paper_human_proposal_read(proposal_id);
END;
"""

_METADATA = """
DECLARE result jsonb;
BEGIN
 IF NOT (""" + _IDENTITY + """) THEN RETURN NULL; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.paper_commands WHERE id=p_operation
  AND operation_type='PLACE_LIMIT_ORDER') THEN RETURN NULL; END IF;
 SELECT jsonb_build_object('proposalId',p.id,'orderId',bo.id,'orderStatus',bo.status,
  'riskDecision',CASE WHEN rd.id IS NULL THEN NULL WHEN rd.approved THEN 'APPROVED'
   ELSE 'REJECTED' END,'riskReasons',coalesce(rd.reason_codes,'[]'::jsonb)) INTO result
  FROM public.paper_human_requests r JOIN public.trade_proposals p ON p.human_request_id=r.id
  LEFT JOIN public.risk_decisions rd ON rd.proposal_id=p.id
  LEFT JOIN public.order_intents oi ON oi.proposal_id=p.id
  LEFT JOIN public.broker_orders bo ON bo.intent_id=oi.id WHERE r.operation_id=p_operation;
 RETURN coalesce(result,jsonb_build_object('proposalId',NULL,'orderId',NULL,'orderStatus',NULL,
  'riskDecision',NULL,'riskReasons','[]'::jsonb));
END;
"""

_FINISH = """
DECLARE c public.paper_commands%ROWTYPE; facts jsonb; failure text; n timestamptz;
 reservation public.risk_reservations%ROWTYPE;
BEGIN
 IF NOT (""" + _IDENTITY + """) THEN RETURN false; END IF;
 PERFORM singleton FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
 PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
 SELECT * INTO c FROM public.paper_commands WHERE id=p_operation FOR UPDATE;
 n:=clock_timestamp();
 IF c.id IS NOT NULL AND c.operation_type='PLACE_LIMIT_ORDER'
  AND c.status IN ('SUCCEEDED','BLOCKED','FAILED') THEN RETURN true; END IF;
 IF c.id IS NULL OR c.operation_type<>'PLACE_LIMIT_ORDER' OR c.status<>'RUNNING'
  OR p_owner IS NULL OR p_fence IS NULL OR p_fence<1 OR c.owner IS DISTINCT FROM p_owner
  OR c.fence IS DISTINCT FROM p_fence OR c.lease_until IS NULL OR c.lease_until<=n THEN
  RETURN false; END IF;
 facts:=public.paper_human_operation_read(p_operation);
 IF facts->>'orderStatus' IN ('SUBMITTING','UNKNOWN') THEN RETURN false; END IF;
 IF facts->>'riskDecision'='REJECTED' THEN
  failure:=CASE
   WHEN (SELECT operating_state FROM public.paper_workflow_state WHERE singleton)='HALTED'
    THEN 'KILL_SWITCH_ACTIVE'
   WHEN (facts->'riskReasons') ?| ARRAY['EXIT_QUANTITY','INSUFFICIENT_POSITION']
    THEN 'INSUFFICIENT_POSITION'
   WHEN (facts->'riskReasons') ?| ARRAY['CASH_LIMIT','EXPOSURE_LIMIT','POSITION_EXPOSURE_LIMIT',
    'BUDGET_EXCEEDED','RISK_BUDGET_LIMIT','POSITION_FRACTION','POSITION_NOTIONAL_LIMIT']
    THEN 'BUDGET_EXCEEDED'
   WHEN (facts->'riskReasons') ?| ARRAY['APPROVAL_EXPIRED','DECISION_EXPIRED','PROPOSAL_EXPIRED']
    THEN 'APPROVAL_EXPIRED'
   WHEN (facts->'riskReasons') ?| ARRAY['STALE_QUOTE','MARKET_STALE','RISK_CONTEXT_STALE']
    THEN 'STALE_QUOTE'
   WHEN (facts->'riskReasons') ? 'SESSION_CLOSED' THEN 'SESSION_CLOSED'
   WHEN (facts->'riskReasons') ?| ARRAY['CAPABILITY_UNCONFIRMED','BROKER_CAPABILITY']
    THEN 'CAPABILITY_UNCONFIRMED'
   WHEN (facts->'riskReasons') ?| ARRAY['QUANTITY_STEP','PRICE_TICK'] THEN 'INVALID_REQUEST'
   WHEN (facts->'riskReasons') ?| ARRAY['ENTRY_PAUSED','PAPER_ACTIVATION_INVALID']
    THEN 'ENTRY_PAUSED'
   ELSE 'POLICY_MISMATCH' END;
 ELSIF facts->>'orderStatus'='REJECTED' THEN failure:='PROVIDER_REJECTED';
 ELSIF facts->>'orderStatus'='EXPIRED' THEN failure:='APPROVAL_EXPIRED';
 ELSIF facts->>'orderStatus' IN ('ACKNOWLEDGED','PARTIALLY_FILLED','FILLED','CANCELED')
  THEN failure:=NULL;
 ELSIF facts->>'proposalId' IS NOT NULL AND facts->>'orderId' IS NULL
  AND EXISTS(SELECT 1 FROM public.trade_proposals WHERE id=facts->>'proposalId' AND valid_until<=n)
  AND NOT EXISTS(SELECT 1 FROM public.risk_reservations WHERE proposal_id=facts->>'proposalId'
   AND state='HELD_UNKNOWN') THEN
  FOR reservation IN SELECT * FROM public.risk_reservations
   WHERE proposal_id=facts->>'proposalId' AND state='ACTIVE' FOR UPDATE LOOP
   INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
    entity_version,trace_id) VALUES(gen_random_uuid()::text,n,'execution-human',
    'execution.expire_unsent','["APPROVAL_EXPIRED"]',reservation.id,
    reservation.version+1,c.trace_id);
   UPDATE public.risk_reservations SET state='RELEASED',version=version+1,released_at=n
    WHERE id=reservation.id;
  END LOOP;
  failure:='APPROVAL_EXPIRED';
 ELSE RETURN false; END IF;
 UPDATE public.paper_commands SET status=CASE WHEN failure IS NULL THEN 'SUCCEEDED'
  ELSE 'BLOCKED' END,error_code=failure,lease_until=NULL,updated_at=n WHERE id=p_operation;
 INSERT INTO public.paper_command_events(operation_id,event_type,epoch,fence)
  VALUES(p_operation,CASE WHEN failure IS NULL THEN 'SUCCEEDED' ELSE 'BLOCKED' END,
   c.accepted_generation,p_fence);
 RETURN true;
END;
"""

_OPERATION = """
DECLARE result jsonb; order_id text;
BEGIN
 result:=public.paper_operation_v16(p_id);
 SELECT bo.id INTO order_id FROM public.paper_human_requests r
  JOIN public.trade_proposals p ON p.human_request_id=r.id
  JOIN public.order_intents oi ON oi.proposal_id=p.id
  JOIN public.broker_orders bo ON bo.intent_id=oi.id WHERE r.operation_id=p_id;
 RETURN CASE WHEN order_id IS NULL THEN result
  ELSE result||jsonb_build_object('orderId',order_id) END;
END;
"""
