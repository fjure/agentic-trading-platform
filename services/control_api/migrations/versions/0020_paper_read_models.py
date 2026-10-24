"""Bounded, stored-only public Paper projections; no financial mutations."""

from alembic import op

revision = "0020_paper_read_models"
down_revision = "0019_paper_cancellation"
branch_labels = None
depends_on = None

IDENTITY = """current_database()='lab_paper_runtime'
 AND session_user='atl_paper_control_api_login'
 AND current_setting('role')='atl_paper_control_api'"""


def function(name, args, result, body):
    op.execute(
        f"CREATE FUNCTION public.{name}({args}) RETURNS {result} "
        "LANGUAGE plpgsql STABLE SECURITY DEFINER "
        "SET search_path=pg_catalog,public,pg_temp AS $$ " + body + " $$"
    )
    signature = ",".join(arg.split()[-1] for arg in args.split(","))
    op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC")


def upgrade():
    function(
        "paper_read_time",
        "p_time timestamptz",
        "text",
        """
    BEGIN RETURN to_char(p_time AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'); END;
    """,
    )
    function("paper_read_order", "p_order text", "jsonb", _ORDER)
    function(
        "paper_read_page",
        "p_resource text,p_limit integer,p_cursor text,p_trace text",
        "jsonb",
        _PAGE,
    )
    for resource in ("orders", "fills", "operations"):
        function(
            "paper_" + resource,
            "p_limit integer,p_cursor text,p_trace text",
            "jsonb",
            "BEGIN RETURN public.paper_read_page('"
            + resource
            + "',p_limit,p_cursor,p_trace); END;",
        )
    function("paper_snapshot", "p_trace text", "jsonb", _SNAPSHOT)
    function("paper_trace", "p_trace text", "jsonb", _TRACE)
    op.execute("""
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_control_api') THEN
        GRANT EXECUTE ON FUNCTION public.paper_snapshot(text),public.paper_trace(text),
          public.paper_orders(integer,text,text),public.paper_fills(integer,text,text),
          public.paper_operations(integer,text,text) TO atl_paper_control_api;
      END IF;
    END $$;
    """)


_ORDER = """
DECLARE result jsonb;
BEGIN
 SELECT jsonb_build_object('orderId',bo.id,'proposalId',p.id,'clientOrderId',oi.client_order_id,
  'proposalSource',CASE WHEN p.decision_id IS NULL THEN 'HUMAN_REQUEST' ELSE
   'MODEL_DECISION' END,'decisionId',p.decision_id,'humanRequestId',p.human_request_id,
  'traceId',oi.trace_id,'symbol',oi.instrument_id,'side',oi.side,'orderType',oi.order_type,
  'timeInForce',CASE WHEN CASE WHEN p.decision_id IS NULL THEN hr.terms->>'time_in_force'
   ELSE lp.inputs#>>'{policy,time_in_force}' END='DAY' THEN 'DAY' END,
  'session',CASE WHEN CASE WHEN p.decision_id IS NULL THEN hr.terms->>'session'
   ELSE lp.inputs#>>'{policy,session}' END='REGULAR' THEN 'REGULAR' END,
  'quantity',trim_scale(oi.quantity)::text,'limitPrice',trim_scale(oi.limit_price)::text,
  'status',bo.status,'filledQuantity',trim_scale(bo.filled_quantity)::text,
  'averageFillPrice',(SELECT trim_scale(er.average_fill_price)::text
   FROM public.execution_reports er WHERE er.order_id=bo.id AND er.adapter='alpaca-paper'
    AND er.average_fill_price IS NOT NULL ORDER BY er.received_at DESC,er.id DESC LIMIT 1),
  'updatedAt',public.paper_read_time(bo.updated_at),
  'cancel',CASE WHEN q.id IS NULL THEN NULL ELSE jsonb_build_object('state',q.state,
   'requestedAt',public.paper_read_time(q.created_at),'nextAttemptAt',CASE
    WHEN q.state='FINISHED' THEN NULL ELSE public.paper_read_time(q.not_before) END,
   'deleteAttempts',q.delete_attempts) END) INTO result
 FROM public.broker_orders bo JOIN public.order_intents oi ON oi.id=bo.intent_id
 JOIN public.trade_proposals p ON p.id=oi.proposal_id
 LEFT JOIN public.paper_limit_plans lp ON lp.proposal_id=p.id
 LEFT JOIN public.paper_human_requests hr ON hr.id=p.human_request_id
 LEFT JOIN public.paper_cancels q ON q.order_id=bo.id
 WHERE bo.id=p_order AND bo.adapter='alpaca-paper' AND oi.account_id='paper';
 RETURN result;
END;
"""

_PAGE = """
DECLARE lim integer:=coalesce(p_limit,25); cursor_body jsonb; boundary_time timestamptz;
 boundary_id text; boundary_priority integer; generation bigint; items jsonb:='[]'; row record;
 last_time timestamptz; last_id text; last_priority integer; next_cursor text;
 count_rows integer:=0;
BEGIN
 IF NOT (IDENTITY) THEN RETURN NULL; END IF;
 IF p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$' OR lim<1 OR lim>50
  OR p_resource NOT IN ('orders','fills','operations') THEN
  RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
 SELECT account_generation INTO generation FROM public.paper_workflow_state WHERE singleton;
 IF p_cursor IS NOT NULL THEN
  IF length(p_cursor)>1024 OR p_cursor !~ '^[A-Za-z0-9_-]+$' THEN
   RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
  BEGIN
   cursor_body:=convert_from(decode(translate(p_cursor,'-_','+/')||
    repeat('=',(4-length(p_cursor)%4)%4),'base64'),'UTF8')::jsonb;
   IF jsonb_typeof(cursor_body) IS DISTINCT FROM 'object'
    OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(cursor_body) k)
     IS DISTINCT FROM ARRAY['accountGeneration','createdAt','localId','namespace','priority',
      'resource','version']::text[]
    OR cursor_body->'version' IS DISTINCT FROM '2'::jsonb
    OR cursor_body->'priority' NOT IN ('0'::jsonb,'1'::jsonb)
    OR jsonb_typeof(cursor_body->'priority') IS DISTINCT FROM 'number'
    OR jsonb_typeof(cursor_body->'accountGeneration') IS DISTINCT FROM 'number'
    OR (cursor_body->>'accountGeneration') !~ '^[0-9]{1,16}$'
    OR jsonb_typeof(cursor_body->'createdAt') IS DISTINCT FROM 'string'
    OR (cursor_body->>'createdAt') !~
     '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[.][0-9]{6}Z$'
    OR jsonb_typeof(cursor_body->'localId') IS DISTINCT FROM 'string'
    OR length(cursor_body->>'localId') NOT BETWEEN 1 AND 255 THEN
    RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
   IF cursor_body->>'namespace' IS DISTINCT FROM 'alpaca-paper'
    OR cursor_body->>'resource' IS DISTINCT FROM p_resource
    OR (cursor_body->>'accountGeneration')::bigint IS DISTINCT FROM generation THEN
    RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
   boundary_time:=(cursor_body->>'createdAt')::timestamptz;
   boundary_id:=cursor_body->>'localId';
   boundary_priority:=(cursor_body->>'priority')::integer;
   IF p_resource<>'operations' AND boundary_priority<>0 THEN
    RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
  EXCEPTION WHEN data_exception THEN RETURN jsonb_build_object('error','INVALID_REQUEST');
  END;
 END IF;
 FOR row IN
  WITH candidates AS (
   SELECT 0 priority,oi.created_at stamp,bo.id,public.paper_read_order(bo.id) body
    FROM public.broker_orders bo JOIN public.order_intents oi ON oi.id=bo.intent_id
    WHERE p_resource='orders' AND bo.adapter='alpaca-paper' AND oi.account_id='paper'
   UNION ALL
   SELECT 0,e.occurred_at,e.id,jsonb_build_object('fillId',e.id,'orderId',bo.id,
    'symbol',oi.instrument_id,'side',oi.side,'quantity',e.evidence->>'quantity',
    'price',e.evidence->>'price','occurredAt',public.paper_read_time(e.occurred_at),
    'fee',NULL,'currency','USD','traceId',e.trace_id)
    FROM public.paper_activity_evidence e JOIN public.broker_orders bo ON bo.id=e.order_id
    JOIN public.order_intents oi ON oi.id=bo.intent_id
    WHERE p_resource='fills' AND e.account_id='paper' AND e.activity_kind='FILL'
     AND bo.adapter='alpaca-paper' AND oi.account_id='paper'
   UNION ALL
   SELECT CASE WHEN c.status IN ('ACCEPTED','RUNNING') THEN 1 ELSE 0 END,
    c.accepted_at,c.id::text,public.paper_operation(c.id)
    FROM public.paper_commands c WHERE p_resource='operations'
  ) SELECT * FROM candidates WHERE boundary_time IS NULL OR
   (priority,stamp,id)<(boundary_priority,boundary_time,boundary_id)
    ORDER BY priority DESC,stamp DESC,id DESC LIMIT lim+1
 LOOP
  count_rows:=count_rows+1;
  IF count_rows>lim THEN
   next_cursor:=rtrim(translate(replace(encode(convert_to(jsonb_build_object('version',2,
    'namespace','alpaca-paper','resource',p_resource,'accountGeneration',generation,
    'createdAt',public.paper_read_time(last_time),'localId',last_id,
    'priority',last_priority)::text,'UTF8'),
     'base64'),chr(10),''),'+/','-_'),'=');
   EXIT;
  END IF;
  items:=items||jsonb_build_array(row.body); last_time:=row.stamp; last_id:=row.id;
  last_priority:=row.priority;
 END LOOP;
 RETURN jsonb_build_object('namespace','alpaca-paper','items',items,'nextCursor',next_cursor);
END;
""".replace("IDENTITY", IDENTITY)

_SNAPSHOT = """
DECLARE snap public.portfolio_snapshots%ROWTYPE; finance jsonb; positions jsonb;
 markets jsonb; item record; i public.risk_instrument_context%ROWTYPE;
 b public.broker_binding_proofs%ROWTYPE; q public.paper_quote_observations%ROWTYPE;
 state_id text; market_json jsonb;
BEGIN
 IF NOT (IDENTITY) THEN RETURN NULL; END IF;
 IF p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$' THEN
  RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
 SELECT ps.* INTO snap FROM public.risk_account_context a
 JOIN public.portfolio_snapshots ps ON ps.id=a.portfolio_snapshot_id WHERE a.account_id='paper';
 IF snap.id IS NOT NULL THEN
  finance:=jsonb_build_object('currency','USD','equity',trim_scale(snap.equity)::text,
   'cash',trim_scale(snap.cash)::text,'exposure',trim_scale(snap.exposure)::text,
   'ledgerVersion',snap.ledger_version,'asOf',public.paper_read_time(snap.as_of),
   'quality',snap.quality,'reconciliation',snap.reconciliation);
  SELECT coalesce(jsonb_agg(jsonb_build_object('symbol',p.instrument_id,
   'quantity',trim_scale(p.quantity)::text,'averagePrice',trim_scale(p.average_price)::text,
   'costBasis',trim_scale(cb.cost_basis)::text,'realizedPnl',trim_scale(p.realized_pnl)::text,
   'ledgerVersion',p.ledger_version) ORDER BY p.instrument_id),'[]') INTO positions
   FROM public.positions p LEFT JOIN public.paper_position_cost_bases cb
    ON cb.position_id=p.id AND cb.ledger_version=p.ledger_version
   WHERE p.account_id='paper' AND p.quantity>0;
 END IF;
 markets:='[]';
 FOR item IN SELECT unnest(ARRAY['SPY','QQQ']) symbol LOOP
  SELECT * INTO i FROM public.risk_instrument_context WHERE account_id='paper'
   AND instrument_id=item.symbol;
  SELECT * INTO b FROM public.broker_binding_proofs WHERE account_id='paper'
   AND instrument_id=item.symbol AND context_version=i.version;
  SELECT ms.id INTO state_id FROM public.market_states ms JOIN public.positions p
   ON p.id=ms.position_id WHERE p.account_id='paper' AND ms.instrument_id=item.symbol
   ORDER BY ms.as_of DESC,ms.id DESC LIMIT 1;
  SELECT * INTO q FROM public.paper_quote_observations WHERE market_state_id=state_id
   AND account_id='paper' AND context_version=i.version;
  market_json:=jsonb_build_object('symbol',item.symbol,'marketStateId',state_id,
   'quote',CASE WHEN q.id IS NULL OR b.context_version IS NULL THEN NULL ELSE
    jsonb_build_object('quoteId',q.id,'sourceId',q.quote->>'sourceId','bid',q.quote->>'bid',
     'ask',q.quote->>'ask','observedAt',q.quote->>'observedAt',
     'providerTime',q.quote->>'providerTime','sourceCapabilityVersion',
     (q.quote->>'sourceCapabilityVersion')::integer,'validUntil',public.paper_read_time(
      least(b.valid_until,(q.quote->>'observedAt')::timestamptz+interval '30 seconds'))) END,
   'capability',CASE WHEN i.instrument_id IS NULL OR b.context_version IS NULL THEN NULL ELSE
    jsonb_build_object('version',i.capability_version,'observedAt',public.paper_read_time(
     least(b.market_observed_at,b.trade_observed_at)),
     'validUntil',public.paper_read_time(least(i.valid_until,b.valid_until)),
     'limitOrderConfirmed',i.limit_order_confirmed,'fractionalConfirmed',i.fractional_confirmed,
     'quantityStep',trim_scale(i.quantity_step)::text,'priceTick',trim_scale(i.price_tick)::text)
    END,'session',CASE WHEN b.context_version IS NULL THEN NULL ELSE
    jsonb_build_object('version',i.version,'open',i.session_open,
     'observedAt',public.paper_read_time(b.market_observed_at),
     'validUntil',public.paper_read_time(least(i.session_valid_until,b.valid_until)),
     'startsAt',public.paper_read_time(b.session_start),
     'endsAt',public.paper_read_time(b.session_end)) END);
  markets:=markets||jsonb_build_array(market_json);
 END LOOP;
 RETURN jsonb_build_object('namespace','alpaca-paper','observedAt',
  public.paper_read_time(statement_timestamp()),'status',public.paper_status(p_trace),
  'verificationOperationId',(SELECT c.id FROM public.paper_commands c
   CROSS JOIN public.paper_workflow_state w WHERE w.singleton AND w.verified_at IS NOT NULL
    AND w.pinned_account_id IS NOT NULL AND c.operation_type='VERIFY_CONNECTION'
    AND c.status='SUCCEEDED' AND c.credential_generation=coalesce(w.pending_generation,
     w.active_generation,w.saved_generation) AND (c.payload->>'credentialGeneration')::bigint
      =c.credential_generation ORDER BY c.updated_at DESC,c.id DESC LIMIT 1),
  'profile',public.paper_trading_profile_get(p_trace),'portfolio',finance,
  'positions',positions,'markets',markets);
END;
""".replace("IDENTITY", IDENTITY)

_TRACE = """
DECLARE items jsonb:='[]'; row record; count_rows integer:=0; complete boolean:=true;
BEGIN
 IF NOT (IDENTITY) THEN RETURN NULL; END IF;
 IF p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$' THEN
  RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
 FOR row IN WITH events AS (
  SELECT c.id::text||'/accepted' event_id,c.accepted_at at,'CONTROL_API' role,
   'OPERATION_ACCEPTED' action,c.id::text entity_id,1::bigint version,NULL::text reason
   FROM public.paper_commands c WHERE c.trace_id=p_trace
  UNION ALL
  SELECT c.id::text||'/updated/'||c.status,c.updated_at,'EXECUTION','OPERATION_UPDATED',
   c.id::text,1,c.error_code FROM public.paper_commands c
   WHERE c.trace_id=p_trace AND c.status<>'ACCEPTED'
  UNION ALL
  SELECT p.id,p.created_at,CASE WHEN p.decision_id IS NULL THEN 'EXECUTION' ELSE 'WORKER' END,
   'PROPOSAL_CREATED',p.id,1,NULL FROM public.trade_proposals p JOIN public.market_states ms
   ON ms.id=p.market_state_id JOIN public.positions pos ON pos.id=ms.position_id
   WHERE p.trace_id=p_trace AND pos.account_id='paper'
  UNION ALL
  SELECT rd.id,rd.decided_at,'EXECUTION',CASE WHEN rd.approved THEN 'RISK_APPROVED'
   ELSE 'RISK_REJECTED' END,rd.id,1,NULL FROM public.risk_decisions rd
   JOIN public.trade_proposals p ON p.id=rd.proposal_id JOIN public.market_states ms
    ON ms.id=p.market_state_id JOIN public.positions pos ON pos.id=ms.position_id
   WHERE rd.trace_id=p_trace AND pos.account_id='paper'
  UNION ALL
  SELECT bo.id||':'||bo.version,bo.updated_at,'EXECUTION','ORDER_UPDATED',bo.id,bo.version,NULL
   FROM public.broker_orders bo JOIN public.order_intents oi ON oi.id=bo.intent_id
   WHERE bo.trace_id=p_trace AND bo.adapter='alpaca-paper' AND oi.account_id='paper'
  UNION ALL
  SELECT er.id,er.received_at,'EXECUTION','EXECUTION_REPORTED',er.id,1,er.reason_code
   FROM public.execution_reports er JOIN public.broker_orders bo ON bo.id=er.order_id
   JOIN public.order_intents oi ON oi.id=bo.intent_id
   WHERE er.trace_id=p_trace AND er.adapter='alpaca-paper' AND oi.account_id='paper'
  UNION ALL
  SELECT r.id,r.observed_at,'EXECUTION','RECONCILIATION_OBSERVED',r.id,1,r.reason_code
   FROM public.paper_reconciliation_receipts r WHERE r.trace_id=p_trace AND r.account_id='paper'
  UNION ALL
  SELECT a.id::text,a.started_at,'EXECUTION',CASE a.kind WHEN 'LOOKUP' THEN 'CANCEL_LOOKUP'
   ELSE 'CANCEL_DELETE' END,a.id::text,1,NULL FROM public.paper_cancel_attempts a
   JOIN public.paper_cancels q ON q.id=a.cancel_id WHERE a.trace_id=p_trace
  UNION ALL
  SELECT r.id||'/result',(r.outcome->>'observedAt')::timestamptz,'EXECUTION','CANCEL_OBSERVED',
   r.id,1,CASE r.outcome->>'kind' WHEN 'UNKNOWN' THEN 'BROKER_STATUS_UNKNOWN'
    WHEN 'THROTTLED' THEN 'PROVIDER_RATE_LIMITED' END
   FROM public.paper_cancel_results r WHERE r.trace_id=p_trace
 ) SELECT * FROM events ORDER BY at,event_id LIMIT 33 LOOP
  count_rows:=count_rows+1;
  IF count_rows>32 THEN complete:=false; EXIT; END IF;
  items:=items||jsonb_build_array(jsonb_build_object('eventId',
   encode(sha256(convert_to(row.action||':'||row.event_id,'UTF8')),'hex'),
   'occurredAt',public.paper_read_time(row.at),'role',row.role,'action',row.action,
   'entityId',row.entity_id,'entityVersion',row.version,'reasonCodes',CASE
    WHEN row.reason IN ('APPROVAL_EXPIRED','BROKER_STATUS_UNKNOWN','PROVIDER_RATE_LIMITED',
     'RECONCILIATION_REQUIRED','BUDGET_EXCEEDED','KILL_SWITCH_ACTIVE','ENTRY_PAUSED',
     'VERSION_CONFLICT','POLICY_MISMATCH','CAPABILITY_UNCONFIRMED','PROVIDER_REJECTED',
     'INSUFFICIENT_POSITION','STALE_QUOTE','INVALID_REQUEST','ORDER_NOT_FOUND')
     THEN jsonb_build_array(row.reason) ELSE '[]'::jsonb END));
 END LOOP;
 RETURN jsonb_build_object('namespace','alpaca-paper','traceId',p_trace,
  'complete',complete,'items',items);
END;
""".replace("IDENTITY", IDENTITY)


def downgrade():
    raise RuntimeError("Paper read projection migrations are forward-only")
