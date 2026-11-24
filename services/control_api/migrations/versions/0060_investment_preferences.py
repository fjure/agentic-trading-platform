"""Independent actor-scoped display preferences and immutable retry receipts."""

from alembic import op

revision = "0060_investment_preferences"
down_revision = "0059_provider_status_time"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE SCHEMA investment_experience;
    REVOKE ALL ON SCHEMA investment_experience FROM PUBLIC;
    GRANT USAGE ON SCHEMA investment_experience TO atl_control_api;
    CREATE TABLE investment_experience.preferences (
      actor text PRIMARY KEY CHECK (actor ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
      version bigint NOT NULL CHECK (version > 0),
      document jsonb NOT NULL CHECK (jsonb_typeof(document)='object')
    );
    CREATE TABLE investment_experience.preference_receipts (
      actor text NOT NULL,
      key_hash text NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
      request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
      response_body jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(actor,key_hash)
    );
    REVOKE ALL ON ALL TABLES IN SCHEMA investment_experience FROM PUBLIC;
    CREATE TRIGGER preference_receipts_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
      ON investment_experience.preference_receipts FOR EACH STATEMENT
      EXECUTE FUNCTION public.ledger_reject_mutation();

    CREATE FUNCTION investment_experience.default_preferences() RETURNS jsonb
    LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$
      SELECT jsonb_build_object('schemaVersion','1','version',0,'updatedAt',NULL,
        'experienceLevel','UNSPECIFIED','goals','[]'::jsonb,'horizon','UNSPECIFIED',
        'lossTolerance','UNSPECIFIED',
        'contextReference',jsonb_build_object('context','FAKE','namespace','fake'),
        'onboardingSteps',(SELECT jsonb_agg(jsonb_build_object(
          'step',s,'state','UNSEEN','updatedAt',NULL) ORDER BY n)
          FROM unnest(ARRAY['EXPERIENCE','GOALS','HORIZON','LOSS_TOLERANCE','CONTEXT'])
            WITH ORDINALITY AS steps(s,n)))
    $$;
    CREATE FUNCTION investment_experience.valid_preferences(p jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog AS $$
    DECLARE c jsonb; item jsonb; seen text[] := ARRAY[]::text[];
    BEGIN
      IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR
         (SELECT count(*) FROM jsonb_object_keys(p))<>6 OR
         NOT (p ?& ARRAY['experienceLevel','goals','horizon','lossTolerance',
                        'contextReference','onboardingSteps']) OR
         p->>'experienceLevel' NOT IN ('UNSPECIFIED','BEGINNER','EXPERIENCED') OR
         p->>'horizon' NOT IN ('UNSPECIFIED','SHORT','MEDIUM','LONG') OR
         p->>'lossTolerance' NOT IN ('UNSPECIFIED','LOW','MEDIUM','HIGH') OR
         jsonb_typeof(p->'goals') IS DISTINCT FROM 'array' OR
         jsonb_array_length(p->'goals')>3 OR
         jsonb_typeof(p->'onboardingSteps') IS DISTINCT FROM 'array' OR
         jsonb_array_length(p->'onboardingSteps')>5 THEN RETURN false; END IF;
      IF p->'experienceLevel'='null'::jsonb OR p->'horizon'='null'::jsonb OR
         p->'lossTolerance'='null'::jsonb THEN RETURN false; END IF;
      FOR item IN SELECT value FROM jsonb_array_elements(p->'goals') LOOP
        IF jsonb_typeof(item)<>'string' OR item#>>'{}' NOT IN
           ('LEARN','UNDERSTAND_PORTFOLIO','RESEARCH_STRATEGIES') OR
           item#>>'{}'=ANY(seen) THEN RETURN false; END IF;
        seen:=array_append(seen,item#>>'{}');
      END LOOP;
      c:=p->'contextReference';
      IF jsonb_typeof(c) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
      IF c->>'context'='DEMO' THEN
        IF (SELECT count(*) FROM jsonb_object_keys(c))<>3 OR
           NOT(c ?& ARRAY['context','namespace','demoRunId']) OR
           c->>'namespace' IS DISTINCT FROM 'demo' OR jsonb_typeof(c->'demoRunId')<>'string' OR
           NOT(c->>'demoRunId' ~ '^[A-Za-z0-9._:-]{1,128}$')
           THEN RETURN false; END IF;
      ELSIF c NOT IN ('{"context":"FAKE","namespace":"fake"}'::jsonb,
                     '{"context":"ALPACA_PAPER","namespace":"alpaca-paper"}'::jsonb)
        THEN RETURN false;
      END IF;
      seen:=ARRAY[]::text[];
      FOR item IN SELECT value FROM jsonb_array_elements(p->'onboardingSteps') LOOP
        IF jsonb_typeof(item)<>'object' OR
           (SELECT count(*) FROM jsonb_object_keys(item))<>3 OR
           NOT(item ?& ARRAY['step','state','updatedAt']) OR
           item->>'step' IS NULL OR item->>'state' IS NULL OR
           item->>'step' NOT IN ('EXPERIENCE','GOALS','HORIZON','LOSS_TOLERANCE','CONTEXT') OR
           item->>'step'=ANY(seen) OR
           item->>'state' NOT IN ('COMPLETED','SKIPPED','UNSEEN')
           THEN RETURN false; END IF;
        seen:=array_append(seen,item->>'step');
      END LOOP;
      RETURN true;
    EXCEPTION WHEN OTHERS THEN RETURN false;
    END $$;
    CREATE FUNCTION investment_experience.read_preferences(p_actor text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    BEGIN
      IF p_actor IS NULL OR NOT(p_actor ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$') THEN
        RAISE EXCEPTION 'invalid actor';
      END IF;
      RETURN COALESCE((SELECT document FROM investment_experience.preferences
        WHERE actor=p_actor), investment_experience.default_preferences());
    END $$;
    CREATE FUNCTION investment_experience.read_preference_receipt(
      p_actor text,p_key text,p_hash text) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
    DECLARE receipt investment_experience.preference_receipts%ROWTYPE;
    BEGIN
      IF p_actor IS NULL OR NOT(p_actor ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$') OR
         p_key IS NULL OR NOT(p_key ~ '^[0-9a-f]{64}$') OR
         p_hash IS NULL OR NOT(p_hash ~ '^[0-9a-f]{64}$') THEN
        RAISE EXCEPTION 'invalid receipt request'; END IF;
      SELECT * INTO receipt FROM investment_experience.preference_receipts
        WHERE actor=p_actor AND key_hash=p_key;
      IF NOT FOUND THEN RETURN NULL; END IF;
      IF receipt.request_hash<>p_hash THEN
        RETURN jsonb_build_object('error','IDEMPOTENCY_CONFLICT'); END IF;
      RETURN receipt.response_body;
    END $$;
    CREATE FUNCTION investment_experience.update_preferences(
      p_actor text,p_key text,p_hash text,p_expected bigint,p_reset boolean,p_document jsonb)
      RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    DECLARE prior jsonb; receipt investment_experience.preference_receipts%ROWTYPE;
      next_doc jsonb; normalized jsonb; step_name text; state_name text; field_name text;
      prior_step jsonb; now_stamp text;
    BEGIN
      IF p_actor IS NULL OR NOT(p_actor ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$') OR
         p_key IS NULL OR NOT(p_key ~ '^[0-9a-f]{64}$') OR
         p_hash IS NULL OR NOT(p_hash ~ '^[0-9a-f]{64}$') OR
         p_expected IS NULL OR p_expected<0 OR p_expected>=9223372036854775807 OR
         p_reset IS NULL OR NOT investment_experience.valid_preferences(p_document)
         THEN RETURN jsonb_build_object('error','INVALID_REQUEST'); END IF;
      PERFORM pg_advisory_xact_lock(hashtextextended('investment.preferences:'||p_actor,0));
      SELECT * INTO receipt FROM investment_experience.preference_receipts
        WHERE actor=p_actor AND key_hash=p_key;
      IF FOUND THEN
        IF receipt.request_hash<>p_hash THEN
          RETURN jsonb_build_object('error','IDEMPOTENCY_CONFLICT'); END IF;
        RETURN receipt.response_body;
      END IF;
      prior:=investment_experience.read_preferences(p_actor);
      IF (prior->>'version')::bigint<>p_expected THEN
        RETURN jsonb_build_object('error','VERSION_CONFLICT'); END IF;
      now_stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
      IF p_reset THEN
        next_doc:=investment_experience.default_preferences();
      ELSE
        normalized:='[]'::jsonb;
        FOREACH step_name IN ARRAY
          ARRAY['EXPERIENCE','GOALS','HORIZON','LOSS_TOLERANCE','CONTEXT'] LOOP
          field_name:=CASE step_name WHEN 'EXPERIENCE' THEN 'experienceLevel'
            WHEN 'GOALS' THEN 'goals' WHEN 'HORIZON' THEN 'horizon'
            WHEN 'LOSS_TOLERANCE' THEN 'lossTolerance' ELSE 'contextReference' END;
          SELECT value->>'state' INTO state_name
            FROM jsonb_array_elements(p_document->'onboardingSteps')
            WHERE value->>'step'=step_name;
          state_name:=COALESCE(state_name,'UNSEEN');
          SELECT value INTO prior_step FROM jsonb_array_elements(prior->'onboardingSteps')
            WHERE value->>'step'=step_name;
          normalized:=normalized||jsonb_build_array(jsonb_build_object('step',step_name,
            'state',state_name,'updatedAt',CASE WHEN state_name='UNSEEN' THEN NULL
              WHEN prior_step->>'state'=state_name AND
                prior->field_name=p_document->field_name THEN prior_step->>'updatedAt'
              ELSE now_stamp END));
        END LOOP;
        next_doc:=p_document||jsonb_build_object('onboardingSteps',normalized);
      END IF;
      next_doc:=next_doc||jsonb_build_object('schemaVersion','1',
        'version',p_expected+1,'updatedAt',now_stamp);
      INSERT INTO investment_experience.preferences(actor,version,document)
        VALUES(p_actor,p_expected+1,next_doc) ON CONFLICT(actor) DO UPDATE
        SET version=EXCLUDED.version,document=EXCLUDED.document;
      INSERT INTO investment_experience.preference_receipts
        (actor,key_hash,request_hash,response_body)
        VALUES(p_actor,p_key,p_hash,next_doc);
      RETURN next_doc;
    END $$;
    REVOKE ALL ON ALL FUNCTIONS IN SCHEMA investment_experience FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION investment_experience.read_preferences(text),
      investment_experience.read_preference_receipt(text,text,text),
      investment_experience.update_preferences(text,text,text,bigint,boolean,jsonb)
      TO atl_control_api;
    """)


def downgrade():
    raise RuntimeError("Preferences and immutable receipts require an explicit retention decision")
