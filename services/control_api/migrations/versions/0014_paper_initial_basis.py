"""Once-only Paper financial basis and immutable conservative daily evidence."""

from importlib import import_module

from alembic import op

revision = "0014_paper_initial_basis"
down_revision = "0013_paper_commands"
branch_labels = None
depends_on = None


def upgrade():
    function = import_module(
        "services.control_api.migrations.versions.0013_paper_commands"
    ).function
    op.execute("""
    CREATE TABLE public.paper_initial_bases (
      id text UNIQUE NOT NULL,
      account_id varchar(255) PRIMARY KEY REFERENCES public.accounts(id),
      operation_id uuid UNIQUE NOT NULL REFERENCES public.paper_commands(id),
      evidence_hash text NOT NULL CHECK(evidence_hash ~ '^[0-9a-f]{64}$'),
      confirmed_at timestamptz NOT NULL,
      evidence jsonb NOT NULL,
      actor_id varchar(255) NOT NULL,
      trace_id varchar(32) NOT NULL CHECK(trace_id ~ '^[0-9a-f]{32}$')
    );
    CREATE TABLE public.paper_position_cost_bases (
      id text UNIQUE NOT NULL,
      position_id varchar(255) NOT NULL,
      ledger_version integer NOT NULL,
      quantity numeric(38,18) NOT NULL CHECK(quantity>=0),
      cost_basis numeric(38,18) NOT NULL CHECK(cost_basis>=0),
      source_hash text NOT NULL CHECK(source_hash ~ '^[0-9a-f]{64}$'),
      actor_id varchar(255) NOT NULL,trace_id varchar(32) NOT NULL,
      PRIMARY KEY(position_id,ledger_version),
      FOREIGN KEY(position_id,ledger_version)
        REFERENCES public.position_versions(position_id,ledger_version)
    );
    CREATE TABLE public.paper_daily_risk_evidence (
      id text PRIMARY KEY,
      account_id varchar(255) NOT NULL REFERENCES public.accounts(id),
      portfolio_version integer NOT NULL CHECK(portfolio_version>0),
      profile text NOT NULL CHECK(profile='paper-utc-equity-risk.v1'),
      reference_kind text NOT NULL CHECK(reference_kind IN ('UTC_DAY','FIRST_DAY')),
      starts_at timestamptz NOT NULL,expires_at timestamptz NOT NULL,
      opening_lower numeric(38,18) NOT NULL CHECK(opening_lower>0),
      opening_upper numeric(38,18) NOT NULL,
      capital_lower numeric(38,18) NOT NULL CHECK(capital_lower>0),
      external_funding numeric(38,18) NOT NULL,
      observed_at timestamptz NOT NULL,
      current_equity numeric(38,18) NOT NULL CHECK(current_equity>0),
      reference_hash text NOT NULL CHECK(reference_hash ~ '^[0-9a-f]{64}$'),
      evidence_hash text NOT NULL CHECK(evidence_hash ~ '^[0-9a-f]{64}$'),
      account_created_at timestamptz,
      actor_id varchar(255) NOT NULL,trace_id varchar(32) NOT NULL,
      CHECK(opening_lower<=opening_upper AND capital_lower<=opening_lower),
      CHECK(starts_at<=observed_at AND observed_at<expires_at),
      CHECK(expires_at=date_trunc('day',starts_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
            +interval '1 day'),
      CHECK((reference_kind='UTC_DAY' AND account_created_at IS NULL
          AND starts_at=date_trunc('day',starts_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
        OR (reference_kind='FIRST_DAY' AND account_created_at IS NOT NULL
          AND account_created_at>date_trunc('day',starts_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
          AND account_created_at<=starts_at))
    );
    """)
    for table in ("paper_initial_bases", "paper_position_cost_bases", "paper_daily_risk_evidence"):
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE "
                   f"ON public.{table} FOR EACH STATEMENT "
                   "EXECUTE FUNCTION public.ledger_reject_mutation()")
        op.execute(f"CREATE CONSTRAINT TRIGGER {table}_audit AFTER INSERT ON public.{table} "
                   "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                   "EXECUTE FUNCTION public.paper_reconciliation_require_audit()")
    function("paper_initial_amount", "p_value jsonb", "boolean", """
      RETURN p_value IS NOT NULL AND jsonb_typeof(p_value)='string'
        AND (p_value#>>'{}') ~ '^-?(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$';
    """)
    function("paper_initial_valid", "p jsonb", "boolean", """
    DECLARE pos jsonb; d jsonb; a jsonb; k text;
    BEGIN
      IF p IS NULL OR jsonb_typeof(p)<>'object' OR octet_length(p::text)>4194304
        OR NOT p ?& ARRAY['credentialGeneration','providerAccountId','cash','equity','positions',
          'firstReadAt','confirmedAt','providerTime','snapshotHash','evidenceHash','orderAnchors',
          'activityAnchors','daily']
        OR p-ARRAY['credentialGeneration','providerAccountId','cash','equity','positions',
          'firstReadAt','confirmedAt','providerTime','snapshotHash','evidenceHash','orderAnchors',
          'activityAnchors','daily']<>'{}'::jsonb THEN RETURN false; END IF;
      IF jsonb_typeof(p->'credentialGeneration')<>'number'
        OR (p->>'credentialGeneration') !~ '^[1-9][0-9]{0,15}$'
        OR (p->>'credentialGeneration')::numeric>9007199254740991
        OR jsonb_typeof(p->'providerAccountId')<>'string'
        OR p->>'providerAccountId' IS DISTINCT FROM ((p->>'providerAccountId')::uuid)::text
        OR NOT public.paper_initial_amount(p->'cash') OR (p->>'cash')::numeric<0
        OR NOT public.paper_initial_amount(p->'equity') OR (p->>'equity')::numeric<=0
        OR jsonb_typeof(p->'snapshotHash')<>'string'
        OR jsonb_typeof(p->'evidenceHash')<>'string'
        OR p->>'snapshotHash' !~ '^[0-9a-f]{64}$'
        OR p->>'evidenceHash' !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
      FOREACH k IN ARRAY ARRAY['firstReadAt','confirmedAt','providerTime'] LOOP
        IF jsonb_typeof(p->k)<>'string' OR p->>k !~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?Z$'
          OR (p->>k)::timestamptz>clock_timestamp()+interval '5 seconds'
        THEN RETURN false; END IF;
      END LOOP;
      IF (p->>'firstReadAt')::timestamptz>(p->>'confirmedAt')::timestamptz
        OR clock_timestamp()-(p->>'confirmedAt')::timestamptz NOT BETWEEN interval '0 seconds'
          AND interval '119 seconds'
        OR (p->>'confirmedAt')::timestamptz-(p->>'firstReadAt')::timestamptz>=interval '30 seconds'
        OR clock_timestamp()-(p->>'providerTime')::timestamptz>interval '120 seconds'
      THEN RETURN false; END IF;
      IF jsonb_typeof(p->'positions')<>'array' OR jsonb_array_length(p->'positions')>2
        OR jsonb_typeof(p->'orderAnchors')<>'array'
        OR jsonb_array_length(p->'orderAnchors')>10000
        OR jsonb_typeof(p->'activityAnchors')<>'array'
        OR jsonb_array_length(p->'activityAnchors')>2000 THEN RETURN false; END IF;
      FOR pos IN SELECT value FROM jsonb_array_elements(p->'positions') LOOP
        IF jsonb_typeof(pos)<>'object' OR NOT pos ?& ARRAY['symbol','providerAssetId','quantity',
            'costBasis','averageEntryPrice','marketValue']
          OR pos-ARRAY['symbol','providerAssetId','quantity','costBasis','averageEntryPrice',
            'marketValue']<>'{}'::jsonb OR pos->>'symbol' NOT IN ('SPY','QQQ')
          OR pos->>'providerAssetId' IS DISTINCT FROM ((pos->>'providerAssetId')::uuid)::text
        THEN RETURN false; END IF;
        FOREACH k IN ARRAY ARRAY['quantity','costBasis','averageEntryPrice','marketValue'] LOOP
          IF NOT public.paper_initial_amount(pos->k) OR (pos->>k)::numeric<0
          THEN RETURN false; END IF;
        END LOOP;
        IF (pos->>'quantity')::numeric<=0 OR (pos->>'averageEntryPrice')::numeric<=0
          OR (pos->>'marketValue')::numeric<=0 THEN RETURN false; END IF;
      END LOOP;
      IF (SELECT count(DISTINCT value->>'symbol') FROM jsonb_array_elements(p->'positions'))
          <>jsonb_array_length(p->'positions')
        OR (SELECT count(DISTINCT value->>'providerAssetId')
          FROM jsonb_array_elements(p->'positions'))<>jsonb_array_length(p->'positions')
        OR (p->>'cash')::numeric+coalesce((SELECT sum((value->>'marketValue')::numeric)
          FROM jsonb_array_elements(p->'positions')),0)<>(p->>'equity')::numeric
      THEN RETURN false; END IF;
      FOR a IN SELECT value FROM jsonb_array_elements(p->'orderAnchors') LOOP
        IF jsonb_typeof(a)<>'object' OR NOT a ?& ARRAY['id','hash','untraded']
          OR a-ARRAY['id','hash','untraded']<>'{}'::jsonb
          OR jsonb_typeof(a->'untraded')<>'boolean'
          OR jsonb_typeof(a->'id')<>'string' OR jsonb_typeof(a->'hash')<>'string'
          OR a->>'id' IS DISTINCT FROM ((a->>'id')::uuid)::text
          OR a->>'hash' !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
      END LOOP;
      FOR a IN SELECT value FROM jsonb_array_elements(p->'activityAnchors') LOOP
        IF jsonb_typeof(a)<>'object' OR NOT a ?& ARRAY['id','hash','kind']
          OR a-ARRAY['id','hash','kind']<>'{}'::jsonb
          OR jsonb_typeof(a->'id')<>'string' OR jsonb_typeof(a->'hash')<>'string'
          OR length(a->>'id') NOT BETWEEN 1 AND 256
          OR position('::' IN a->>'id')=0 OR a->>'hash' !~ '^[0-9a-f]{64}$'
          OR jsonb_typeof(a->'kind')<>'string' OR a->>'kind' !~ '^[A-Z]{1,32}$'
        THEN RETURN false; END IF;
      END LOOP;
      d:=p->'daily';
      IF jsonb_typeof(d)<>'object' OR NOT d ?& ARRAY['kind','startsAt','expiresAt','openingLower',
          'openingUpper','capitalLower','externalFunding','referenceHash','observationHash',
          'accountCreatedAt'] OR d-ARRAY['kind','startsAt','expiresAt','openingLower',
          'openingUpper','capitalLower','externalFunding','referenceHash','observationHash',
          'accountCreatedAt']<>'{}'::jsonb OR jsonb_typeof(d->'kind')<>'string'
        OR jsonb_typeof(d->'referenceHash')<>'string'
        OR jsonb_typeof(d->'observationHash')<>'string'
        OR d->>'kind' NOT IN ('UTC_DAY','FIRST_DAY')
        OR d->>'referenceHash' !~ '^[0-9a-f]{64}$'
        OR d->>'observationHash' IS DISTINCT FROM p->>'evidenceHash'
      THEN RETURN false; END IF;
      FOREACH k IN ARRAY ARRAY['openingLower','openingUpper','capitalLower','externalFunding'] LOOP
        IF NOT public.paper_initial_amount(d->k) THEN RETURN false; END IF;
      END LOOP;
      FOREACH k IN ARRAY ARRAY['startsAt','expiresAt'] LOOP
        IF jsonb_typeof(d->k)<>'string' OR d->>k !~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?Z$'
        THEN RETURN false; END IF;
      END LOOP;
      IF (d->>'openingLower')::numeric<=0 OR (d->>'capitalLower')::numeric<=0
        OR (d->>'capitalLower')::numeric>(d->>'openingLower')::numeric
        OR (d->>'openingLower')::numeric>(d->>'openingUpper')::numeric
        OR (d->>'startsAt')::timestamptz>(p->>'confirmedAt')::timestamptz
        OR (d->>'expiresAt')::timestamptz<=clock_timestamp()
        OR (d->>'expiresAt')::timestamptz<>
          (date_trunc('day',(d->>'startsAt')::timestamptz AT TIME ZONE 'UTC')
            AT TIME ZONE 'UTC')+interval '1 day' THEN RETURN false; END IF;
      IF d->>'kind'='UTC_DAY' THEN
        IF d->'accountCreatedAt'<>'null'::jsonb OR (d->>'startsAt')::timestamptz<>
          date_trunc('day',(d->>'startsAt')::timestamptz AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
          OR (d->>'openingUpper')::numeric-(d->>'openingLower')::numeric<>0.02
          OR trunc(((d->>'openingLower')::numeric+0.01)*100)<>
            ((d->>'openingLower')::numeric+0.01)*100
        THEN RETURN false; END IF;
      ELSE
        IF jsonb_typeof(d->'accountCreatedAt')<>'string' OR d->>'accountCreatedAt' !~
          '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]+)?Z$'
          OR (d->>'accountCreatedAt')::timestamptz<=
            date_trunc('day',(d->>'startsAt')::timestamptz AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
          OR (d->>'accountCreatedAt')::timestamptz>(d->>'startsAt')::timestamptz
          OR d->>'startsAt' IS DISTINCT FROM p->>'confirmedAt'
          OR (d->>'openingLower')::numeric<>(p->>'cash')::numeric
          OR (d->>'openingUpper')::numeric<>(p->>'cash')::numeric
          OR (d->>'externalFunding')::numeric<>0 OR p->'positions'<>'[]'::jsonb
          OR EXISTS(SELECT 1 FROM jsonb_array_elements(p->'orderAnchors') order_anchor(value)
            WHERE order_anchor.value->'untraded' IS DISTINCT FROM 'true'::jsonb)
          OR EXISTS(SELECT 1 FROM jsonb_array_elements(p->'activityAnchors') activity(value)
            WHERE activity.value->>'kind' NOT IN ('CSD','CSW')) THEN RETURN false; END IF;
      END IF;
      RETURN true;
    EXCEPTION WHEN OTHERS THEN RETURN false;
    END;
    """, block=True)
    function("paper_prepare", "p_id uuid,p_owner uuid,p_fence bigint,p_evidence jsonb", "jsonb", """
    DECLARE s public.paper_workflow_state%ROWTYPE; c public.paper_commands%ROWTYPE;
      pos jsonb; symbol text; seed text; at_time timestamptz; d jsonb; row_id text;
      baseline_positions jsonb:='[]'; old_hash text;
    BEGIN
      IF current_database()<>'lab_paper_runtime'
        OR session_user<>'atl_paper_execution_login' THEN
        RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
      SELECT * INTO s FROM public.paper_workflow_state WHERE singleton FOR UPDATE;
      SELECT * INTO c FROM public.paper_commands WHERE id=p_id FOR UPDATE;
      IF NOT FOUND OR c.operation_type<>'PREPARE_LEDGER' OR p_owner IS NULL
        OR p_fence IS NULL OR p_fence<1 OR c.owner IS DISTINCT FROM p_owner
        OR c.fence IS DISTINCT FROM p_fence THEN
        RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
      IF c.status='SUCCEEDED' AND EXISTS(SELECT 1 FROM public.paper_initial_bases
        WHERE operation_id=c.id) THEN RETURN jsonb_build_object('prepared',true); END IF;
      IF c.status<>'RUNNING' OR c.lease_until IS NULL OR c.lease_until<=clock_timestamp()
        OR s.epoch<>c.accepted_generation OR s.active_generation<>c.credential_generation
        OR s.saved_generation<>s.active_generation OR s.pending_generation IS NOT NULL
        OR s.verified_at IS NULL OR s.operating_state='HALTED' THEN
        RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
      IF NOT public.paper_initial_valid(p_evidence) THEN
        RETURN jsonb_build_object('error','RECONCILIATION_REQUIRED'); END IF;
      IF (p_evidence->>'providerAccountId')::uuid IS DISTINCT FROM s.pinned_account_id
        OR (p_evidence->>'credentialGeneration')::bigint<>s.active_generation THEN
        RETURN jsonb_build_object('error','ACCOUNT_IDENTITY_MISMATCH'); END IF;
      IF (c.payload->>'accountGeneration')::bigint IS DISTINCT FROM s.account_generation
        OR NOT EXISTS(SELECT 1 FROM public.paper_commands v
          WHERE v.id=(c.payload->>'verificationOperationId')::uuid
          AND v.operation_type='VERIFY_CONNECTION' AND v.status='SUCCEEDED'
          AND v.credential_generation=s.active_generation) THEN
        RETURN jsonb_build_object('error','CONNECTION_NOT_VERIFIED'); END IF;
      IF EXISTS(SELECT 1 FROM public.paper_initial_bases) THEN
        RETURN jsonb_build_object('error','LEDGER_ALREADY_BOUND'); END IF;
      IF EXISTS(SELECT 1 FROM public.accounts) OR EXISTS(SELECT 1 FROM public.positions)
        OR EXISTS(SELECT 1 FROM public.portfolio_snapshots)
        OR EXISTS(SELECT 1 FROM public.risk_policies)
        OR EXISTS(SELECT 1 FROM public.broker_account_bindings)
        OR EXISTS(SELECT 1 FROM public.paper_reconciliation_baselines) THEN
        RETURN jsonb_build_object('error','LEDGER_ALREADY_BOUND'); END IF;
      at_time:=(p_evidence->>'confirmedAt')::timestamptz;
      seed:='paper-initial-'||c.id::text;
      d:=p_evidence->'daily';
      INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
        entity_version,trace_id) VALUES
        (seed||'-account-audit',clock_timestamp(),c.actor_id,'paper.initialize',
          '["BROKER_INITIAL_BASIS"]','paper',1,c.trace_id),
        (seed||'-portfolio-audit',clock_timestamp(),c.actor_id,'paper.initialize',
          '["BROKER_INITIAL_BASIS"]',seed||'-portfolio',1,c.trace_id),
        (seed||'-binding-audit',clock_timestamp(),c.actor_id,'paper.initialize',
          '["BROKER_INITIAL_BASIS"]','paper'||':'||'provider-account',1,c.trace_id),
        (seed||'-baseline-audit',clock_timestamp(),c.actor_id,'paper.initialize',
          '["BROKER_INITIAL_BASIS"]',seed||'-baseline',1,c.trace_id),
        (seed||'-basis-audit',clock_timestamp(),c.actor_id,'paper.initialize',
          '["BROKER_INITIAL_BASIS"]',seed,1,c.trace_id),
        (seed||'-daily-audit',clock_timestamp(),c.actor_id,'paper.initialize',
          '["BROKER_INITIAL_BASIS"]',seed||'-daily',1,c.trace_id);
      INSERT INTO public.accounts VALUES('paper','USD',1);
      PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
      INSERT INTO public.broker_account_bindings VALUES('paper',s.pinned_account_id,
        c.actor_id,c.trace_id,clock_timestamp());
      FOREACH symbol IN ARRAY ARRAY['SPY','QQQ'] LOOP
        INSERT INTO public.instruments(id,market,broker_symbol,asset_class,currency,version,
          session_name) VALUES(symbol,'US_EQUITY',symbol,'ETF','USD',1,'XNYS');
        pos:=NULL;
        SELECT value INTO pos FROM jsonb_array_elements(p_evidence->'positions')
          WHERE value->>'symbol'=symbol;
        row_id:='paper-position-'||symbol;
        INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
          entity_version,trace_id) VALUES(seed||'-position-'||symbol,clock_timestamp(),c.actor_id,
          'paper.initialize','["BROKER_INITIAL_BASIS"]',row_id,1,c.trace_id),
          (seed||'-cost-audit-'||symbol,clock_timestamp(),c.actor_id,
          'paper.initialize','["BROKER_INITIAL_BASIS"]',seed||'-cost-'||symbol,1,c.trace_id);
        INSERT INTO public.positions(id,instrument_id,currency,quantity,average_price,
          realized_pnl,ledger_version,account_id) VALUES(row_id,symbol,'USD',
          coalesce((pos->>'quantity')::numeric,0),(pos->>'averageEntryPrice')::numeric,0,1,'paper');
        INSERT INTO public.paper_position_cost_bases VALUES(seed||'-cost-'||symbol,row_id,1,
          coalesce((pos->>'quantity')::numeric,0),coalesce((pos->>'costBasis')::numeric,0),
          p_evidence->>'evidenceHash',c.actor_id,c.trace_id);
        baseline_positions:=baseline_positions||jsonb_build_array(jsonb_build_object(
          'id',row_id,'instrument_id',symbol,'currency','USD',
          'quantity',coalesce(pos->>'quantity','0'),'average_price',pos->>'averageEntryPrice',
          'cost_basis',coalesce(pos->>'costBasis','0'),'realized_pnl','0','ledger_version',1));
        IF pos IS NOT NULL THEN
          INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
            entity_version,trace_id) VALUES(seed||'-asset-audit-'||symbol,clock_timestamp(),
            c.actor_id,'paper.initialize','["BROKER_INITIAL_BASIS"]',
            'paper'||':'||symbol||':'||'provider-asset',1,c.trace_id);
          INSERT INTO public.broker_asset_bindings VALUES('paper',symbol,
            (pos->>'providerAssetId')::uuid,c.actor_id,c.trace_id,clock_timestamp());
        END IF;
      END LOOP;
      INSERT INTO public.portfolio_snapshots VALUES(seed||'-portfolio','USD',
        (p_evidence->>'equity')::numeric,(p_evidence->>'cash')::numeric,
        (p_evidence->>'equity')::numeric-(p_evidence->>'cash')::numeric,
        at_time,1,'VALID','CURRENT');
      INSERT INTO public.paper_reconciliation_baselines VALUES(seed||'-baseline','paper',
        s.pinned_account_id,seed||'-portfolio',1,at_time,NULL,baseline_positions,
        c.actor_id,c.trace_id);
      INSERT INTO public.paper_initial_bases VALUES(seed,'paper',c.id,
        p_evidence->>'evidenceHash',at_time,p_evidence,c.actor_id,c.trace_id);
      INSERT INTO public.paper_daily_risk_evidence VALUES(seed||'-daily','paper',1,
        'paper-utc-equity-risk.v1',d->>'kind',(d->>'startsAt')::timestamptz,
        (d->>'expiresAt')::timestamptz,(d->>'openingLower')::numeric,
        (d->>'openingUpper')::numeric,(d->>'capitalLower')::numeric,
        (d->>'externalFunding')::numeric,at_time,(p_evidence->>'equity')::numeric,
        d->>'referenceHash',d->>'observationHash',(d->>'accountCreatedAt')::timestamptz,
        c.actor_id,c.trace_id);
      INSERT INTO public.audit_events(id,occurred_at,actor_id,action,reason_codes,entity_id,
        entity_version,trace_id) VALUES(seed||'-policy-audit',clock_timestamp(),c.actor_id,
        'risk.policy.initialize','["BROKER_INITIAL_BASIS"]',seed||'-policy',1,c.trace_id);
      INSERT INTO public.risk_policies VALUES(seed||'-policy',1,'USD',1,2,0.10,0.01,
        at_time,c.actor_id,seed||'-policy-audit',true);
      INSERT INTO public.risk_account_context(account_id,version,portfolio_snapshot_id,
        portfolio_version,risk_config_version,day_start_at,day_start_equity,
        day_start_realized_pnl,mode,safety_state,reconciliation_current,valid_until,
        actor_id,trace_id) VALUES('paper',1,seed||'-portfolio',1,'paper-risk-v1',
        (d->>'startsAt')::timestamptz,(d->>'capitalLower')::numeric,0,'DISABLED',
        'PAUSE_NEW',false,least(at_time+interval '120 seconds',
        (d->>'expiresAt')::timestamptz),c.actor_id,c.trace_id);
      UPDATE public.paper_workflow_state SET cash=(p_evidence->>'cash')::numeric,
        blocker='OK',changed_at=clock_timestamp() WHERE singleton;
      UPDATE public.paper_commands SET status='SUCCEEDED',error_code=NULL,lease_until=NULL,
        updated_at=clock_timestamp() WHERE id=c.id;
      INSERT INTO public.paper_command_events(operation_id,event_type,epoch,fence)
        VALUES(c.id,'SUCCEEDED',s.epoch,p_fence);
      RETURN jsonb_build_object('prepared',true);
    END;
    """, block=True)
    function("paper_daily_read", "p_account text,p_version bigint", "jsonb", """
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
        OR p_account IS DISTINCT FROM 'paper' OR p_version IS NULL OR p_version<1
      THEN RETURN NULL; END IF;
      RETURN (SELECT jsonb_build_object('kind',e.reference_kind,
        'startsAt',to_char(e.starts_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'expiresAt',to_char(e.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'openingLower',trim_scale(e.opening_lower)::text,
        'openingUpper',trim_scale(e.opening_upper)::text,
        'capitalLower',trim_scale(e.capital_lower)::text,
        'externalFunding',trim_scale(e.external_funding)::text,
        'currentEquity',trim_scale(e.current_equity)::text,
        'referenceHash',e.reference_hash,'observationHash',e.evidence_hash,
        'observedAt',to_char(e.observed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'accountCreatedAt',CASE WHEN e.account_created_at IS NULL THEN NULL ELSE
          to_char(e.account_created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END)
        FROM public.paper_daily_risk_evidence e
        JOIN public.risk_account_context c ON c.account_id=e.account_id
          AND c.portfolio_version=e.portfolio_version
        JOIN public.portfolio_snapshots p ON p.id=c.portfolio_snapshot_id
          AND p.ledger_version=e.portfolio_version AND p.equity=e.current_equity
        JOIN public.paper_workflow_state s ON s.singleton
          AND s.verified_at IS NOT NULL AND s.pending_generation IS NULL
          AND s.active_generation>0 AND s.saved_generation=s.active_generation
        WHERE e.account_id=p_account AND e.portfolio_version=p_version
        ORDER BY e.observed_at DESC,e.id DESC LIMIT 1);
    """)
    function("paper_initial_history", "p_account text", "jsonb", """
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
        OR p_account IS DISTINCT FROM 'paper' THEN RETURN NULL; END IF;
      RETURN (SELECT jsonb_build_object('providerAccountId',b.evidence->>'providerAccountId',
        'baselineAt',to_char(b.confirmed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'orders',b.evidence->'orderAnchors','activities',b.evidence->'activityAnchors')
        FROM public.paper_initial_bases b WHERE b.account_id=p_account);
    """)
    function("paper_cost_basis_read", "p_position text,p_version integer", "numeric", """
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
        OR p_position IS NULL OR p_version IS NULL OR p_version<1 THEN RETURN NULL; END IF;
      RETURN (SELECT b.cost_basis FROM public.paper_position_cost_bases b
        JOIN public.positions p ON p.id=b.position_id AND p.account_id='paper'
        WHERE b.position_id=p_position AND b.ledger_version=p_version);
    """)
    function("paper_cost_basis_append",
             "p_position text,p_version integer,p_quantity numeric,p_basis numeric,"
             "p_hash text,p_trace text", "boolean", """
    DECLARE row_id text;
    BEGIN
      IF current_database()<>'lab_paper_runtime' OR session_user<>'atl_paper_execution_login'
        OR p_position IS NULL OR p_version IS NULL OR p_version<2 OR p_quantity IS NULL
        OR p_basis IS NULL OR p_hash IS NULL OR p_trace IS NULL
        OR p_hash !~ '^[0-9a-f]{64}$' OR p_trace !~ '^[0-9a-f]{32}$'
        OR p_quantity::text !~ '^(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$'
        OR p_basis::text !~ '^(0|[1-9][0-9]{0,19})(\\.[0-9]{1,18})?$'
        OR (p_quantity=0 AND p_basis<>0) THEN RETURN false; END IF;
      PERFORM id FROM public.accounts WHERE id='paper' FOR UPDATE;
      IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM public.positions p
        WHERE p.id=p_position AND p.account_id='paper' AND p.ledger_version=p_version
          AND p.quantity=p_quantity) THEN RETURN false; END IF;
      IF EXISTS(SELECT 1 FROM public.paper_position_cost_bases
        WHERE position_id=p_position AND ledger_version=p_version) THEN
        RETURN EXISTS(SELECT 1 FROM public.paper_position_cost_bases WHERE position_id=p_position
          AND ledger_version=p_version AND quantity=p_quantity AND cost_basis=p_basis
          AND source_hash=p_hash); END IF;
      row_id:=p_position||':'||'cost'||':'||p_version::text;
      IF NOT EXISTS(SELECT 1 FROM public.audit_events WHERE entity_id=row_id
        AND entity_version=1 AND actor_id='execution-reconciliation' AND trace_id=p_trace
        AND transaction_id=txid_current()) THEN RETURN false; END IF;
      INSERT INTO public.paper_position_cost_bases VALUES(row_id,p_position,p_version,
        p_quantity,p_basis,p_hash,'execution-reconciliation',p_trace);
      RETURN true;
    END;
    """, block=True)
    # Reuse the complete canonical projection; only the setup discriminator changes.
    op.execute("""
    DO $$ DECLARE definition text; BEGIN
      SELECT pg_get_functiondef('public.paper_status(text)'::regprocedure) INTO definition;
      IF position('WHEN verified_at IS NOT NULL THEN ''VERIFIED''' IN definition)=0 THEN
        RAISE EXCEPTION 'unexpected Paper status definition'; END IF;
      definition:=replace(definition,'WHEN verified_at IS NOT NULL THEN ''VERIFIED''',
        'WHEN verified_at IS NOT NULL AND EXISTS(SELECT 1 FROM public.paper_initial_bases) '
        'THEN ''PREPARED'' WHEN verified_at IS NOT NULL THEN ''VERIFIED''');
      EXECUTE definition;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='atl_paper_execution') THEN
        GRANT EXECUTE ON FUNCTION public.paper_prepare(uuid,uuid,bigint,jsonb)
          TO atl_paper_execution;
        GRANT EXECUTE ON FUNCTION public.paper_daily_read(text,bigint) TO atl_paper_execution;
        GRANT EXECUTE ON FUNCTION public.paper_initial_history(text) TO atl_paper_execution;
        GRANT EXECUTE ON FUNCTION public.safety_latch(varchar,varchar,varchar,varchar,varchar)
          TO atl_paper_execution;
        GRANT SELECT ON public.accounts,public.instruments,public.positions,
          public.portfolio_snapshots,public.risk_account_context,public.risk_decisions,
          public.risk_reservations,public.order_intents,public.broker_orders,
          public.execution_reports,public.audit_events,public.paper_reconciliation_baselines,
          public.paper_order_accounting_bases,public.paper_activity_evidence,
          public.outbox_events,public.execution_owners,public.broker_asset_bindings,
          public.broker_account_bindings,public.broker_binding_proofs TO atl_paper_execution;
        GRANT INSERT ON public.audit_events,public.portfolio_snapshots,
          public.paper_activity_evidence,public.paper_reconciliation_receipts,
          public.paper_order_accounting_bases,public.execution_reports,public.outbox_events,
          public.execution_owners TO atl_paper_execution;
        GRANT UPDATE(ledger_version) ON public.accounts TO atl_paper_execution;
        GRANT UPDATE(version,safety_state,reconciliation_current,portfolio_snapshot_id,
          portfolio_version,actor_id,trace_id) ON public.risk_account_context
          TO atl_paper_execution;
        GRANT UPDATE(owner_id,fencing_token,claimed_at,heartbeat_at,trace_id)
          ON public.execution_owners TO atl_paper_execution;
        GRANT UPDATE(instrument_id,currency,quantity,average_price,realized_pnl,ledger_version)
          ON public.positions TO atl_paper_execution;
        GRANT UPDATE(proposal_id,risk_decision_id,currency,reserved_notional,state,version,
          created_at,trace_id,released_at) ON public.risk_reservations TO atl_paper_execution;
        GRANT UPDATE(intent_id,client_order_id,broker_order_id,status,requested_quantity,
          filled_quantity,version,updated_at,trace_id) ON public.broker_orders
          TO atl_paper_execution;
        GRANT UPDATE(event_id) ON public.outbox_events TO atl_paper_execution;
        GRANT EXECUTE ON FUNCTION public.paper_cost_basis_read(text,integer),
          public.paper_cost_basis_append(text,integer,numeric,numeric,text,text)
          TO atl_paper_execution;
      END IF;
    END $$;
    """)


def downgrade():
    raise RuntimeError("initial Paper financial provenance is forward-only")
