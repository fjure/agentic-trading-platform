"""Account-locked USD cap and versioned strategy with narrow audited API mutation."""

from alembic import op

revision = "0012_trading_profile"
down_revision = "0011_paper_smoke_authority"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE public.trading_profiles (
          account_id varchar(255) PRIMARY KEY REFERENCES public.accounts(id) ON DELETE RESTRICT,
          budget numeric(38,18) NOT NULL CHECK (budget>0 AND budget<=1000000),
          currency varchar(3) NOT NULL CHECK (currency='USD'),
          strategy varchar(40) NOT NULL CHECK (strategy IN ('mock-neutral','neutral-baseline')),
          strategy_version varchar(60) NOT NULL CHECK (strategy_version=strategy||'.v1'),
          version integer NOT NULL CHECK (version>0),
          changed_at timestamptz NOT NULL,
          actor_id varchar(255) NOT NULL,
          audit_event_id varchar(255) NOT NULL REFERENCES public.audit_events(id)
        );
        REVOKE ALL ON public.trading_profiles FROM PUBLIC;
        GRANT SELECT ON public.trading_profiles TO atl_control_api,atl_worker,atl_execution;
        CREATE TABLE public.trading_profile_receipts (
          actor_id varchar(255) NOT NULL,
          idempotency_key varchar(128) NOT NULL,
          account_id varchar(255) NOT NULL REFERENCES public.accounts(id),
          budget numeric(38,18) NOT NULL,
          strategy varchar(40) NOT NULL,
          response_body jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (actor_id,idempotency_key)
        );
        REVOKE ALL ON public.trading_profile_receipts FROM PUBLIC;
        GRANT SELECT ON public.trading_profile_receipts TO atl_control_api;
        CREATE TRIGGER trading_profile_receipts_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
          ON public.trading_profile_receipts FOR EACH STATEMENT
          EXECUTE FUNCTION public.ledger_reject_mutation();
    """)
    op.execute("""
        CREATE FUNCTION public.control_update_trading_profile(
          p_account varchar,p_actor varchar,p_key varchar,p_trace varchar,
          p_budget numeric,p_strategy varchar
        ) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,public,pg_temp AS $$
        DECLARE rec public.trading_profile_receipts%ROWTYPE;
                pos record; gross numeric:=0; reserved numeric:=0;
                mark numeric; mark_at timestamptz; now_at timestamptz;
                next_version integer; audit_id varchar; result jsonb; pv integer;
        BEGIN
          IF p_budget IS NULL OR p_budget<=0 OR p_budget>1000000
             OR p_budget<>trunc(p_budget,18)
             OR p_strategy IS NULL OR p_strategy NOT IN ('mock-neutral','neutral-baseline')
             OR p_actor IS NULL OR length(p_actor)=0 OR length(p_actor)>255
             OR p_actor ~ '[[:cntrl:]]'
             OR p_key IS NULL OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$'
             OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$' THEN
            RAISE EXCEPTION 'invalid trading profile';
          END IF;
          PERFORM id FROM public.accounts WHERE id=p_account AND currency='USD' FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'account unavailable'; END IF;
          SELECT * INTO rec FROM public.trading_profile_receipts
            WHERE actor_id=p_actor AND idempotency_key=p_key;
          IF FOUND THEN
            IF rec.account_id<>p_account OR rec.budget<>p_budget OR rec.strategy<>p_strategy THEN
              RETURN jsonb_build_object('error','IDEMPOTENCY_CONFLICT');
            END IF;
            RETURN rec.response_body;
          END IF;
          now_at:=clock_timestamp();
          IF EXISTS(SELECT 1 FROM public.positions WHERE account_id IS NULL)
             OR EXISTS(SELECT 1 FROM public.risk_reservations WHERE account_id IS NULL)
             OR EXISTS(SELECT 1 FROM public.order_intents WHERE account_id IS NULL)
             OR EXISTS(SELECT 1 FROM public.risk_reservations
               WHERE account_id=p_account AND state='HELD_UNKNOWN')
             OR EXISTS(SELECT 1 FROM public.broker_orders b
               JOIN public.order_intents i ON i.id=b.intent_id
               WHERE i.account_id=p_account AND b.status IN ('UNKNOWN','SUBMITTING')) THEN
            RETURN jsonb_build_object('error','RISK_LIMIT_EXCEEDED');
          END IF;
          -- Reject unbounded/orphan financial authority instead of assuming zero.
          IF EXISTS(SELECT 1 FROM public.order_intents i
            LEFT JOIN public.risk_reservations r ON r.proposal_id=i.proposal_id
            LEFT JOIN public.broker_orders b ON b.intent_id=i.id
            WHERE i.account_id=p_account AND (r.id IS NULL OR
              (b.status IN ('CREATED','RISK_RESERVED','SUBMITTING','ACKNOWLEDGED',
                'PARTIALLY_FILLED','CANCEL_PENDING','UNKNOWN')
               AND r.state NOT IN ('ACTIVE','HELD_UNKNOWN')))) THEN
            RETURN jsonb_build_object('error','RISK_LIMIT_EXCEEDED');
          END IF;
          FOR pos IN SELECT * FROM public.positions
            WHERE account_id=p_account ORDER BY id FOR UPDATE LOOP
            IF pos.currency<>'USD' OR pos.quantity<0 THEN
              RETURN jsonb_build_object('error','RISK_LIMIT_EXCEEDED');
            END IF;
            IF pos.quantity=0 THEN CONTINUE; END IF;
            SELECT b.close,ms.available_at INTO mark,mark_at
              FROM public.market_states ms JOIN public.market_bars b ON b.id=ms.bar_id
              WHERE ms.instrument_id=pos.instrument_id AND ms.quality='VALID'
              ORDER BY ms.as_of DESC,ms.id DESC LIMIT 1;
            IF mark IS NULL OR mark<=0 OR mark_at>now_at
               OR mark_at<now_at-interval '120 seconds' THEN
              RETURN jsonb_build_object('error','RISK_LIMIT_EXCEEDED');
            END IF;
            gross:=gross+pos.quantity*mark;
          END LOOP;
          IF EXISTS(SELECT 1 FROM public.risk_reservations
            WHERE account_id=p_account AND state IN ('ACTIVE','HELD_UNKNOWN') AND currency<>'USD')
          THEN RETURN jsonb_build_object('error','RISK_LIMIT_EXCEEDED'); END IF;
          SELECT coalesce(sum(r.reserved_notional),0) INTO reserved
            FROM public.risk_reservations r
            JOIN public.trade_proposals p ON p.id=r.proposal_id
            LEFT JOIN public.order_intents i ON i.proposal_id=p.id
            LEFT JOIN public.broker_orders b ON b.intent_id=i.id
            WHERE r.account_id=p_account AND r.state IN ('ACTIVE','HELD_UNKNOWN')
              AND p.target='LONG' AND (b.status IS NULL OR b.status IN
                ('CREATED','RISK_RESERVED','SUBMITTING','ACKNOWLEDGED',
                 'PARTIALLY_FILLED','CANCEL_PENDING','UNKNOWN'));
          IF gross+reserved*1.01>p_budget THEN
            RETURN jsonb_build_object('error','RISK_LIMIT_EXCEEDED');
          END IF;
          SELECT coalesce(max(version),0)+1 INTO next_version FROM public.trading_profiles
            WHERE account_id=p_account;
          SELECT version INTO pv FROM public.risk_policies ORDER BY version DESC LIMIT 1;
          audit_id:=gen_random_uuid()::text;
          INSERT INTO public.audit_events
            (id,occurred_at,actor_id,action,reason_codes,entity_id,
             entity_version,trace_id,policy_version,outcome)
            VALUES(audit_id,now_at,p_actor,'control.trading_profile_updated',
              jsonb_build_array('OK'),'trading-profile:'||p_account,next_version,p_trace,pv,'UPDATED');
          INSERT INTO public.trading_profiles
            (account_id,budget,currency,strategy,strategy_version,version,
             changed_at,actor_id,audit_event_id)
            VALUES(p_account,p_budget,'USD',p_strategy,p_strategy||'.v1',next_version,
              now_at,p_actor,audit_id)
            ON CONFLICT(account_id) DO UPDATE SET budget=excluded.budget,
              strategy=excluded.strategy,strategy_version=excluded.strategy_version,
              version=excluded.version,changed_at=excluded.changed_at,
              actor_id=excluded.actor_id,audit_event_id=excluded.audit_event_id;
          result:=jsonb_build_object('budget',trim_scale(p_budget)::text,
            'currency','USD','strategy',p_strategy);
          INSERT INTO public.trading_profile_receipts
            (actor_id,idempotency_key,account_id,budget,strategy,response_body)
            VALUES(p_actor,p_key,p_account,p_budget,p_strategy,result);
          RETURN result;
        END $$;
        REVOKE ALL ON FUNCTION public.control_update_trading_profile
          (varchar,varchar,varchar,varchar,numeric,varchar) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION public.control_update_trading_profile
          (varchar,varchar,varchar,varchar,numeric,varchar) TO atl_control_api;
    """)


def downgrade():
    raise RuntimeError("Audited trading profiles and receipts are forward-only")
