"""Durable secret-free Paper commands before financial account initialization."""

from alembic import op

revision = "0013_paper_commands"
down_revision = "0012_trading_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE public.paper_workflow_state (
      singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
      epoch bigint NOT NULL DEFAULT 1 CHECK(epoch BETWEEN 1 AND 9007199254740991),
      credential_counter bigint NOT NULL DEFAULT 0
        CHECK(credential_counter BETWEEN 0 AND 9007199254740991),
      pending_generation bigint CHECK(pending_generation BETWEEN 1 AND 9007199254740991),
      saved_generation bigint NOT NULL DEFAULT 0
        CHECK(saved_generation BETWEEN 0 AND 9007199254740991),
      active_generation bigint NOT NULL DEFAULT 0
        CHECK(active_generation BETWEEN 0 AND 9007199254740991),
      account_generation bigint NOT NULL DEFAULT 0
        CHECK(account_generation BETWEEN 0 AND 9007199254740991),
      pinned_account_id uuid, account_label varchar(14),
      verified_at timestamptz, cash numeric(38,18),
      operating_state varchar(12) NOT NULL DEFAULT 'PAUSED'
        CHECK(operating_state IN ('PAUSED','STOPPED','HALTED')),
      blocker varchar(60) NOT NULL DEFAULT 'CREDENTIALS_MISSING',
      changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    INSERT INTO public.paper_workflow_state(singleton) VALUES(true);
    CREATE TABLE public.paper_commands (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      actor_id varchar(255) NOT NULL, idempotency_key varchar(128) NOT NULL,
      operation_type varchar(30) NOT NULL CHECK(operation_type IN
        ('VERIFY_CONNECTION','PREPARE_LEDGER','START','PAUSE','STOP','KILL','PLACE_LIMIT_ORDER',
        'CANCEL_ORDER')),
      payload jsonb NOT NULL, trace_id varchar(32) NOT NULL,
      accepted_generation bigint NOT NULL CHECK(accepted_generation BETWEEN 1 AND 9007199254740991),
      credential_generation bigint NOT NULL
        CHECK(credential_generation BETWEEN 0 AND 9007199254740991),
      status varchar(12) NOT NULL CHECK(status IN ('ACCEPTED','RUNNING','SUCCEEDED','BLOCKED',
      'FAILED')),
      accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      owner uuid, fence bigint NOT NULL DEFAULT 0, lease_until timestamptz,
      error_code varchar(60), order_id uuid,
      UNIQUE(actor_id,idempotency_key)
    );
    CREATE TABLE public.paper_rotation_receipts (
      actor_id varchar(255) NOT NULL, idempotency_key varchar(128) NOT NULL,
      generation bigint NOT NULL, PRIMARY KEY(actor_id,idempotency_key)
    );
    CREATE TABLE public.paper_command_events (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(), operation_id uuid REFERENCES
      public.paper_commands(id),
      event_type varchar(30) NOT NULL, epoch bigint NOT NULL, fence bigint NOT NULL DEFAULT 0,
      occurred_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    REVOKE ALL ON public.paper_workflow_state,public.paper_commands,
      public.paper_rotation_receipts,public.paper_command_events FROM PUBLIC;
    CREATE TRIGGER paper_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
      ON public.paper_command_events FOR EACH STATEMENT EXECUTE FUNCTION
      public.ledger_reject_mutation();
    CREATE TRIGGER paper_rotation_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
      ON public.paper_rotation_receipts FOR EACH STATEMENT EXECUTE FUNCTION
      public.ledger_reject_mutation();
    """)
    op.execute("""
      CREATE FUNCTION public.paper_command_guard() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog,public,pg_temp AS $$ BEGIN
        IF (NEW.id,NEW.actor_id,NEW.idempotency_key,NEW.operation_type,NEW.payload,
            NEW.trace_id,NEW.accepted_generation,NEW.credential_generation,NEW.accepted_at,
            NEW.order_id)
          IS DISTINCT FROM
           (OLD.id,OLD.actor_id,OLD.idempotency_key,OLD.operation_type,OLD.payload,
            OLD.trace_id,OLD.accepted_generation,OLD.credential_generation,OLD.accepted_at,
            OLD.order_id)
        THEN RAISE EXCEPTION 'immutable command identity'; END IF;
        RETURN NEW;
      END $$;
      REVOKE ALL ON FUNCTION public.paper_command_guard() FROM PUBLIC;
      CREATE TRIGGER paper_command_identity_immutable BEFORE UPDATE ON public.paper_commands
        FOR EACH ROW EXECUTE FUNCTION public.paper_command_guard();
      CREATE TRIGGER paper_commands_no_delete BEFORE DELETE OR TRUNCATE ON public.paper_commands
        FOR EACH STATEMENT EXECUTE FUNCTION public.ledger_reject_mutation();
    """)
    function(
        "paper_operation",
        "p_id uuid",
        "jsonb",
        """
      RETURN (SELECT jsonb_build_object('operationId',id,'operationType',operation_type,
        'status',status,'idempotencyKey',idempotency_key,'acceptedGeneration',accepted_generation,
        'acceptedAt',to_char(accepted_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'updatedAt',to_char(updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'traceId',trace_id) || CASE WHEN error_code IS NULL THEN '{}'::jsonb
        ELSE jsonb_build_object('errorCode',error_code) END || CASE WHEN order_id IS NULL
        THEN '{}'::jsonb ELSE jsonb_build_object('orderId',order_id) END
        FROM public.paper_commands WHERE id=p_id);
    """,
    )
    function(
        "paper_status",
        "p_trace text",
        "jsonb",
        """
      RETURN (SELECT jsonb_build_object('namespace','alpaca-paper','environment','PAPER',
        'setupState',CASE WHEN verified_at IS NOT NULL THEN 'VERIFIED'
          WHEN saved_generation>0 THEN 'SAVED' ELSE 'NOT_CONFIGURED' END,
        'operatingState',operating_state,'entryGateOpen',false,
        'credentialGeneration',coalesce(pending_generation,active_generation,saved_generation),
        'accountGeneration',account_generation,'accountLabel',account_label,'currency','USD',
        'verifiedAt',CASE WHEN verified_at IS NULL THEN NULL ELSE
          to_char(verified_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
        'reconciledAt',NULL,'observedAt',to_char(clock_timestamp() AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'marketDataFeed',CASE WHEN verified_at IS NULL THEN 'UNVERIFIED' ELSE 'IEX' END,
        'providerVerification',CASE WHEN verified_at IS NULL THEN 'UNVERIFIED' ELSE 'CONFIRMED' END,
        'blockers',CASE WHEN blocker='OK' THEN '[]'::jsonb ELSE jsonb_build_array(blocker) END,
        'traceId',p_trace) || CASE WHEN cash IS NULL THEN '{}'::jsonb
          ELSE jsonb_build_object('cash',trim_scale(cash)::text) END
        FROM public.paper_workflow_state WHERE singleton);
    """,
    )
    function(
        "paper_rotation_begin",
        "p_actor text,p_key text,p_trace text",
        "bigint",
        """
      DECLARE s public.paper_workflow_state%ROWTYPE; g bigint;
      BEGIN
        IF p_actor IS NULL OR p_actor !~ '^[A-Za-z0-9._:-]{1,255}$'
          OR p_key IS NULL OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$'
          OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$' THEN RAISE EXCEPTION
          'invalid request'; END IF;
        SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
        SELECT generation INTO g FROM public.paper_rotation_receipts WHERE actor_id=p_actor AND
        idempotency_key=p_key;
        IF FOUND THEN RETURN g; END IF;
        IF s.credential_counter>=9007199254740991 OR s.epoch>=9007199254740991
        THEN RETURN NULL; END IF;
        g:=s.credential_counter+1;
        UPDATE public.paper_workflow_state SET credential_counter=g,pending_generation=g,
          epoch=epoch+1,verified_at=NULL,cash=NULL,blocker='CREDENTIALS_MISSING',
          operating_state=CASE WHEN operating_state='HALTED' THEN 'HALTED' ELSE 'PAUSED' END,
          changed_at=clock_timestamp() WHERE singleton;
        INSERT INTO public.paper_rotation_receipts VALUES(p_actor,p_key,g);
        INSERT INTO public.paper_command_events(event_type,epoch) VALUES('ROTATION_BEGUN',
        s.epoch+1);
        RETURN g;
      END;
    """,
        block=True,
    )
    function(
        "paper_rotation_complete",
        "p_generation bigint",
        "boolean",
        """
      UPDATE public.paper_workflow_state SET saved_generation=p_generation,
        blocker='CONNECTION_NOT_VERIFIED',changed_at=clock_timestamp()
        WHERE singleton AND pending_generation=p_generation;
      RETURN FOUND;
    """,
    )
    # Defense in depth: even callers bypassing HTTP cannot put secrets or arbitrary text in
    # payloads.
    function(
        "paper_payload_valid",
        "p_kind text,p_body jsonb",
        "boolean",
        """
      DECLARE k text; v jsonb; allowed text[];
      BEGIN
        IF jsonb_typeof(p_body)<>'object' OR pg_column_size(p_body)>8192 THEN RETURN false; END IF;
        allowed:=CASE p_kind
          WHEN 'VERIFY_CONNECTION' THEN ARRAY['credentialGeneration']
          WHEN 'PREPARE_LEDGER' THEN ARRAY['credentialGeneration','accountGeneration',
          'verificationOperationId']
          WHEN 'PAUSE' THEN ARRAY['action'] WHEN 'STOP' THEN ARRAY['action'] WHEN 'KILL' THEN
          ARRAY['action']
          WHEN 'CANCEL_ORDER' THEN ARRAY['accountGeneration','confirmation','orderId']
          WHEN 'START' THEN ARRAY['credentialGeneration','accountGeneration','policyVersion',
          'profileVersion',
            'limitPolicyVersion','budgetVersion','riskBudget','currency','expiresAt','symbols',
            'strategy','confirmation']
          WHEN 'PLACE_LIMIT_ORDER' THEN ARRAY['credentialGeneration','accountGeneration',
          'policyVersion','profileVersion',
            'limitPolicyVersion','budgetVersion','riskBudget','currency','expiresAt','symbol',
            'side','quantity',
            'limitPrice','orderType','timeInForce','session','confirmation'] ELSE NULL END;
        IF allowed IS NULL OR (SELECT count(*) FROM
        jsonb_object_keys(p_body))<>cardinality(allowed) THEN RETURN false; END IF;
        FOR k,v IN SELECT * FROM jsonb_each(p_body) LOOP
          IF NOT k=ANY(allowed) THEN RETURN false; END IF;
          IF k ~ '(Generation|Version)$' THEN
            IF jsonb_typeof(v)<>'number' OR v::text !~ '^[1-9][0-9]{0,15}$'
              OR v::text::numeric>9007199254740991 THEN RETURN false; END IF;
          ELSIF k='symbols' THEN
            IF jsonb_typeof(v)<>'array' OR jsonb_array_length(v) NOT BETWEEN 1 AND 2
              OR EXISTS(SELECT 1 FROM jsonb_array_elements(v) x WHERE x NOT IN ('"SPY"','"QQQ"'))
              OR (SELECT count(DISTINCT x) FROM jsonb_array_elements(v)
              x)<>jsonb_array_length(v) THEN RETURN false; END IF;
          ELSIF jsonb_typeof(v)<>'string' THEN RETURN false;
          ELSIF k IN ('riskBudget','quantity','limitPrice') THEN
            IF v#>>'{}' !~ '^(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$' OR (v#>>'{}')::numeric<=0
              OR (k='riskBudget' AND (v#>>'{}')::numeric>1000000)
              OR (k='quantity' AND length(split_part(v#>>'{}','.',2))>9)
              OR (k='limitPrice' AND length(split_part(v#>>'{}','.',2))>6) THEN RETURN false;
              END IF;
          ELSIF k='expiresAt' THEN
            IF v#>>'{}' !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]{8,18}Z$' OR length(v#>>'{}')>30
            THEN RETURN false; END IF;
            PERFORM (v#>>'{}')::timestamptz;
          ELSIF k IN ('verificationOperationId','orderId') THEN
            IF v#>>'{}' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            THEN RETURN false; END IF;
          ELSIF k IN ('symbol','side','currency','orderType','timeInForce','session','strategy',
          'action','confirmation') THEN
            IF NOT (CASE k WHEN 'symbol' THEN v#>>'{}' IN ('SPY','QQQ') WHEN 'side' THEN
            v#>>'{}' IN ('BUY','SELL')
              WHEN 'currency' THEN v#>>'{}'='USD' WHEN 'orderType' THEN v#>>'{}'='LIMIT'
              WHEN 'timeInForce' THEN v#>>'{}'='DAY' WHEN 'session' THEN v#>>'{}'='REGULAR'
              WHEN 'strategy' THEN v#>>'{}' IN ('mock-neutral','neutral-baseline','jev')
              WHEN 'action' THEN v#>>'{}'=p_kind WHEN 'confirmation' THEN v#>>'{}'=CASE p_kind
                WHEN 'START' THEN 'START_PAPER' WHEN 'CANCEL_ORDER' THEN 'CANCEL_PAPER_ORDER'
                ELSE 'PLACE_PAPER_LIMIT' END ELSE false END) THEN RETURN false; END IF;
          ELSE RETURN false; END IF;
        END LOOP;
        RETURN true;
      EXCEPTION WHEN OTHERS THEN RETURN false;
      END;
    """,
        block=True,
    )
    function(
        "paper_enqueue",
        "p_kind text,p_body jsonb,p_actor text,p_key text,p_trace text",
        "jsonb",
        """
      DECLARE s public.paper_workflow_state%ROWTYPE; c public.paper_commands%ROWTYPE;
      next_epoch bigint;
      BEGIN
        IF NOT public.paper_payload_valid(p_kind,p_body) OR p_actor IS NULL OR p_actor !~
        '^[A-Za-z0-9._:-]{1,255}$'
          OR p_key IS NULL OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$'
          OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$' THEN RETURN
          jsonb_build_object('error','INVALID_REQUEST'); END IF;
        SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
        SELECT * INTO c FROM public.paper_commands WHERE actor_id=p_actor AND idempotency_key=p_key;
        IF FOUND THEN
          IF c.operation_type<>p_kind OR c.payload<>p_body THEN RETURN
          jsonb_build_object('error','IDEMPOTENCY_CONFLICT'); END IF;
          RETURN public.paper_operation(c.id);
        END IF;
        next_epoch:=s.epoch;
        IF p_kind IN ('PAUSE','STOP','KILL') THEN
          IF s.epoch>=9007199254740991 THEN
            RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
          next_epoch:=s.epoch+1;
          UPDATE public.paper_workflow_state SET epoch=next_epoch,operating_state=CASE
            WHEN p_kind='KILL' OR operating_state='HALTED' THEN 'HALTED'
            WHEN p_kind='STOP' THEN 'STOPPED' ELSE 'PAUSED' END,changed_at=clock_timestamp()
            WHERE singleton;
        END IF;
        INSERT INTO public.paper_commands(actor_id,idempotency_key,operation_type,payload,trace_id,
          accepted_generation,credential_generation,status,order_id)
          VALUES(p_actor,p_key,p_kind,p_body,p_trace,next_epoch,
          coalesce((p_body->>'credentialGeneration')::bigint,s.saved_generation),'ACCEPTED',
          (p_body->>'orderId')::uuid) RETURNING * INTO c;
        INSERT INTO public.paper_command_events(operation_id,event_type,epoch)
          VALUES(c.id,CASE WHEN p_kind IN ('PAUSE','STOP','KILL') THEN 'LATCH_CLOSED' ELSE
          'ACCEPTED' END,next_epoch);
        RETURN public.paper_operation(c.id);
      END;
    """,
        block=True,
    )
    function(
        "paper_claim",
        "p_owner uuid,p_seconds integer",
        "jsonb",
        """
      DECLARE s public.paper_workflow_state%ROWTYPE; c public.paper_commands%ROWTYPE;
      BEGIN
        IF p_owner IS NULL OR p_seconds IS NULL OR p_seconds NOT BETWEEN 1 AND 120
        THEN RAISE EXCEPTION
        'invalid lease'; END IF;
        SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
        -- One account command at a time. No row lock survives this function's transaction.
        IF EXISTS(SELECT 1 FROM public.paper_commands WHERE status='RUNNING' AND
        lease_until>clock_timestamp()) THEN RETURN NULL; END IF;
        SELECT * INTO c FROM public.paper_commands WHERE status IN ('ACCEPTED','RUNNING')
          ORDER BY accepted_at,id FOR UPDATE SKIP LOCKED LIMIT 1;
        IF NOT FOUND THEN RETURN NULL; END IF;
        IF c.operation_type NOT IN ('PAUSE','STOP','KILL') AND
          (c.accepted_generation<>s.epoch OR c.credential_generation<>s.saved_generation
           OR c.credential_generation<>coalesce(s.pending_generation,s.active_generation)) THEN
          UPDATE public.paper_commands SET status='BLOCKED',error_code='VERSION_CONFLICT',
            updated_at=clock_timestamp() WHERE id=c.id;
          INSERT INTO public.paper_command_events(operation_id,event_type,epoch,fence)
            VALUES(c.id,'BLOCKED',s.epoch,c.fence);
          RETURN NULL;
        END IF;
        UPDATE public.paper_commands SET status='RUNNING',owner=p_owner,fence=fence+1,
          lease_until=clock_timestamp()+make_interval(secs=>p_seconds),updated_at=clock_timestamp()
          WHERE id=c.id RETURNING * INTO c;
        INSERT INTO public.paper_command_events(operation_id,event_type,epoch,fence)
          VALUES(c.id,'ATTEMPT_STARTED',s.epoch,c.fence);
        RETURN jsonb_build_object('operationId',c.id,'operationType',c.operation_type,'payload',
        c.payload,
          'owner',c.owner,'fence',c.fence,'epoch',c.accepted_generation,'credentialGeneration',
          c.credential_generation,
          'activeGeneration',s.active_generation,'pendingGeneration',s.pending_generation,
          'pinnedAccountId',s.pinned_account_id,
          'traceId',c.trace_id,'actorId',c.actor_id);
      END;
    """,
        block=True,
    )
    function(
        "paper_verification_valid",
        "p_body jsonb,p_generation bigint",
        "boolean",
        """
      DECLARE k text; stamp timestamptz;
      BEGIN
        IF p_body IS NULL OR jsonb_typeof(p_body)<>'object'
          OR pg_column_size(p_body)>4096
          OR (SELECT count(*) FROM jsonb_object_keys(p_body))<>9
          OR EXISTS(SELECT 1 FROM jsonb_object_keys(p_body) x WHERE x NOT IN
            ('credentialGeneration','observedAt','accountLabel','currency','cash','verifiedAt',
             'marketDataFeed','providerVerification','blockers'))
          OR jsonb_typeof(p_body->'credentialGeneration') IS DISTINCT FROM 'number'
          OR p_body->>'credentialGeneration' IS DISTINCT FROM p_generation::text
          OR jsonb_typeof(p_body->'cash') IS DISTINCT FROM 'string'
          OR p_body->>'cash' IS NULL
          OR p_body->>'cash' !~ '^(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$'
          OR p_body->>'accountLabel' IS NULL
          OR p_body->>'accountLabel' !~ '^paper-[0-9a-f]{8}$'
          OR p_body->>'currency' IS DISTINCT FROM 'USD'
          OR p_body->>'marketDataFeed' IS DISTINCT FROM 'IEX'
          OR p_body->>'providerVerification' IS DISTINCT FROM 'CONFIRMED'
          OR p_body->'blockers' IS DISTINCT FROM '[]'::jsonb THEN RETURN false; END IF;
        FOR k IN SELECT unnest(ARRAY['observedAt','verifiedAt']) LOOP
          IF jsonb_typeof(p_body->k) IS DISTINCT FROM 'string' OR p_body->>k IS NULL
            OR p_body->>k
              !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,9})?Z$'
            OR length(p_body->>k)>30 THEN RETURN false; END IF;
          stamp:=(p_body->>k)::timestamptz;
          IF stamp<clock_timestamp()-interval '120 seconds' OR stamp>clock_timestamp()
          THEN RETURN false; END IF;
        END LOOP;
        RETURN true;
      EXCEPTION WHEN OTHERS THEN RETURN false;
      END;
    """,
        block=True,
    )
    function(
        "paper_finish",
        "p_id uuid,p_owner uuid,p_fence bigint,p_public jsonb,p_private uuid,p_error text",
        "boolean",
        """
      DECLARE s public.paper_workflow_state%ROWTYPE; c public.paper_commands%ROWTYPE; failure text;
      BEGIN
        SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
        SELECT * INTO c FROM public.paper_commands WHERE id=p_id FOR UPDATE;
        IF NOT FOUND OR p_owner IS NULL OR p_fence IS NULL OR p_fence<1
          OR c.status<>'RUNNING' OR c.owner IS NULL OR c.owner<>p_owner OR c.fence<>p_fence
          OR c.lease_until IS NULL OR c.lease_until<=clock_timestamp() THEN RETURN false; END IF;
        failure:=p_error;
        IF c.operation_type NOT IN ('PAUSE','STOP','KILL') THEN
          IF c.accepted_generation<>s.epoch OR c.credential_generation<>s.saved_generation
            OR c.credential_generation<>coalesce(s.pending_generation,s.active_generation) THEN
          failure:='VERSION_CONFLICT'; END IF;
          IF s.operating_state='HALTED' THEN failure:='KILL_SWITCH_ACTIVE'; END IF;
        END IF;
        IF failure IS NULL AND c.operation_type='VERIFY_CONNECTION' THEN
          IF p_private IS NULL
            OR NOT public.paper_verification_valid(p_public,c.credential_generation)
          THEN failure:='CONNECTION_NOT_VERIFIED';
          ELSIF s.pinned_account_id IS NOT NULL AND s.pinned_account_id<>p_private THEN
          failure:='ACCOUNT_IDENTITY_MISMATCH';
          ELSIF EXISTS(SELECT 1 FROM public.broker_orders WHERE status IN ('UNKNOWN','SUBMITTING'))
            OR EXISTS(SELECT 1 FROM public.risk_reservations WHERE state='HELD_UNKNOWN') THEN
            failure:='BROKER_STATUS_UNKNOWN';
          ELSE
            UPDATE public.paper_workflow_state SET pinned_account_id=p_private,
              account_label=p_public->>'accountLabel',account_generation=CASE WHEN
              account_generation=0 THEN 1 ELSE account_generation END,
              active_generation=c.credential_generation,pending_generation=NULL,
              verified_at=(p_public->>'verifiedAt')::timestamptz,cash=(p_public->>'cash')::numeric,
              blocker='OK',changed_at=clock_timestamp() WHERE singleton;
          END IF;
        ELSIF failure IS NULL AND c.operation_type NOT IN ('PAUSE','STOP','KILL') THEN
          failure:='LEDGER_NOT_PREPARED';
        END IF;
        IF failure IS NOT NULL THEN
          IF failure NOT IN ('INVALID_REQUEST','AUTHENTICATION_REQUIRED','ORIGIN_REJECTED',
          'IDEMPOTENCY_CONFLICT','OPERATION_NOT_FOUND','ORDER_NOT_FOUND','VERSION_CONFLICT',
          'CREDENTIALS_MISSING','CREDENTIALS_INVALID','ACCOUNT_IDENTITY_MISMATCH',
          'ACCOUNT_BLOCKED','CURRENCY_UNSUPPORTED','MARKET_DATA_UNAVAILABLE',
          'OPEN_ORDERS_PRESENT','UNSUPPORTED_POSITION','COST_BASIS_MISSING','SNAPSHOT_CHANGED',
          'LEDGER_ALREADY_BOUND','LEDGER_NOT_PREPARED','CONNECTION_NOT_VERIFIED',
          'APPROVAL_EXPIRED','ENTRY_PAUSED','KILL_SWITCH_ACTIVE','SESSION_CLOSED','STALE_QUOTE',
          'CAPABILITY_UNCONFIRMED','BUDGET_EXCEEDED','INSUFFICIENT_POSITION','MINIMUM_QUANTITY',
          'POLICY_MISMATCH','RECONCILIATION_REQUIRED','BROKER_STATUS_UNKNOWN',
          'EXTERNAL_ACTIVITY','PROVIDER_RATE_LIMITED','PROVIDER_UNAVAILABLE',
          'PROVIDER_REJECTED','TEMPORARILY_UNAVAILABLE') THEN
          failure:='TEMPORARILY_UNAVAILABLE'; END IF;
          UPDATE public.paper_workflow_state SET blocker=failure,
            verified_at=CASE WHEN c.operation_type='VERIFY_CONNECTION'
              THEN NULL ELSE verified_at END,
            cash=CASE WHEN c.operation_type='VERIFY_CONNECTION' THEN NULL ELSE cash END,
            changed_at=clock_timestamp() WHERE singleton;
        END IF;
        UPDATE public.paper_commands SET status=CASE WHEN failure IS NULL THEN 'SUCCEEDED' ELSE
        'BLOCKED' END,
          error_code=failure,lease_until=NULL,updated_at=clock_timestamp() WHERE id=p_id;
        INSERT INTO public.paper_command_events(operation_id,event_type,epoch,fence)
          VALUES(p_id,CASE WHEN failure IS NULL THEN 'SUCCEEDED' ELSE 'BLOCKED' END,s.epoch,
          p_fence);
        RETURN true;
      END;
    """,
        block=True,
    )
    # No generic grants or legacy role inheritance. Functions are the only mutation surface.
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_control_api') THEN
          GRANT USAGE ON SCHEMA public TO atl_paper_control_api;
          GRANT EXECUTE ON FUNCTION public.paper_status(text),public.paper_operation(uuid),
            public.paper_rotation_begin(text,text,text),public.paper_rotation_complete(bigint),
            public.paper_enqueue(text,jsonb,text,text,text) TO atl_paper_control_api;
        END IF;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_execution') THEN
          GRANT USAGE ON SCHEMA public TO atl_paper_execution;
          GRANT EXECUTE ON FUNCTION public.paper_claim(uuid,integer),
            public.paper_finish(uuid,uuid,bigint,jsonb,uuid,text) TO atl_paper_execution;
        END IF;
      END $$;
    """)


def function(name, args, result, body, *, block=False):
    """Only fixed migration literals enter this helper."""
    content = body if block else "BEGIN " + body + " END;"
    op.execute(
        f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql "
        f"SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$ {content} $$;"
    )
    types = []
    for arg in args.split(","):
        types.append(arg.strip().split()[-1])
    op.execute(f"REVOKE ALL ON FUNCTION public.{name}({','.join(types)}) FROM PUBLIC")


def downgrade():
    raise RuntimeError("Paper command history and generations are forward-only")
