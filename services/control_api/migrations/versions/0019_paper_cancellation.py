"""Independent, fenced and audited Paper cancellation obligations."""

from alembic import op

revision = "0019_paper_cancellation"
down_revision = "0018_paper_human_finish_guard"
branch_labels = None
depends_on = None


def function(name, args, result, body):
    op.execute(
        f"CREATE OR REPLACE FUNCTION public.{name}({args}) RETURNS {result} "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp "
        "AS $$ " + body + " $$"
    )
    signature = ",".join(value.split()[-1] for value in args.split(",")) if args else ""
    op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC")


AUTH_ARGS = "p_cancel uuid,p_worker uuid,p_lease_fence bigint,p_exec_owner text,p_exec_fence bigint"
IDENTITY = """current_database()='lab_paper_runtime'
 AND session_user='atl_paper_execution_login' AND current_setting('role')='atl_paper_execution'"""


def upgrade():
    op.execute("""
    CREATE TABLE public.paper_cancels (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),order_id varchar(255) UNIQUE NOT NULL
        REFERENCES public.broker_orders(id),account_generation bigint NOT NULL
 CHECK(account_generation>0),
      state text NOT NULL DEFAULT 'READY' CHECK(state IN ('READY','BACKOFF','LOOKUP_ONLY',
 'FINISHED')),
      worker_id uuid,lease_fence bigint NOT NULL DEFAULT 0 CHECK(lease_fence BETWEEN 0 AND
 9007199254740991),
      lease_until timestamptz,exec_owner text,exec_fence bigint,
      delete_attempts integer NOT NULL DEFAULT 0 CHECK(delete_attempts BETWEEN 0 AND 3),
      lookup_only boolean NOT NULL DEFAULT false,not_before timestamptz NOT NULL DEFAULT
 clock_timestamp(),
      last_error text CHECK(last_error IN ('BROKER_STATUS_UNKNOWN','PROVIDER_RATE_LIMITED')),
      actor_id text NOT NULL,trace_id varchar(32) NOT NULL,version integer NOT NULL DEFAULT 1,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE public.paper_cancel_links (
      id text PRIMARY KEY,cancel_id uuid NOT NULL REFERENCES public.paper_cancels(id),
      operation_id uuid NOT NULL REFERENCES public.paper_commands(id),
      origin text NOT NULL CHECK(origin IN ('EXPLICIT_CANCEL','KILL_DRAIN')),
      actor_id text NOT NULL,trace_id varchar(32) NOT NULL,UNIQUE(cancel_id,operation_id)
    );
    CREATE TABLE public.paper_cancel_attempts (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),cancel_id uuid NOT NULL REFERENCES
 public.paper_cancels(id),
      kind text NOT NULL CHECK(kind IN ('LOOKUP','DELETE')),worker_id uuid NOT NULL,
      lease_fence bigint NOT NULL,exec_owner text NOT NULL,exec_fence bigint NOT NULL,
      credential_generation bigint NOT NULL,started_at timestamptz NOT NULL DEFAULT
 clock_timestamp(),
      actor_id text NOT NULL,trace_id varchar(32) NOT NULL
    );
    CREATE TABLE public.paper_cancel_results (
      id text PRIMARY KEY,attempt_id uuid UNIQUE NOT NULL REFERENCES
 public.paper_cancel_attempts(id),
      outcome jsonb NOT NULL,actor_id text NOT NULL,trace_id varchar(32) NOT NULL
    );
    REVOKE ALL ON public.paper_cancels,public.paper_cancel_links,public.paper_cancel_attempts,
      public.paper_cancel_results FROM PUBLIC;
    """)
    function(
        "paper_cancel_audit",
        "p_id text,p_version integer,p_actor text,p_trace text",
        "void",
        """
    DECLARE phase text;
    BEGIN
      SELECT kind INTO phase FROM public.paper_cancel_attempts WHERE id::text=p_id;
      INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
       entity_version,trace_id) VALUES(gen_random_uuid()::text,clock_timestamp(),p_actor,
       CASE WHEN EXISTS(SELECT 1 FROM public.paper_cancel_results WHERE id=p_id)
        THEN 'paper.cancel_result' ELSE coalesce('paper.cancel_'||lower(phase),'paper.cancel') END,
       '["PAPER_CANCEL"]',p_id,p_version,p_trace);
    END;
    """,
    )
    function(
        "paper_cancel_require_audit",
        "",
        "trigger",
        """
    DECLARE v integer:=1;
    BEGIN
      IF TG_TABLE_NAME='paper_cancels' THEN v:=NEW.version; END IF;
      IF NOT EXISTS(SELECT 1 FROM public.audit_events WHERE entity_id=NEW.id::text
       AND entity_version=v AND actor_id=NEW.actor_id AND trace_id=NEW.trace_id
       AND transaction_id=txid_current()) THEN RAISE EXCEPTION 'cancel audit missing'; END IF;
      RETURN NULL;
    END;
    """,
    )
    for table in (
        "paper_cancels",
        "paper_cancel_links",
        "paper_cancel_attempts",
        "paper_cancel_results",
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {table}_audit AFTER INSERT"
            + (" OR UPDATE" if table == "paper_cancels" else "")
            + f" ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION public.paper_cancel_require_audit()"
        )
        if table != "paper_cancels":
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
                f"ON public.{table} FOR EACH STATEMENT "
                "EXECUTE FUNCTION public.ledger_reject_mutation()"
            )
    function(
        "paper_cancel_identity",
        "",
        "trigger",
        """
    BEGIN
      IF (NEW.id,NEW.order_id,NEW.account_generation,NEW.actor_id,NEW.trace_id,NEW.created_at)
       IS DISTINCT FROM (OLD.id,OLD.order_id,OLD.account_generation,OLD.actor_id,OLD.trace_id,
       OLD.created_at) OR NEW.version<>OLD.version+1 OR NEW.delete_attempts<OLD.delete_attempts
       THEN RAISE EXCEPTION 'immutable cancel identity'; END IF;
      RETURN NEW;
    END;
    """,
    )
    op.execute(
        "CREATE TRIGGER paper_cancel_identity BEFORE UPDATE ON public.paper_cancels "
        "FOR EACH ROW EXECUTE FUNCTION public.paper_cancel_identity()"
    )
    op.execute(
        "CREATE TRIGGER paper_cancel_no_delete BEFORE DELETE OR TRUNCATE "
        "ON public.paper_cancels FOR EACH STATEMENT "
        "EXECUTE FUNCTION public.ledger_reject_mutation()"
    )
    function("paper_cancel_owner", "p_owner text,p_fence bigint", "boolean", _OWNER)
    function("paper_cancel_authority", AUTH_ARGS + ",p_delete boolean", "boolean", _AUTHORITY)
    function("paper_cancel_read", "p_cancel uuid", "jsonb", _READ)
    function(
        "paper_cancel_claim",
        "p_exec_owner text,p_exec_fence bigint,p_worker uuid,p_seconds integer",
        "jsonb",
        _CLAIM,
    )
    function("paper_cancel_begin_lookup", AUTH_ARGS, "jsonb", _BEGIN_LOOKUP)
    function("paper_cancel_begin_delete", AUTH_ARGS + ",p_lookup uuid", "jsonb", _BEGIN_DELETE)
    function(
        "paper_cancel_record",
        "p_attempt uuid,p_worker uuid,p_lease_fence bigint,"
        "p_exec_owner text,p_exec_fence bigint,p_outcome jsonb",
        "boolean",
        _RECORD,
    )
    function("paper_cancel_finish", AUTH_ARGS, "boolean", _FINISH)
    function("paper_cancel_link", "p_operation uuid,p_order text,p_origin text", "uuid", _LINK)
    _wrappers()
    op.execute("""
    DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_execution') THEN
      GRANT EXECUTE ON FUNCTION public.paper_cancel_read(uuid),
       public.paper_cancel_claim(text,bigint,uuid,integer),
       public.paper_cancel_begin_lookup(uuid,uuid,bigint,text,bigint),
       public.paper_cancel_begin_delete(uuid,uuid,bigint,text,bigint,uuid),
       public.paper_cancel_record(uuid,uuid,bigint,text,bigint,jsonb),
       public.paper_cancel_finish(uuid,uuid,bigint,text,bigint) TO atl_paper_execution;
    END IF; END $$;
    """)


_OWNER = (
    """
BEGIN
 IF NOT ("""
    + IDENTITY
    + """) OR p_owner IS NULL OR p_fence IS NULL OR p_fence<1 THEN RETURN false; END IF;
 RETURN EXISTS(SELECT 1 FROM public.execution_owners o WHERE o.account_id='paper'
  AND o.owner_id=p_owner AND o.fencing_token=p_fence
  AND o.heartbeat_at BETWEEN clock_timestamp()-interval '30 seconds' AND clock_timestamp());
END;
"""
)

_AUTHORITY = """
BEGIN
 IF NOT public.paper_cancel_owner(p_exec_owner,p_exec_fence)
  OR p_cancel IS NULL OR p_worker IS NULL OR p_lease_fence IS NULL THEN RETURN false; END IF;
 RETURN EXISTS(SELECT 1 FROM public.paper_cancels q
  JOIN public.broker_orders bo ON bo.id=q.order_id
  JOIN public.order_intents oi ON oi.id=bo.intent_id
  CROSS JOIN public.paper_workflow_state s
  JOIN public.paper_initial_bases ib ON ib.account_id='paper'
  WHERE q.id=p_cancel AND q.state<>'FINISHED' AND q.worker_id=p_worker
   AND q.lease_fence=p_lease_fence AND q.lease_until>clock_timestamp()
   AND q.exec_owner=p_exec_owner AND q.exec_fence=p_exec_fence AND oi.account_id='paper'
   AND bo.adapter='alpaca-paper' AND q.account_generation=s.account_generation
   AND s.pinned_account_id=(ib.evidence->>'providerAccountId')::uuid AND s.active_generation>0
   AND (NOT p_delete OR EXISTS(SELECT 1 FROM public.paper_commands verified
    WHERE verified.operation_type='VERIFY_CONNECTION' AND verified.status='SUCCEEDED'
     AND verified.credential_generation=s.active_generation)));
END;
"""

_READ = (
    """
BEGIN
 IF NOT ("""
    + IDENTITY
    + """) THEN RETURN NULL; END IF;
 RETURN (SELECT jsonb_build_object('cancelId',q.id,'workerId',q.worker_id,
  'leaseFence',q.lease_fence,'execOwner',q.exec_owner,'execFence',q.exec_fence,
  'leaseUntil',CASE WHEN q.lease_until IS NULL THEN NULL ELSE
   to_char(q.lease_until AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
  'orderId',bo.id,'intentId',oi.id,'clientOrderId',oi.client_order_id,'brokerOrderId',
 bo.broker_order_id,
  'accountGeneration',q.account_generation,'credentialGeneration',s.active_generation,
  'pinnedAccountId',s.pinned_account_id,'actorId',q.actor_id,'traceId',q.trace_id,'state',q.state,
  'deleteAttempts',q.delete_attempts,'notBefore',
   to_char(q.not_before AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'lookupOnly',q.lookup_only,'lastAttemptId',a.id,'lastAttemptKind',a.kind)
  FROM public.paper_cancels q JOIN public.broker_orders bo ON bo.id=q.order_id
  JOIN public.order_intents oi ON oi.id=bo.intent_id CROSS JOIN public.paper_workflow_state s
  LEFT JOIN LATERAL(SELECT id,kind FROM public.paper_cancel_attempts WHERE cancel_id=q.id
   ORDER BY started_at DESC,id DESC LIMIT 1) a ON true WHERE q.id=p_cancel);
END;
"""
)

_CLAIM = """
DECLARE q public.paper_cancels%ROWTYPE;
BEGIN
 IF NOT public.paper_cancel_owner(p_exec_owner,p_exec_fence) OR p_worker IS NULL
  OR p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 30 THEN RETURN NULL; END IF;
 PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
 SELECT * INTO q FROM public.paper_cancels WHERE state<>'FINISHED'
  AND not_before<=clock_timestamp() AND (lease_until IS NULL OR lease_until<=clock_timestamp())
  AND lease_fence<9007199254740991 ORDER BY not_before,created_at,id
  FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 UPDATE public.paper_cancels SET worker_id=p_worker,lease_fence=lease_fence+1,
  lease_until=clock_timestamp()+make_interval(secs=>p_seconds),exec_owner=p_exec_owner,
  exec_fence=p_exec_fence,version=version+1 WHERE id=q.id RETURNING * INTO q;
 PERFORM public.paper_cancel_audit(q.id::text,q.version,q.actor_id,q.trace_id);
 UPDATE public.paper_commands c SET status='RUNNING',updated_at=clock_timestamp()
  FROM public.paper_cancel_links l WHERE l.operation_id=c.id AND l.cancel_id=q.id
  AND l.origin='EXPLICIT_CANCEL' AND c.status='ACCEPTED';
 RETURN public.paper_cancel_read(q.id);
END;
"""

_BEGIN_LOOKUP = """
DECLARE q public.paper_cancels%ROWTYPE; a uuid; generation bigint; pin uuid;
BEGIN
 PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
 SELECT * INTO q FROM public.paper_cancels WHERE id=p_cancel FOR UPDATE;
 IF NOT public.paper_cancel_authority(p_cancel,p_worker,p_lease_fence,p_exec_owner,
 p_exec_fence,false)
  THEN RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
 SELECT active_generation,pinned_account_id INTO generation,pin
  FROM public.paper_workflow_state WHERE singleton;
 INSERT INTO public.paper_cancel_attempts(cancel_id,kind,worker_id,lease_fence,exec_owner,
  exec_fence,credential_generation,actor_id,trace_id) VALUES(q.id,'LOOKUP',p_worker,
  p_lease_fence,p_exec_owner,p_exec_fence,generation,q.actor_id,q.trace_id) RETURNING id INTO a;
 PERFORM public.paper_cancel_audit(a::text,1,q.actor_id,q.trace_id);
 RETURN jsonb_build_object('attemptId',a,'credentialGeneration',generation,
  'accountGeneration',q.account_generation,'pinnedAccountId',pin);
END;
"""

_BEGIN_DELETE = """
DECLARE q public.paper_cancels%ROWTYPE; lookup public.paper_cancel_attempts%ROWTYPE;
 proof jsonb; a uuid; bo public.broker_orders%ROWTYPE; report_id text;
BEGIN
 PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
 SELECT * INTO q FROM public.paper_cancels WHERE id=p_cancel FOR UPDATE;
 IF NOT public.paper_cancel_authority(p_cancel,p_worker,p_lease_fence,p_exec_owner,
 p_exec_fence,true)
  THEN RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
 SELECT * INTO lookup FROM public.paper_cancel_attempts WHERE id=p_lookup AND cancel_id=q.id
  AND kind='LOOKUP' AND worker_id=p_worker AND lease_fence=p_lease_fence;
 SELECT outcome INTO proof FROM public.paper_cancel_results WHERE attempt_id=lookup.id;
 IF q.lookup_only OR q.delete_attempts>=3 OR q.not_before>clock_timestamp()
  OR lookup.id IS NULL OR lookup.credential_generation<>(SELECT active_generation
   FROM public.paper_workflow_state WHERE singleton)
  OR proof->>'kind' IS DISTINCT FROM 'OBSERVED'
  OR (proof->>'observedAt')::timestamptz<=clock_timestamp()-interval '30 seconds'
  OR (proof->>'observedAt')::timestamptz>clock_timestamp()
  OR proof->'order'->>'status' NOT IN ('ACKNOWLEDGED','PARTIALLY_FILLED')
  THEN RETURN jsonb_build_object('error','BROKER_STATUS_UNKNOWN'); END IF;
 SELECT * INTO bo FROM public.broker_orders WHERE id=q.order_id FOR UPDATE;
 IF bo.status IN ('FILLED','CANCELED','REJECTED') THEN
  RETURN jsonb_build_object('error','ORDER_NOT_FOUND'); END IF;
 INSERT INTO public.paper_cancel_attempts(cancel_id,kind,worker_id,lease_fence,exec_owner,
  exec_fence,credential_generation,actor_id,trace_id) VALUES(q.id,'DELETE',p_worker,
  p_lease_fence,p_exec_owner,p_exec_fence,lookup.credential_generation,q.actor_id,q.trace_id)
  RETURNING id INTO a;
 PERFORM public.paper_cancel_audit(a::text,1,q.actor_id,q.trace_id);
 UPDATE public.paper_cancels SET state='LOOKUP_ONLY',lookup_only=true,
  delete_attempts=delete_attempts+1,version=version+1 WHERE id=q.id RETURNING * INTO q;
 PERFORM public.paper_cancel_audit(q.id::text,q.version,q.actor_id,q.trace_id);
 PERFORM public.paper_cancel_audit(bo.id,bo.version+1,'execution-cancel',bo.trace_id);
 UPDATE public.broker_orders SET status='CANCEL_PENDING',version=version+1,
  broker_order_id=coalesce(broker_order_id,proof->'order'->>'brokerOrderId'),
  updated_at=clock_timestamp() WHERE id=bo.id;
 report_id:='paper-cancel-report-'||a::text;
 PERFORM public.paper_cancel_audit(report_id,1,'execution-cancel',bo.trace_id);
 INSERT INTO public.execution_reports(id,order_id,status,requested_quantity,filled_quantity,
  reconciliation,occurred_at,received_at,trace_id,reason_code,adapter)
  VALUES(report_id,bo.id,'CANCEL_PENDING',bo.requested_quantity,bo.filled_quantity,'PENDING',
   clock_timestamp(),clock_timestamp(),bo.trace_id,NULL,'alpaca-paper');
 INSERT INTO public.outbox_events(event_id,event_type,occurred_at,aggregate_id,aggregate_version,
  trace_id,schema_version,payload) SELECT gen_random_uuid()::text,'execution.reported.v1',
  occurred_at,id,1,trace_id,1,
  jsonb_set(jsonb_set(to_jsonb(r)-'adapter','{requested_quantity}',
   to_jsonb(trim_scale(r.requested_quantity)::text)),'{filled_quantity}',
   to_jsonb(trim_scale(r.filled_quantity)::text)) FROM public.execution_reports r
  WHERE id=report_id;
 PERFORM public.safety_latch('paper','PAUSE_NEW','execution-cancel',q.trace_id,
  'TEMPORARILY_UNAVAILABLE');
 RETURN jsonb_build_object('attemptId',a);
END;
"""

_RECORD = """
DECLARE a public.paper_cancel_attempts%ROWTYPE; q public.paper_cancels%ROWTYPE;
 prior jsonb; observed timestamptz; body jsonb; seconds integer; kind text;
BEGIN
 PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
 SELECT * INTO a FROM public.paper_cancel_attempts WHERE id=p_attempt;
 SELECT * INTO q FROM public.paper_cancels WHERE id=a.cancel_id FOR UPDATE;
 IF NOT public.paper_cancel_authority(q.id,p_worker,p_lease_fence,p_exec_owner,p_exec_fence,false)
  OR a.worker_id IS DISTINCT FROM p_worker OR a.lease_fence IS DISTINCT FROM p_lease_fence
  OR a.exec_owner IS DISTINCT FROM p_exec_owner OR a.exec_fence IS DISTINCT FROM p_exec_fence
  THEN RETURN false; END IF;
 IF p_outcome IS NULL OR jsonb_typeof(p_outcome)<>'object' OR pg_column_size(p_outcome)>4096
  OR NOT p_outcome ?& ARRAY['kind','observedAt','retryAfterSeconds','deleteAttempted','order']
  OR (p_outcome-ARRAY['kind','observedAt','retryAfterSeconds','deleteAttempted','order'])<>'{}'
  OR jsonb_typeof(p_outcome->'kind') IS DISTINCT FROM 'string'
  OR p_outcome->>'kind' NOT IN ('OBSERVED','ACCEPTED','THROTTLED','NOT_CANCELABLE','UNKNOWN',
 'NOT_SENT')
  OR jsonb_typeof(p_outcome->'observedAt')<>'string'
  OR p_outcome->>'observedAt' !~ '^[0-9-]{10}T[0-9:.]{8,18}Z$'
  OR jsonb_typeof(p_outcome->'deleteAttempted')<>'boolean' THEN RETURN false; END IF;
 observed:=(p_outcome->>'observedAt')::timestamptz; kind:=p_outcome->>'kind';
 IF observed<a.started_at OR observed>clock_timestamp() OR
 observed<=clock_timestamp()-interval '30 seconds'
  OR (p_outcome->>'deleteAttempted')::boolean IS DISTINCT FROM (a.kind='DELETE') THEN RETURN
 false; END IF;
 IF kind='THROTTLED' THEN
  IF a.kind<>'DELETE' OR jsonb_typeof(p_outcome->'retryAfterSeconds')<>'number'
   OR p_outcome->>'retryAfterSeconds' !~ '^[1-9][0-9]?$'
   OR (p_outcome->>'retryAfterSeconds')::int>60 THEN RETURN false; END IF;
  seconds:=(p_outcome->>'retryAfterSeconds')::int;
 ELSIF p_outcome->'retryAfterSeconds'<>'null'::jsonb THEN RETURN false; END IF;
 IF kind='OBSERVED' THEN
  body:=p_outcome->'order';
  IF a.kind<>'LOOKUP' OR jsonb_typeof(body)<>'object' OR NOT body ?& ARRAY[
   'clientOrderId','brokerOrderId','status','requestedQuantity','filledQuantity']
   OR (body-ARRAY['clientOrderId','brokerOrderId','status','requestedQuantity',
 'filledQuantity'])<>'{}'
   OR EXISTS(SELECT 1 FROM jsonb_each(body) item WHERE jsonb_typeof(item.value)<>'string')
   OR body->>'status' NOT IN ('CREATED','RISK_RESERVED','SUBMITTING','ACKNOWLEDGED',
    'PARTIALLY_FILLED','CANCEL_PENDING','UNKNOWN','FILLED','CANCELED','REJECTED')
   OR body->>'brokerOrderId' !~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
   OR jsonb_typeof(body->'requestedQuantity')<>'string'
   OR jsonb_typeof(body->'filledQuantity')<>'string'
   OR body->>'requestedQuantity' !~ '^(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$'
   OR body->>'filledQuantity' !~ '^(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$'
   OR NOT EXISTS(SELECT 1 FROM public.broker_orders bo JOIN public.order_intents oi ON
 oi.id=bo.intent_id
    WHERE bo.id=q.order_id AND oi.account_id='paper' AND oi.client_order_id=body->>'clientOrderId'
     AND oi.quantity=(body->>'requestedQuantity')::numeric
     AND (body->>'filledQuantity')::numeric BETWEEN bo.filled_quantity AND oi.quantity
     AND (bo.broker_order_id IS NULL OR bo.broker_order_id=body->>'brokerOrderId')) THEN
 RETURN false; END IF;
 ELSIF p_outcome->'order'<>'null'::jsonb THEN RETURN false; END IF;
 IF (a.kind='LOOKUP' AND kind NOT IN ('OBSERVED','UNKNOWN','NOT_SENT'))
  OR (a.kind='DELETE' AND kind NOT IN ('ACCEPTED','THROTTLED','NOT_CANCELABLE','UNKNOWN')) THEN
  RETURN false; END IF;
 SELECT outcome INTO prior FROM public.paper_cancel_results WHERE attempt_id=a.id;
 IF FOUND THEN RETURN prior=p_outcome; END IF;
 INSERT INTO public.paper_cancel_results VALUES(a.id::text,a.id,p_outcome,q.actor_id,q.trace_id);
 PERFORM public.paper_cancel_audit(a.id::text,1,q.actor_id,q.trace_id);
 UPDATE public.paper_cancels SET state=CASE
   WHEN kind='THROTTLED' AND delete_attempts<3 THEN 'BACKOFF'
   WHEN lookup_only OR a.kind='DELETE' THEN 'LOOKUP_ONLY' ELSE state END,
  lookup_only=CASE WHEN kind='THROTTLED' AND delete_attempts<3 THEN false ELSE lookup_only END,
  not_before=CASE WHEN kind='THROTTLED' THEN clock_timestamp()+make_interval(secs=>seconds)
   ELSE not_before END,last_error=CASE WHEN kind='THROTTLED' THEN 'PROVIDER_RATE_LIMITED'
   WHEN kind='UNKNOWN' THEN 'BROKER_STATUS_UNKNOWN'
   WHEN kind IN ('OBSERVED','ACCEPTED','NOT_CANCELABLE') THEN NULL ELSE last_error END,
  version=version+1 WHERE id=q.id RETURNING * INTO q;
 PERFORM public.paper_cancel_audit(q.id::text,q.version,q.actor_id,q.trace_id);
 IF kind IN ('THROTTLED','UNKNOWN') THEN
  PERFORM public.safety_latch('paper','PAUSE_NEW','execution-cancel',q.trace_id,
   'TEMPORARILY_UNAVAILABLE');
 END IF;
 RETURN true;
EXCEPTION WHEN invalid_text_representation OR datetime_field_overflow OR numeric_value_out_of_range
 THEN RETURN false;
END;
"""

_FINISH = """
DECLARE q public.paper_cancels%ROWTYPE; r public.paper_reconciliation_receipts%ROWTYPE; c record;
BEGIN
 PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
 SELECT * INTO q FROM public.paper_cancels WHERE id=p_cancel FOR UPDATE;
 IF q.state='FINISHED' AND public.paper_cancel_owner(p_exec_owner,p_exec_fence) THEN RETURN
 true; END IF;
 IF NOT public.paper_cancel_authority(p_cancel,p_worker,p_lease_fence,p_exec_owner,
 p_exec_fence,false)
  THEN RETURN false; END IF;
 SELECT * INTO r FROM public.paper_reconciliation_receipts WHERE account_id='paper'
  ORDER BY observed_at DESC,id DESC LIMIT 1;
 IF r.id IS NULL OR NOT r.ready OR r.owner_id<>p_exec_owner OR r.fencing_token<>p_exec_fence
  OR r.observed_at<=clock_timestamp()-interval '30 seconds' OR r.observed_at>clock_timestamp()
  OR NOT EXISTS(SELECT 1 FROM public.risk_account_context ac
   WHERE ac.account_id='paper' AND ac.portfolio_snapshot_id=r.portfolio_snapshot_id
    AND ac.portfolio_version=r.portfolio_version)
  OR NOT EXISTS(SELECT 1 FROM public.broker_orders bo
   JOIN public.order_intents oi ON oi.id=bo.intent_id
   JOIN public.risk_reservations rr ON rr.proposal_id=oi.proposal_id
   WHERE bo.id=q.order_id AND bo.status IN ('FILLED','CANCELED','REJECTED') AND rr.state IN
 ('RELEASED','CONSUMED')
    AND EXISTS(SELECT 1 FROM public.audit_events oa,public.audit_events ra
     WHERE oa.entity_id=bo.id AND oa.entity_version=bo.version
      AND ra.entity_id=r.id AND ra.action='execution.paper_receipt'
      AND oa.transaction_id<=ra.transaction_id)) THEN
  IF EXISTS(SELECT 1 FROM public.paper_cancel_attempts a
   JOIN public.paper_cancel_results ar ON ar.attempt_id=a.id
   WHERE a.cancel_id=q.id AND a.lease_fence=q.lease_fence) THEN
   UPDATE public.paper_cancels SET lease_until=NULL,not_before=greatest(not_before,
    clock_timestamp()+interval '1 second'),version=version+1 WHERE id=q.id RETURNING * INTO q;
   PERFORM public.paper_cancel_audit(q.id::text,q.version,q.actor_id,q.trace_id);
  END IF;
  RETURN false; END IF;
 UPDATE public.paper_cancels SET state='FINISHED',lease_until=NULL,version=version+1
  WHERE id=q.id RETURNING * INTO q;
 PERFORM public.paper_cancel_audit(q.id::text,q.version,q.actor_id,q.trace_id);
 FOR c IN SELECT pc.id,pc.accepted_generation,pc.fence FROM public.paper_cancel_links l
  JOIN public.paper_commands pc ON pc.id=l.operation_id WHERE l.cancel_id=q.id
   AND l.origin='EXPLICIT_CANCEL' AND pc.status IN ('ACCEPTED','RUNNING') LOOP
  UPDATE public.paper_commands SET status='SUCCEEDED',error_code=NULL,lease_until=NULL,
   updated_at=clock_timestamp() WHERE id=c.id;
  INSERT INTO public.paper_command_events(operation_id,event_type,epoch,fence)
   VALUES(c.id,'SUCCEEDED',c.accepted_generation,c.fence);
 END LOOP;
 RETURN true;
END;
"""

_LINK = """
DECLARE c public.paper_commands%ROWTYPE; q public.paper_cancels%ROWTYPE; s bigint; link text;
BEGIN
 SELECT * INTO c FROM public.paper_commands WHERE id=p_operation;
 SELECT account_generation INTO s FROM public.paper_workflow_state WHERE singleton;
 SELECT * INTO q FROM public.paper_cancels WHERE order_id=p_order FOR UPDATE;
 IF NOT FOUND THEN
  INSERT INTO public.paper_cancels(order_id,account_generation,actor_id,trace_id)
   VALUES(p_order,s,c.actor_id,c.trace_id) RETURNING * INTO q;
  PERFORM public.paper_cancel_audit(q.id::text,1,q.actor_id,q.trace_id);
 END IF;
 link:=c.id::text||':'||q.id::text;
 IF NOT EXISTS(SELECT 1 FROM public.paper_cancel_links WHERE id=link) THEN
  INSERT INTO public.paper_cancel_links VALUES(link,q.id,c.id,p_origin,c.actor_id,c.trace_id);
  PERFORM public.paper_cancel_audit(link,1,c.actor_id,c.trace_id);
 END IF;
 RETURN q.id;
END;
"""


def _wrappers():
    op.execute("""
    ALTER FUNCTION public.paper_enqueue(text,jsonb,text,text,text) RENAME TO paper_enqueue_v18;
    ALTER FUNCTION public.paper_status(text) RENAME TO paper_status_v18;
    ALTER FUNCTION public.paper_finish(uuid,uuid,bigint,jsonb,uuid,text) RENAME TO paper_finish_v18;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_control_api') THEN
        REVOKE ALL ON FUNCTION public.paper_enqueue_v18(text,jsonb,text,text,text),
         public.paper_status_v18(text) FROM atl_paper_control_api;
      END IF;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_execution') THEN
        REVOKE ALL ON FUNCTION public.paper_finish_v18(uuid,uuid,bigint,jsonb,uuid,text)
         FROM atl_paper_execution;
      END IF;
    END $$;
    """)
    function(
        "paper_enqueue",
        "p_kind text,p_body jsonb,p_actor text,p_key text,p_trace text",
        "jsonb",
        """
    DECLARE s public.paper_workflow_state%ROWTYPE; result jsonb; row_id text; prior uuid;
    BEGIN
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_control_api_login'
       OR current_setting('role')<>'atl_paper_control_api' THEN RETURN NULL; END IF;
      IF NOT public.paper_payload_valid(p_kind,p_body) THEN
       RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
      SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
      PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
      SELECT id INTO prior FROM public.paper_commands WHERE actor_id=p_actor AND
 idempotency_key=p_key;
      IF prior IS NULL AND p_kind='CANCEL_ORDER' THEN
       IF (p_body->>'accountGeneration')::bigint<>s.account_generation THEN
        RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
       IF NOT EXISTS(SELECT 1 FROM public.broker_orders bo JOIN public.order_intents oi ON
 oi.id=bo.intent_id
        WHERE bo.id=p_body->>'orderId' AND bo.adapter='alpaca-paper' AND oi.account_id='paper') THEN
        RETURN jsonb_build_object('error','ORDER_NOT_FOUND'); END IF;
       IF EXISTS(SELECT 1 FROM public.broker_orders bo WHERE bo.id=p_body->>'orderId'
        AND bo.status IN ('FILLED','CANCELED','REJECTED')) OR EXISTS(
         SELECT 1 FROM public.paper_cancels WHERE order_id=p_body->>'orderId' AND
 state='FINISHED') THEN
        RETURN jsonb_build_object('error','ORDER_NOT_FOUND'); END IF;
      END IF;
      result:=public.paper_enqueue_v18(p_kind,p_body,p_actor,p_key,p_trace);
      IF result IS NULL OR result ? 'error' THEN RETURN result; END IF;
      IF p_kind='CANCEL_ORDER' THEN
       PERFORM public.paper_cancel_link((result->>'operationId')::uuid,p_body->>'orderId',
 'EXPLICIT_CANCEL');
      ELSIF p_kind='KILL' THEN
       FOR row_id IN SELECT bo.id FROM public.broker_orders bo JOIN public.order_intents oi
 ON oi.id=bo.intent_id
        WHERE oi.account_id='paper' AND bo.adapter='alpaca-paper'
         AND bo.status NOT IN ('FILLED','CANCELED','REJECTED') LOOP
        PERFORM public.paper_cancel_link((result->>'operationId')::uuid,row_id,'KILL_DRAIN');
       END LOOP;
      END IF;
      RETURN result;
    END;
    """,
    )
    function(
        "paper_finish",
        "p_id uuid,p_owner uuid,p_fence bigint,p_public jsonb,p_private uuid,p_error text",
        "boolean",
        """
    BEGIN
      IF EXISTS(SELECT 1 FROM public.paper_commands WHERE id=p_id AND operation_type='CANCEL_ORDER')
       THEN RETURN false; END IF;
      RETURN public.paper_finish_v18(p_id,p_owner,p_fence,p_public,p_private,p_error);
    END;
    """,
    )
    function(
        "paper_status",
        "p_trace text",
        "jsonb",
        """
    DECLARE result jsonb; failure text; blockers jsonb;
    BEGIN
      result:=public.paper_status_v18(p_trace);
      SELECT last_error INTO failure FROM public.paper_cancels WHERE state<>'FINISHED'
       AND last_error IS NOT NULL ORDER BY CASE last_error WHEN 'BROKER_STATUS_UNKNOWN' THEN
 0 ELSE 1 END,
       created_at,id LIMIT 1;
      IF failure IS NOT NULL THEN
       SELECT jsonb_agg(value ORDER BY value) INTO blockers FROM (
        SELECT DISTINCT value FROM jsonb_array_elements_text((result->'blockers')||
         jsonb_build_array(failure)) value LIMIT 37) bounded;
       result:=jsonb_set(jsonb_set(result,'{blockers}',blockers),'{entryGateOpen}','false');
       IF result->>'operatingState'='RUNNING' THEN
        result:=jsonb_set(result,'{operatingState}','"PAUSED"'); END IF;
      END IF;
      RETURN result;
    END;
    """,
    )
    # Preserve all established main-lane checks, excluding only independent cancels.
    op.execute("""
    DO $$ DECLARE definition text; BEGIN
      SELECT pg_get_functiondef('public.paper_claim(uuid,integer)'::regprocedure) INTO definition;
      IF position('WHERE status=''RUNNING''' IN definition)=0
       OR position('WHERE status IN (''ACCEPTED'',''RUNNING'')' IN definition)=0 THEN
       RAISE EXCEPTION 'unexpected Paper command claim definition'; END IF;
      definition:=replace(definition,'WHERE status=''RUNNING''',
       'WHERE operation_type<>''CANCEL_ORDER'' AND status=''RUNNING''');
      definition:=replace(definition,'WHERE status IN (''ACCEPTED'',''RUNNING'')',
       'WHERE operation_type<>''CANCEL_ORDER'' AND status IN (''ACCEPTED'',''RUNNING'')');
      EXECUTE definition;
    END $$;
    DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_control_api') THEN
        GRANT EXECUTE ON FUNCTION public.paper_enqueue(text,jsonb,text,text,text),
         public.paper_status(text) TO atl_paper_control_api;
      END IF;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_execution') THEN
        GRANT EXECUTE ON FUNCTION public.paper_finish(uuid,uuid,bigint,jsonb,uuid,text) TO
 atl_paper_execution;
      END IF;
    END $$;
    """)
