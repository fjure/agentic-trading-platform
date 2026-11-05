"""QSL-03: immutable research bindings and the existing global admission barrier.

No bars, financial ledger, provider rights or execution capabilities are copied.
"""

from alembic import op
from sqlalchemy import text

revision = "0035_quant_lab"
down_revision = "0034_retrospective_gate"
branch_labels = None
depends_on = None


def upgrade():
    _tables()
    _exposures()
    _admission()
    _worker()
    _current_gate()
    _selection_guards()
    _reliability()
    _content()
    _permissions()


def _tables():
    op.execute("""
    CREATE FUNCTION backtest.quant_canonical(p_value jsonb) RETURNS text LANGUAGE plpgsql
      IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE result text;ch text;code integer;
    BEGIN
      CASE jsonb_typeof(p_value)
      WHEN 'object' THEN SELECT '{'||coalesce(string_agg(backtest.quant_canonical(to_jsonb(key))||
        ':'||backtest.quant_canonical(value),',' ORDER BY key COLLATE "C"),'')||'}'
        INTO result FROM jsonb_each(p_value);
      WHEN 'array' THEN SELECT '['||coalesce(string_agg(backtest.quant_canonical(value),','
        ORDER BY ord),'')||']' INTO result FROM jsonb_array_elements(p_value)
        WITH ORDINALITY AS item(value,ord);
      WHEN 'string' THEN
        result:='';
        FOR ch IN SELECT regexp_split_to_table(p_value::text,'') LOOP
          code:=ascii(ch);
          IF code<=127 THEN result:=result||ch;
          ELSIF code<=65535 THEN result:=result||chr(92)||'u'||lpad(to_hex(code),4,'0');
          ELSE code:=code-65536;result:=result||chr(92)||'u'||to_hex(55296+(code/1024))||
            chr(92)||'u'||to_hex(56320+(code%1024)); END IF;
        END LOOP;
      ELSE result:=p_value::text;
      END CASE;
      RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION backtest.quant_canonical(jsonb) FROM PUBLIC;
    CREATE FUNCTION backtest.quant_candidate_preimage(p_body jsonb) RETURNS jsonb LANGUAGE sql
      IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
      SELECT jsonb_build_array(p_body->'strategyId',p_body->'version',coalesce(
        (SELECT jsonb_agg(jsonb_build_array(key,value) ORDER BY key)
          FROM jsonb_each(p_body->'parameters')),'[]'::jsonb)) $$;
    REVOKE ALL ON FUNCTION backtest.quant_candidate_preimage(jsonb) FROM PUBLIC;
    CREATE FUNCTION backtest.quant_grid(p_spaces jsonb) RETURNS TABLE(candidate_id text)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE space jsonb;params jsonb;pair jsonb;size integer;total integer:=0;keys_count integer;
      count_values integer;generated text[]:='{}'::text[];candidate jsonb;computed text;
    BEGIN
      IF jsonb_typeof(p_spaces) IS DISTINCT FROM 'array' OR jsonb_array_length(p_spaces)
        NOT BETWEEN 1 AND 32 THEN RAISE EXCEPTION 'invalid quant grid'; END IF;
      FOR space IN SELECT value FROM jsonb_array_elements(p_spaces) LOOP
        params:=space->'parameter_values';
        keys_count:=jsonb_array_length(params);size:=1;
        IF keys_count>3 OR space->>'strategy_version' IS DISTINCT FROM '1' THEN
          RAISE EXCEPTION 'invalid quant grid'; END IF;
        IF (SELECT count(DISTINCT item->>0) FROM jsonb_array_elements(params)item)<>keys_count THEN
          RAISE EXCEPTION 'invalid quant grid'; END IF;
        FOR pair IN SELECT value FROM jsonb_array_elements(params) LOOP
          count_values:=jsonb_array_length(pair->1);
          IF count_values NOT BETWEEN 1 AND 32 OR (SELECT count(DISTINCT item)
            FROM jsonb_array_elements(pair->1)item)<>count_values THEN
              RAISE EXCEPTION 'invalid quant grid'; END IF;
          size:=least(33,size*count_values);
        END LOOP;
        total:=total+size;
        IF total>32 THEN RAISE EXCEPTION 'invalid quant grid'; END IF;
        FOR candidate IN WITH RECURSIVE product(index,configuration) AS (
          SELECT 0,'{}'::jsonb UNION ALL SELECT index+1,configuration||jsonb_build_object(
            params->index->>0,item.value) FROM product CROSS JOIN LATERAL
            jsonb_array_elements(params->index->1) item WHERE index<keys_count)
          SELECT jsonb_build_object('strategyId',space->>'strategy_id','version',
            space->>'strategy_version','parameters',configuration) FROM product WHERE
          index=keys_count LOOP
          computed:=encode(sha256(convert_to(backtest.quant_canonical(
            backtest.quant_candidate_preimage(candidate)),'UTF8')),'hex');
          IF computed=ANY(generated) THEN RAISE EXCEPTION 'invalid quant grid'; END IF;
          generated:=array_append(generated,computed);candidate_id:=computed;RETURN NEXT;
        END LOOP;
      END LOOP;
    END $$;
    REVOKE ALL ON FUNCTION backtest.quant_grid(jsonb) FROM PUBLIC;
    CREATE TABLE backtest.quant_strategy_revisions (
      strategy_id text NOT NULL,version text NOT NULL CHECK(version='1'),
      implementation_fingerprint text NOT NULL CHECK(implementation_fingerprint ~ '^[0-9a-f]{64}$'),
      execution_available boolean NOT NULL,
      PRIMARY KEY(strategy_id,version,implementation_fingerprint));
    INSERT INTO backtest.quant_strategy_revisions VALUES
      ('flat-v1','1','4896249c4ce4c42ef9e1c5fc74831967c3f25cb5714e7f2bfbc25c5170273b8c',true),
      ('buy-hold-v1','1','f5481d0c87c874bb575e0d75e397368b743d8849de6bfe761de8637059e1c6a4',true),
      ('sma-cross-v1','1','9b6b255650ea354af41f75d8c155abfd322f52aba3c0fa14c030fa70f08b48cf',true),
      ('ema-cross-v1','1','562222b1e3378f07ba32b4f5fb163d79c55617d4747a8e38bb8d44e57f616040',true),
      ('rsi-mean-reversion-v1','1','24434d1bb99ebc451c2521f01320a6e83ff13587c14bbb1ce3ee4b871c3741c2',true);
    CREATE TABLE backtest.experiment_definitions (
      definition_id uuid PRIMARY KEY, definition_version integer NOT NULL
          CHECK(definition_version>0),
      predecessor_id uuid REFERENCES backtest.experiment_definitions,
      supersedes_batch_id uuid, actor text NOT NULL CHECK(actor='local-operator'),
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      policy_hash text NOT NULL CHECK(policy_hash ~ '^[0-9a-f]{64}$'),policy_canonical text NOT
          NULL,
      candidate_count integer NOT NULL CHECK(candidate_count BETWEEN 1 AND 32),
      body jsonb NOT NULL, canonical_text text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK(octet_length(canonical_text)<=12582912),
      CHECK(policy_hash=encode(sha256(convert_to(policy_canonical,'UTF8')),'hex')),
      CHECK(fingerprint=encode(sha256(convert_to(canonical_text,'UTF8')),'hex')));
    CREATE TABLE backtest.experiment_validations (
      definition_id uuid PRIMARY KEY REFERENCES backtest.experiment_definitions,
      validation_id uuid NOT NULL UNIQUE,
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      body jsonb NOT NULL, canonical_text text NOT NULL,
      CHECK(body=canonical_text::jsonb AND octet_length(canonical_text)<=12582912),
      CHECK(fingerprint=encode(sha256(convert_to(canonical_text,'UTF8')),'hex')));
    CREATE TABLE backtest.experiment_candidates (
      definition_id uuid NOT NULL REFERENCES backtest.experiment_definitions,
      candidate_id text NOT NULL CHECK(candidate_id ~ '^[0-9a-f]{64}$'),
      role text NOT NULL CHECK(role IN ('SEARCH','FLAT_REFERENCE','BUY_HOLD_REFERENCE')),
      body jsonb NOT NULL, canonical_text text NOT NULL,
      PRIMARY KEY(definition_id,candidate_id,role),
      CHECK(octet_length(canonical_text)<=12582912),
      CHECK(canonical_text::jsonb=backtest.quant_candidate_preimage(body)),
      CHECK(candidate_id=encode(sha256(convert_to(canonical_text,'UTF8')),'hex')));
    CREATE TABLE backtest.experiment_batches (
      batch_id uuid PRIMARY KEY,definition_id uuid NOT NULL REFERENCES
          backtest.experiment_definitions,
      validation_id uuid NOT NULL REFERENCES backtest.experiment_validations(validation_id),
      actor text NOT NULL CHECK(actor='local-operator'),idempotency_key text NOT NULL,
      code_version text NOT NULL CHECK(code_version ~ '^[0-9a-f]{40}$'),
      trace_id text NOT NULL CHECK(trace_id ~ '^[0-9a-f]{32}$'),
      status text NOT NULL DEFAULT 'ACCEPTED' CHECK(status IN
        ('ACCEPTED','RUNNING','SUCCEEDED','BLOCKED','FAILED')),
      slots integer NOT NULL CHECK(slots BETWEEN 3 AND 34),
      registry_watermark bigint NOT NULL CHECK(registry_watermark>=0),
      registry_hash text NOT NULL CHECK(registry_hash ~ '^[0-9a-f]{64}$'),
      snapshot_version bigint NOT NULL,snapshot_at timestamptz NOT NULL,
      accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      owner uuid,fence bigint NOT NULL DEFAULT 0 CHECK(fence>=0),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
      lease_until timestamptz,started_at timestamptz,error_code text,
      bar_steps integer NOT NULL DEFAULT 0 CHECK(bar_steps BETWEEN 0 AND 100000),
      publication_bytes bigint NOT NULL DEFAULT 0 CHECK(publication_bytes BETWEEN 0 AND 12582912),
      UNIQUE(actor,idempotency_key));
    ALTER TABLE backtest.experiment_definitions ADD FOREIGN KEY(supersedes_batch_id)
      REFERENCES backtest.experiment_batches;
    ALTER TABLE backtest.runs ADD COLUMN quant_batch_id uuid REFERENCES backtest.experiment_batches;
    CREATE INDEX quant_queue ON backtest.experiment_batches(status,accepted_at,batch_id);
    CREATE TABLE backtest.experiment_source_receipts (
      batch_id uuid PRIMARY KEY REFERENCES backtest.experiment_batches,
      license_body jsonb NOT NULL,trust_body jsonb NOT NULL,
      license_fingerprint text NOT NULL CHECK(license_fingerprint=encode(sha256(convert_to(
        backtest.quant_canonical(license_body),'UTF8')),'hex')),
      trust_fingerprint text NOT NULL CHECK(trust_fingerprint=encode(sha256(convert_to(
        backtest.quant_canonical(trust_body),'UTF8')),'hex')));
    CREATE TABLE backtest.experiment_trust_bindings (
      batch_id uuid NOT NULL REFERENCES backtest.experiment_source_receipts,
      decision_id uuid NOT NULL REFERENCES research.trust_decisions,
      PRIMARY KEY(batch_id,decision_id));
    CREATE TABLE backtest.experiment_snapshots (
      batch_id uuid PRIMARY KEY REFERENCES backtest.experiment_batches,
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      body jsonb NOT NULL,canonical_text text NOT NULL,
      CHECK(octet_length(canonical_text)<=12582912),
      CHECK(fingerprint=encode(sha256(convert_to(canonical_text,'UTF8')),'hex')));
    CREATE TABLE backtest.experiment_revision_bindings (
      batch_id uuid NOT NULL REFERENCES backtest.experiment_snapshots,
      kind text NOT NULL CHECK(kind IN ('BAR','CALENDAR','QUALITY','EVIDENCE','COVERAGE')),
      reference_id uuid NOT NULL,expected_fingerprint text NOT NULL,
      expected_identity jsonb NOT NULL,
      bar_id uuid REFERENCES research.bar_revisions,
      calendar_id uuid REFERENCES research.calendar_versions,
      quality_id uuid REFERENCES research.quality_observations,
      evidence_id uuid REFERENCES research.evidence_revisions,
      coverage_id uuid REFERENCES backtest.corporate_action_coverage,
      PRIMARY KEY(batch_id,kind,reference_id),
      CHECK(num_nonnulls(bar_id,calendar_id,quality_id,evidence_id,coverage_id)=1),
      CHECK(CASE kind WHEN 'BAR' THEN bar_id IS NOT DISTINCT FROM reference_id
        WHEN 'CALENDAR' THEN calendar_id IS NOT DISTINCT FROM reference_id
        WHEN 'QUALITY' THEN quality_id IS NOT DISTINCT FROM reference_id
        WHEN 'COVERAGE' THEN coverage_id IS NOT DISTINCT FROM reference_id ELSE
          evidence_id IS NOT DISTINCT FROM reference_id END));
    CREATE TABLE backtest.experiment_phase_bindings (
      batch_id uuid NOT NULL REFERENCES backtest.experiment_snapshots,
      phase_key text NOT NULL,phase text NOT NULL CHECK(phase IN
        ('training','validation','test','holdout')),fold_index integer,
      start_at timestamptz NOT NULL,end_at timestamptz NOT NULL CHECK(start_at<end_at),
      seed_ids uuid[] NOT NULL, fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      body jsonb NOT NULL,canonical_text text NOT NULL,
      PRIMARY KEY(batch_id,phase_key),CHECK((phase='holdout')=(fold_index IS NULL)),
      CHECK(fold_index BETWEEN 0 AND 3),
      CHECK(body=canonical_text::jsonb AND octet_length(canonical_text)<=12582912),
      CHECK(fingerprint=encode(sha256(convert_to(canonical_text,'UTF8')),'hex')));
    CREATE TABLE backtest.experiment_selections (
      batch_id uuid NOT NULL REFERENCES backtest.experiment_batches,
      scope text NOT NULL,selected_candidate_id text,
      status text NOT NULL CHECK(status IN ('SELECTED','NO_ELIGIBLE_CANDIDATE')),
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      body jsonb NOT NULL,canonical_text text NOT NULL,PRIMARY KEY(batch_id,scope),
      CHECK((status='SELECTED')=(selected_candidate_id IS NOT NULL)),
      CHECK(octet_length(canonical_text)<=12582912),
      CHECK(fingerprint=encode(sha256(convert_to(canonical_text,'UTF8')),'hex')));
    ALTER TABLE backtest.retrospective_reports ALTER COLUMN assessment_id DROP NOT NULL,
      ADD COLUMN quant_batch_id uuid REFERENCES backtest.experiment_batches,
      ADD COLUMN quant_phase_key text,
      ADD COLUMN quant_report_hash text CHECK(quant_report_hash ~ '^[0-9a-f]{64}$'),
      ADD FOREIGN KEY(quant_batch_id,quant_phase_key)
        REFERENCES backtest.experiment_phase_bindings(batch_id,phase_key),
      ADD CONSTRAINT quant_reliability_variant CHECK(
        (quant_batch_id IS NULL AND quant_phase_key IS NULL AND assessment_id IS NOT NULL
          AND quant_report_hash IS NULL) OR
        (quant_batch_id IS NOT NULL AND quant_phase_key IS NOT NULL AND assessment_id IS NULL
          AND phase='FINAL' AND run_id IS NOT NULL AND quant_report_hash IS NOT NULL));
    CREATE TABLE backtest.experiment_result_links (
      batch_id uuid NOT NULL REFERENCES backtest.experiment_batches,
      candidate_id text NOT NULL,role text NOT NULL,phase_key text NOT NULL,
      run_id uuid UNIQUE REFERENCES backtest.runs,
      reliability_report_id uuid UNIQUE REFERENCES backtest.retrospective_reports,
      status text NOT NULL CHECK(status IN ('COMPLETE','BLOCKED','FAILED',
        'NOT_EVALUATED_BY_PROTOCOL','NOT_EVALUATED_NO_SELECTION')),
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      body jsonb NOT NULL,canonical_text text NOT NULL,
      PRIMARY KEY(batch_id,candidate_id,role,phase_key),
      FOREIGN KEY(batch_id,phase_key) REFERENCES backtest.experiment_phase_bindings,
      CHECK((status='COMPLETE')=(run_id IS NOT NULL)),
      CHECK((status='COMPLETE')=(reliability_report_id IS NOT NULL)),
      CHECK(octet_length(canonical_text)<=12582912),
      CHECK(fingerprint=encode(sha256(convert_to(canonical_text,'UTF8')),'hex')));
    CREATE TABLE backtest.experiment_reports (
      batch_id uuid PRIMARY KEY REFERENCES backtest.experiment_batches,
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      body jsonb NOT NULL,canonical_text text NOT NULL,
      CHECK(body=canonical_text::jsonb AND octet_length(canonical_text)<=12582912),
      CHECK(fingerprint=encode(sha256(convert_to(canonical_text,'UTF8')),'hex')));
    CREATE TABLE backtest.experiment_events (
      event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      batch_id uuid NOT NULL REFERENCES backtest.experiment_batches,
      status text NOT NULL CHECK(status IN ('ACCEPTED','RUNNING','SUCCEEDED','BLOCKED','FAILED')),
      fence bigint NOT NULL,observed_at timestamptz NOT NULL DEFAULT clock_timestamp());
    CREATE TABLE backtest.test_exposures (
      exposure_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      source_kind text NOT NULL CHECK(source_kind IN ('RUN','RETROSPECTIVE','BATCH')),
      source_id uuid NOT NULL,phase_key text NOT NULL,
      instrument_id uuid,feed text NOT NULL DEFAULT 'iex',interval text NOT NULL DEFAULT '15Min',
      adjustment text NOT NULL DEFAULT 'raw',
      research_role text NOT NULL CHECK(research_role IN ('IN_SAMPLE_ANALYSIS',
        'PARAMETER_SELECTION','WALK_FORWARD_OOS','FINAL_HOLDOUT','UNKNOWN')),
      start_at timestamptz,end_at timestamptz,
      status text NOT NULL CHECK(status IN ('ACCEPTED','RUNNING','SUCCEEDED','BLOCKED','FAILED')),
      knowledge_status text NOT NULL CHECK(knowledge_status IN ('REQUESTED','RESULT_AVAILABLE')),
      observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(source_kind,source_id,phase_key,status,knowledge_status),
      CHECK((start_at IS NULL AND end_at IS NULL) OR start_at<end_at));
    CREATE INDEX exposure_overlap ON backtest.test_exposures
      (instrument_id,feed,interval,adjustment,start_at,end_at);
    """)


def _exposures():
    op.execute("""
    CREATE FUNCTION backtest.register_exposures(p_kind text,p_id uuid,p_request jsonb,
      p_status text,p_available boolean DEFAULT false) RETURNS void LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE f jsonb; idx integer:=0; phase text; a timestamptz;b timestamptz;
      role_name text; item jsonb;instrument uuid;available boolean;folds jsonb;
    BEGIN
      IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'shared backtest admission requires read committed'; END IF;
      PERFORM pg_advisory_xact_lock(894217345);
      instrument:=CASE WHEN p_request->>'instrumentId' ~
        '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        THEN (p_request->>'instrumentId')::uuid ELSE NULL END;
      folds:=coalesce(p_request->'protocol'->'folds',p_request->'folds');
      IF jsonb_typeof(folds) IS DISTINCT FROM 'array' THEN folds:='[]'::jsonb; END IF;
      FOR f IN SELECT value FROM jsonb_array_elements(folds) LOOP
        FOREACH phase IN ARRAY ARRAY['training','validation','test'] LOOP
          item:=f->phase;
          BEGIN
          IF item IS NOT NULL THEN a:=(item->>'start')::timestamptz;
            b:=(item->>'end')::timestamptz;
          ELSE a:=(f->>(phase||'Start'))::timestamptz;
            b:=(f->>(phase||'End'))::timestamptz; END IF;
          EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
            a:=NULL;b:=NULL;
          END;
          IF a IS NULL OR b IS NULL OR a>=b THEN
            IF p_kind<>'BATCH' AND phase IN ('training','test') THEN
              INSERT INTO backtest.test_exposures(source_kind,source_id,phase_key,instrument_id,
                research_role,status,knowledge_status)
                VALUES(p_kind,p_id,idx||':'||phase,instrument,'UNKNOWN',p_status,'REQUESTED')
                ON CONFLICT DO NOTHING;
            END IF;
            CONTINUE;
          END IF;
          role_name:=CASE phase WHEN 'training' THEN CASE WHEN p_kind='BATCH'
            THEN 'IN_SAMPLE_ANALYSIS' ELSE 'UNKNOWN' END
            WHEN 'validation' THEN 'PARAMETER_SELECTION' ELSE 'WALK_FORWARD_OOS' END;
          available:=p_available AND phase='test' AND (EXISTS(SELECT FROM backtest.fold_results
            WHERE run_id=p_id AND fold_index=idx AND jsonb_array_length(body->'equity')>0)
            OR EXISTS(SELECT FROM backtest.results r CROSS JOIN LATERAL
              jsonb_array_elements(coalesce(r.body->'equity','[]'::jsonb)) e
              WHERE r.run_id=p_id AND (e->>'foldIndex')::integer=idx));
          INSERT INTO backtest.test_exposures(source_kind,source_id,phase_key,instrument_id,
            feed,interval,adjustment,research_role,start_at,end_at,status,knowledge_status)
          VALUES(p_kind,p_id,idx||':'||phase,instrument,
            lower(coalesce(p_request->>'feed','iex')),coalesce(p_request->>'interval','15Min'),
            coalesce(p_request->>'adjustment','raw'),role_name,a,b,p_status,
            CASE WHEN available THEN 'RESULT_AVAILABLE' ELSE 'REQUESTED' END)
          ON CONFLICT DO NOTHING;
        END LOOP;
        idx:=idx+1;
      END LOOP;
      item:=p_request->'protocol'->'holdout';
      IF item IS NOT NULL THEN
        INSERT INTO backtest.test_exposures(source_kind,source_id,phase_key,instrument_id,
          feed,interval,adjustment,research_role,start_at,end_at,status,knowledge_status)
        VALUES(p_kind,p_id,'holdout',instrument,
          lower(p_request->>'feed'),p_request->>'interval',p_request->>'adjustment',
          'FINAL_HOLDOUT',(item->>'start')::timestamptz,(item->>'end')::timestamptz,
          p_status,'REQUESTED') ON CONFLICT DO NOTHING;
      END IF;
      IF NOT EXISTS(SELECT FROM backtest.test_exposures WHERE source_kind=p_kind AND
          source_id=p_id AND phase_key<>'unknown')
        THEN INSERT INTO backtest.test_exposures(source_kind,source_id,phase_key,instrument_id,
          research_role,status,knowledge_status)
          VALUES(p_kind,p_id,'unknown',instrument,'UNKNOWN',p_status,
            'REQUESTED') ON CONFLICT DO NOTHING; END IF;
    END $$;
    REVOKE ALL ON FUNCTION backtest.register_exposures(text,uuid,jsonb,text,boolean) FROM PUBLIC;
    DO $$ DECLARE r record; BEGIN
      FOR r IN SELECT * FROM backtest.runs LOOP
        PERFORM backtest.register_exposures('RUN',r.run_id,r.request,r.status,
          EXISTS(SELECT FROM backtest.results WHERE run_id=r.run_id)
          OR EXISTS(SELECT FROM backtest.fold_results WHERE run_id=r.run_id));
      END LOOP;
      FOR r IN SELECT * FROM backtest.retrospective_assessments LOOP
        PERFORM backtest.register_exposures('RETROSPECTIVE',r.assessment_id,r.request,r.status);
      END LOOP;
    END $$;
    CREATE FUNCTION backtest.guard_exposure_writer() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE identity uuid; kind text;
    BEGIN
      IF TG_TABLE_NAME='runs' AND to_jsonb(NEW)->>'quant_batch_id' IS NOT NULL THEN RETURN NEW;
          END IF;
      IF TG_OP='INSERT' AND current_setting('backtest.exposure_version',true)
        IS DISTINCT FROM 'quant-exposure-v1' THEN RAISE EXCEPTION
          'exposure writer compatibility required'; END IF;
      PERFORM pg_advisory_xact_lock(894217345);
      IF TG_OP='INSERT' AND backtest.active_slots()>100 THEN
        RAISE EXCEPTION 'backtest queue full'; END IF;
      identity:=CASE WHEN TG_TABLE_NAME='runs' THEN (to_jsonb(NEW)->>'run_id')::uuid ELSE
        (to_jsonb(NEW)->>'assessment_id')::uuid END;
      kind:=CASE TG_TABLE_NAME WHEN 'runs' THEN 'RUN' ELSE 'RETROSPECTIVE' END;
      PERFORM backtest.register_exposures(kind,identity,NEW.request,NEW.status,
        TG_TABLE_NAME='runs' AND (EXISTS(SELECT FROM backtest.results WHERE run_id=identity)
          OR EXISTS(SELECT FROM backtest.fold_results WHERE run_id=identity)));
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION backtest.guard_exposure_writer() FROM PUBLIC;
    CREATE TRIGGER z_exposure_registration AFTER INSERT OR UPDATE ON backtest.runs
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_exposure_writer();
    CREATE TRIGGER z_exposure_registration AFTER INSERT OR UPDATE ON
      backtest.retrospective_assessments FOR EACH ROW
      EXECUTE FUNCTION backtest.guard_exposure_writer();
    """)


def _admission():
    # The old Strict function has a second, internal run-count guard. Its
    # historical validation and hashes stay intact while the count ignores children.
    legacy = (
        op.get_bind()
        .execute(
            text(
                "SELECT prosrc FROM pg_proc WHERE oid="
                "'backtest.legacy_request_run(uuid,text,text,jsonb,text,text,text)'::regprocedure"
            )
        )
        .scalar_one()
    )
    marker = "FROM backtest.runs WHERE status IN ('ACCEPTED','RUNNING')"
    if legacy.count(marker) != 1:
        raise RuntimeError("legacy Strict queue guard drift")
    legacy = legacy.replace(marker, marker + " AND quant_batch_id IS NULL")
    op.execute(
        "CREATE OR REPLACE FUNCTION backtest.legacy_request_run(p_id uuid,p_actor text,"
        "p_key text,p_request jsonb,p_hash text,p_code text,p_trace text) RETURNS uuid "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $qsl$"
        + legacy
        + "$qsl$;"
    )
    # Keep legacy validation/idempotency/snapshot logic, require a version-aware
    # caller, then register from the stored request under the same xact barrier.
    for name, extra in (
        ("request_run", False),
        ("request_readiness", True),
        ("request_retrospective_assessment", False),
        ("request_retrospective_run", False),
    ):
        types = "uuid,text,text,jsonb,text,text," + ("text,text" if extra else "text")
        params = "p_id uuid,p_actor text,p_key text,p_request jsonb,p_hash text,p_code text,"
        params += "p_policy text,p_trace text" if extra else "p_trace text"
        args = "p_id,p_actor,p_key,p_request,p_hash,p_code," + (
            "p_policy,p_trace" if extra else "p_trace"
        )
        op.execute(f"""
        ALTER FUNCTION backtest.{name}({types}) RENAME TO qsl_previous_{name};
        REVOKE ALL ON FUNCTION backtest.qsl_previous_{name}({types}) FROM
          PUBLIC,atl_control_api,atl_backtest_worker;
        DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='atl_paper_control_api') THEN
          REVOKE ALL ON FUNCTION backtest.qsl_previous_{name}({types}) FROM atl_paper_control_api;
        END IF; END $$;
        CREATE FUNCTION backtest.{name}({params}) RETURNS uuid LANGUAGE plpgsql
          SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$ BEGIN
          IF current_setting('backtest.exposure_version',true) IS DISTINCT FROM 'quant-exposure-v1'
            THEN RAISE EXCEPTION 'exposure writer compatibility required'; END IF;
          IF current_setting('transaction_isolation')<>'read committed' THEN
            RAISE EXCEPTION 'shared backtest admission requires read committed'; END IF;
          PERFORM pg_advisory_xact_lock(894217345);
          RETURN backtest.qsl_previous_{name}({args});
        END $$;
        """)
    op.execute("""
    CREATE FUNCTION backtest.active_slots() RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER
      SET search_path=pg_catalog,backtest,pg_temp AS $$
      SELECT ((SELECT count(*) FROM backtest.runs WHERE quant_batch_id IS NULL AND status IN
        ('ACCEPTED','RUNNING'))+(SELECT count(*) FROM backtest.readiness_jobs WHERE status IN
        ('ACCEPTED','RUNNING'))+(SELECT count(*) FROM backtest.retrospective_assessments
        WHERE status IN ('ACCEPTED','RUNNING'))+coalesce((SELECT sum(slots) FROM
          backtest.experiment_batches WHERE status IN ('ACCEPTED','RUNNING')),0))::integer $$;
    CREATE OR REPLACE FUNCTION backtest.check_shared_queue() RETURNS void LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$ BEGIN
      PERFORM pg_advisory_xact_lock(894217345);
      IF backtest.active_slots()>=100 THEN RAISE EXCEPTION 'backtest queue full'; END IF;
    END $$;
    CREATE FUNCTION backtest.request_experiment_definition(p_id uuid,p_actor text,p_body jsonb,
      p_canonical text,p_hash text,p_policy_hash text,p_policy_canonical text,p_candidates jsonb)
          RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE predecessor backtest.experiment_definitions; c jsonb; role_name text;
      candidate_body jsonb;candidate_text text; n integer:=0;policy jsonb;preimage jsonb;core jsonb;
      policy_binding jsonb;f jsonb;fold_count integer;idx integer:=0;w jsonb;old_end timestamptz;
    BEGIN
      IF current_setting('backtest.quant_version',true) IS DISTINCT FROM 'quant-experiment-v1'
        OR p_actor IS DISTINCT FROM 'local-operator' OR p_body->>'schemaVersion' IS DISTINCT
          FROM 'quant-lab-v1' OR length(p_body->>'hypothesis') NOT BETWEEN 1 AND 2000
        OR p_body->'protocol'->>'fittingStatus' IS DISTINCT FROM 'NOT_TRAINABLE'
        OR jsonb_array_length(p_body->'protocol'->'folds') NOT BETWEEN 1 AND 4
        OR p_body->>'researchMode' NOT IN ('STRICT_PIT','RETROSPECTIVE_EXPLORATORY')
        OR NOT(p_body ?& ARRAY['schemaVersion','hypothesis','predecessorDefinitionId',
          'supersedesBatchId','searchSpaces','protocol','strategySelectionPolicy','instrumentId',
          'symbol','currency','feed','interval','adjustment','researchMode','dataSelectionPolicyVersion',
          'startCash','feeUsd','slippageBps','quantityStep'])
        OR (p_body-ARRAY['schemaVersion','hypothesis','predecessorDefinitionId','supersedesBatchId',
          'searchSpaces','protocol','strategySelectionPolicy','instrumentId','symbol','currency','feed',
          'interval','adjustment','researchMode','dataSelectionPolicyVersion','startCash','feeUsd',
          'slippageBps','quantityStep'])<>'{}'::jsonb
        OR p_body->>'dataSelectionPolicyVersion' IS DISTINCT FROM (CASE p_body->>'researchMode'
          WHEN 'STRICT_PIT' THEN 'strict-pit-v1' ELSE 'retrospective-v1' END)
        THEN RAISE EXCEPTION 'invalid quant definition'; END IF;
      policy:=p_body->'strategySelectionPolicy';
      preimage:=p_canonical::jsonb;core:=preimage->1;policy_binding:=p_policy_canonical::jsonb;
      IF preimage->>0 IS DISTINCT FROM 'quant-experiment-v1'
        OR preimage->>2 IS DISTINCT FROM 'quant-research-protocol-v1'
        OR preimage->>3 IS DISTINCT FROM 'quant-common-seed-v1'
        OR core->>'instrument_id' IS DISTINCT FROM p_body->>'instrumentId'
        OR core->>'hypothesis' IS DISTINCT FROM p_body->>'hypothesis'
        OR core->>'symbol' IS DISTINCT FROM p_body->>'symbol'
        OR core->>'research_mode' IS DISTINCT FROM p_body->>'researchMode'
        OR core->>'start_cash' IS DISTINCT FROM p_body->>'startCash'
        OR core->>'fee_usd' IS DISTINCT FROM p_body->>'feeUsd'
        OR core->>'slippage_bps' IS DISTINCT FROM p_body->>'slippageBps'
        OR core->>'quantity_step' IS DISTINCT FROM p_body->>'quantityStep'
        OR core->>'feed' IS DISTINCT FROM lower(p_body->>'feed')
        OR core->>'interval' IS DISTINCT FROM p_body->>'interval'
        OR core->>'adjustment' IS DISTINCT FROM p_body->>'adjustment'
        OR core->>'currency' IS DISTINCT FROM p_body->>'currency'
        OR preimage->5 IS DISTINCT FROM policy_binding
        OR preimage->>7 IS DISTINCT FROM p_body->>'dataSelectionPolicyVersion'
        OR policy_binding->>0 IS DISTINCT FROM policy->>'policyId'
        OR (policy_binding->>1)::numeric IS DISTINCT FROM (policy->>'maxDrawdown')::numeric
        OR policy_binding->2 IS DISTINCT FROM policy->'minValidationSessions'
        OR policy_binding->3 IS DISTINCT FROM policy->'minimumEntryCount'
        OR (policy_binding->>4)::numeric IS DISTINCT FROM
          (policy->>'minimumInvestedBarRatio')::numeric
        OR core->'strategy_selection_policy'->>'selector_fingerprint' IS DISTINCT FROM
          policy_binding->>5
        OR policy_binding->>5 IS DISTINCT FROM
          '61ab136098bb3c364f0dacd4365d98565d98db20742edb563f3399293564d958'
        OR core->'protocol'->'holdout' IS DISTINCT FROM p_body->'protocol'->'holdout'
        THEN RAISE EXCEPTION 'quant definition conflict'; END IF;
      IF jsonb_array_length(p_body->'searchSpaces') IS DISTINCT FROM jsonb_array_length(
          core->'search_spaces') OR EXISTS(
        SELECT FROM jsonb_array_elements(p_body->'searchSpaces') wire WHERE
          (wire-ARRAY['strategyId','version','parameterValues'])<>'{}'::jsonb OR NOT EXISTS(
          SELECT FROM jsonb_array_elements(core->'search_spaces') canonical WHERE
            canonical->>'strategy_id'=wire->>'strategyId' AND
          canonical->>'strategy_version'=wire->>'version'
            AND (SELECT jsonb_object_agg(param->>0,param->1) FROM
              jsonb_array_elements(canonical->'parameter_values') param) IS NOT DISTINCT FROM
              (SELECT jsonb_object_agg(k.key,(SELECT jsonb_agg(CASE jsonb_typeof(v)
                WHEN 'string' THEN to_jsonb(trim_scale((v#>>'{}')::numeric)::text) ELSE v END
                  ORDER BY (v#>>'{}')::numeric) FROM jsonb_array_elements(k.value) v))
                FROM jsonb_each(wire->'parameterValues') k))) THEN
          RAISE EXCEPTION 'quant definition conflict'; END IF;
      IF ((p_body->'protocol')-ARRAY['schemaVersion','protocolVersion','folds','holdout',
          'fittingStatus'])
        <>'{}'::jsonb THEN RAISE EXCEPTION 'invalid quant protocol'; END IF;
      fold_count:=jsonb_array_length(p_body->'protocol'->'folds');
      IF jsonb_array_length(core->'protocol'->'folds') IS DISTINCT FROM fold_count
        THEN RAISE EXCEPTION 'quant definition conflict'; END IF;
      FOR f IN SELECT value FROM jsonb_array_elements(p_body->'protocol'->'folds') LOOP
        IF (f-'fold') IS DISTINCT FROM core->'protocol'->'folds'->idx THEN
          RAISE EXCEPTION 'quant definition conflict'; END IF;
        FOREACH role_name IN ARRAY ARRAY['training','validation','test'] LOOP
          w:=f->role_name;
          IF NOT ((w->>'start')::timestamptz<(w->>'end')::timestamptz) OR
            (old_end IS NOT NULL AND (w->>'start')::timestamptz<old_end)
            THEN RAISE EXCEPTION 'invalid quant chronology'; END IF;
          old_end:=(w->>'end')::timestamptz;
        END LOOP;
        idx:=idx+1;
      END LOOP;
      IF (p_body->'protocol'->'holdout'->>'start')::timestamptz<old_end THEN
        RAISE EXCEPTION 'invalid quant chronology'; END IF;
      IF NOT(policy ?& ARRAY['policyId','maxDrawdown','minValidationSessions',
        'minimumEntryCount','minimumInvestedBarRatio']) OR
        (policy-ARRAY['policyId','maxDrawdown','minValidationSessions','minimumEntryCount',
          'minimumInvestedBarRatio'])<>'{}'::jsonb OR policy->>'policyId' NOT IN
        ('MAX_NET_RETURN_WITH_DRAWDOWN_CONSTRAINT_V1','CAPITAL_PRESERVATION_DRAWDOWN_FIRST_V1')
        OR jsonb_typeof(policy->'maxDrawdown')<>'string'
        OR policy->>'maxDrawdown' !~ '^(0|1)([.][0-9]{1,12})?$'
        OR (policy->>'maxDrawdown')::numeric NOT BETWEEN 0 AND 1
        OR policy->>'minValidationSessions' !~ '^[0-9]+$'
        OR (policy->>'minValidationSessions')::integer NOT BETWEEN 20 AND 252
        OR (policy->'minimumEntryCount'<>'null'::jsonb AND (policy->>'minimumEntryCount'
          !~ '^[0-9]+$' OR (policy->>'minimumEntryCount')::integer NOT BETWEEN 0 AND 100000))
        OR (policy->'minimumInvestedBarRatio'<>'null'::jsonb AND
          (jsonb_typeof(policy->'minimumInvestedBarRatio')<>'string' OR
          policy->>'minimumInvestedBarRatio' !~ '^(0|1)([.][0-9]{1,12})?$' OR
          (policy->>'minimumInvestedBarRatio')::numeric NOT BETWEEN 0 AND 1))
        THEN RAISE EXCEPTION 'invalid quant policy'; END IF;
      SELECT count(*) INTO n FROM jsonb_array_elements(p_candidates) AS item(value)
        WHERE item.value->>'role'='SEARCH';
      IF n NOT BETWEEN 1 AND 32 OR jsonb_array_length(p_candidates)<>n+2
        OR (SELECT count(*) FROM jsonb_array_elements(p_candidates) AS item(value)
          WHERE item.value->>'role'='FLAT_REFERENCE')<>1
        OR (SELECT count(*) FROM jsonb_array_elements(p_candidates) AS item(value)
          WHERE item.value->>'role'='BUY_HOLD_REFERENCE')<>1
        THEN RAISE EXCEPTION 'invalid quant candidates'; END IF;
      IF (SELECT array_agg(candidate_id ORDER BY candidate_id COLLATE "C") FROM
          backtest.quant_grid(core->'search_spaces')) IS DISTINCT FROM
        (SELECT array_agg(item.value->>'candidateId' ORDER BY (item.value->>'candidateId')
          COLLATE "C")
          FROM jsonb_array_elements(p_candidates) AS item(value) WHERE item.value->>'role'='SEARCH')
        OR (preimage->>4)::integer IS DISTINCT FROM (SELECT max(CASE
          WHEN item.value->'configuration'->>'strategyId' IN ('sma-cross-v1','ema-cross-v1') THEN
            (item.value->'configuration'->'parameters'->>'slow')::integer-1
          WHEN item.value->'configuration'->>'strategyId'='rsi-mean-reversion-v1' THEN
            (item.value->'configuration'->'parameters'->>'period')::integer ELSE 0 END)
          FROM jsonb_array_elements(p_candidates) AS item(value) WHERE item.value->>'role'='SEARCH')
        THEN RAISE EXCEPTION 'quant grid binding conflict'; END IF;
      SELECT * INTO predecessor FROM backtest.experiment_definitions WHERE
        definition_id=(p_body->>'predecessorDefinitionId')::uuid;
      IF p_body->>'predecessorDefinitionId' IS NOT NULL AND predecessor.definition_id IS NULL
        THEN RAISE EXCEPTION 'quant definition conflict'; END IF;
      IF p_body->>'supersedesBatchId' IS NOT NULL AND NOT EXISTS(SELECT FROM
        backtest.experiment_batches b JOIN backtest.experiment_definitions previous
          USING(definition_id)
        WHERE b.batch_id=(p_body->>'supersedesBatchId')::uuid AND b.actor=p_actor
          AND previous.body->>'instrumentId'=p_body->>'instrumentId') THEN
          RAISE EXCEPTION 'quant history conflict'; END IF;
      IF (core->>'definition_version')::integer IS DISTINCT FROM
          coalesce(predecessor.definition_version,0)+1
        OR core->>'predecessor_hash' IS DISTINCT FROM predecessor.fingerprint
        OR jsonb_array_length(core->'candidates') IS DISTINCT FROM n
        OR jsonb_array_length(preimage->6) IS DISTINCT FROM n
        OR EXISTS(SELECT FROM jsonb_array_elements(core->'candidates') AS canonical(value)
          WHERE NOT EXISTS(SELECT FROM backtest.quant_strategy_revisions installed
            WHERE installed.strategy_id=canonical.value->>'strategy_id'
              AND installed.version=canonical.value->>'strategy_version'
              AND installed.implementation_fingerprint=
                canonical.value->>'implementation_fingerprint'))
        OR EXISTS(SELECT FROM jsonb_array_elements(p_candidates) AS item(value)
          WHERE item.value->>'role'='SEARCH' AND NOT EXISTS(
            SELECT FROM jsonb_array_elements(core->'candidates') AS canonical(value)
            WHERE jsonb_build_array(canonical.value->'strategy_id',
              canonical.value->'strategy_version',
              canonical.value->'parameters')=(item.value->>'canonicalText')::jsonb
              AND EXISTS(SELECT FROM jsonb_array_elements(preimage->6) AS bound(value)
                WHERE bound.value->>0=item.value->>'candidateId' AND bound.value->>1=
                  canonical.value->>'implementation_fingerprint')))
        THEN RAISE EXCEPTION 'quant definition conflict'; END IF;
      INSERT INTO backtest.experiment_definitions(definition_id,definition_version,
        predecessor_id,supersedes_batch_id,actor,fingerprint,policy_hash,policy_canonical,candidate_count,
        body,canonical_text) VALUES(p_id,coalesce(predecessor.definition_version,0)+1,
          predecessor.definition_id,(p_body->>'supersedesBatchId')::uuid,p_actor,p_hash,
          p_policy_hash,p_policy_canonical,n,p_body,p_canonical);
      FOR c IN SELECT value FROM jsonb_array_elements(p_candidates) LOOP
        candidate_text:=c->>'canonicalText';candidate_body:=c->'configuration';
        role_name:=c->>'role';
        IF NOT(candidate_body ?& ARRAY['strategyId','version','parameters']) OR
          (candidate_body-ARRAY['strategyId','version','parameters'])<>'{}'::jsonb OR
          candidate_body->>'version' IS DISTINCT FROM '1' OR candidate_body->>'strategyId' NOT IN
          ('flat-v1','buy-hold-v1','sma-cross-v1','ema-cross-v1','rsi-mean-reversion-v1') THEN
            RAISE EXCEPTION 'invalid quant candidate'; END IF;
        IF candidate_body->>'strategyId' IN ('flat-v1','buy-hold-v1') AND
          candidate_body->'parameters' IS DISTINCT FROM '{}'::jsonb THEN
            RAISE EXCEPTION 'invalid quant candidate'; END IF;
        IF candidate_body->>'strategyId' IN ('sma-cross-v1','ema-cross-v1') AND (
          ((candidate_body->'parameters')-ARRAY['fast','slow'])<>'{}'::jsonb OR
          NOT(candidate_body->'parameters' ?& ARRAY['fast','slow']) OR
          jsonb_typeof(candidate_body->'parameters'->'fast') IS DISTINCT FROM 'number' OR
          jsonb_typeof(candidate_body->'parameters'->'slow') IS DISTINCT FROM 'number' OR
          candidate_body->'parameters'->>'fast' !~ '^[0-9]+$' OR
          candidate_body->'parameters'->>'slow' !~ '^[0-9]+$' OR
          (candidate_body->'parameters'->>'fast')::integer NOT BETWEEN 2 AND 99 OR
          (candidate_body->'parameters'->>'slow')::integer NOT BETWEEN 3 AND 100 OR
          (candidate_body->'parameters'->>'fast')::integer>=
            (candidate_body->'parameters'->>'slow')::integer) THEN
              RAISE EXCEPTION 'invalid quant candidate'; END IF;
        IF candidate_body->>'strategyId'='rsi-mean-reversion-v1' AND (
          ((candidate_body->'parameters')-ARRAY['period','entry','exit'])<>'{}'::jsonb OR
          NOT(candidate_body->'parameters' ?& ARRAY['period','entry','exit']) OR
          jsonb_typeof(candidate_body->'parameters'->'period') IS DISTINCT FROM 'number' OR
          jsonb_typeof(candidate_body->'parameters'->'entry') IS DISTINCT FROM 'string' OR
          jsonb_typeof(candidate_body->'parameters'->'exit') IS DISTINCT FROM 'string' OR
          candidate_body->'parameters'->>'period' !~ '^[0-9]+$' OR
          candidate_body->'parameters'->>'entry' !~ '^(0|[1-9][0-9]*)([.][0-9]{1,4})?$' OR
          candidate_body->'parameters'->>'exit' !~ '^(0|[1-9][0-9]*)([.][0-9]{1,4})?$' OR
          (candidate_body->'parameters'->>'period')::integer NOT BETWEEN 2 AND 50 OR
          (candidate_body->'parameters'->>'entry')::numeric NOT BETWEEN 0 AND 100 OR
          (candidate_body->'parameters'->>'exit')::numeric NOT BETWEEN 0 AND 100 OR
          (candidate_body->'parameters'->>'entry')::numeric>=
            (candidate_body->'parameters'->>'exit')::numeric) THEN
              RAISE EXCEPTION 'invalid quant candidate'; END IF;
        IF role_name='FLAT_REFERENCE' AND candidate_body->>'strategyId'<>'flat-v1'
          OR role_name='BUY_HOLD_REFERENCE' AND candidate_body->>'strategyId'<>'buy-hold-v1'
          THEN RAISE EXCEPTION 'invalid quant reference'; END IF;
        INSERT INTO backtest.experiment_candidates VALUES(p_id,c->>'candidateId',role_name,
          candidate_body,candidate_text);
      END LOOP;
      RETURN p_id;
    END $$;
    CREATE FUNCTION backtest.validate_experiment(p_id uuid,p_body jsonb,p_canonical text,p_hash
          text)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE d backtest.experiment_definitions;previous backtest.experiment_validations;identifier
          uuid;
    BEGIN
      SELECT * INTO STRICT d FROM backtest.experiment_definitions WHERE definition_id=p_id;
      PERFORM pg_advisory_xact_lock(894217345);
      IF current_setting('backtest.quant_version',true) IS DISTINCT FROM 'quant-experiment-v1'
        OR p_body->>'definitionHash' IS DISTINCT FROM d.fingerprint
        OR p_body->>'calendarVerified' IS DISTINCT FROM 'true'
        OR jsonb_typeof(p_body->'calendarVerified') IS DISTINCT FROM 'boolean'
        OR (p_body->>'simulationBarSteps')::integer NOT BETWEEN 1 AND 100000
        OR (p_body->>'snapshotRows')::integer NOT BETWEEN 1 AND 100000
        OR (p_body->>'publicationBytes')::integer NOT BETWEEN 1 AND 12582912
        OR (p_body->>'seedBars')::integer NOT BETWEEN 0 AND 100
        OR (p_body->>'seedBars')::integer IS DISTINCT FROM (d.canonical_text::jsonb->>4)::integer
        OR jsonb_typeof(p_body->'simulationBarSteps') IS DISTINCT FROM 'number'
        OR jsonb_typeof(p_body->'snapshotRows') IS DISTINCT FROM 'number'
        OR jsonb_typeof(p_body->'publicationBytes') IS DISTINCT FROM 'number'
        OR jsonb_typeof(p_body->'seedBars') IS DISTINCT FROM 'number'
        OR p_body->>'simulationBarSteps' !~ '^[0-9]+$'
        OR p_body->>'snapshotRows' !~ '^[0-9]+$'
        OR p_body->>'publicationBytes' !~ '^[0-9]+$'
        OR p_body->>'seedBars' !~ '^[0-9]+$'
        OR NOT(p_body ?& ARRAY['definitionHash','calendarVerified','simulationBarSteps',
          'snapshotRows','publicationBytes','seedBars'])
        THEN RAISE EXCEPTION 'invalid quant validation'; END IF;
      SELECT * INTO previous FROM backtest.experiment_validations WHERE definition_id=p_id;
      IF FOUND THEN
        IF previous.fingerprint IS DISTINCT FROM p_hash OR previous.body IS DISTINCT FROM p_body
          THEN RAISE EXCEPTION 'quant validation conflict'; END IF;
        RETURN previous.validation_id;
      END IF;
      identifier:=gen_random_uuid();
      INSERT INTO backtest.experiment_validations VALUES(p_id,identifier,p_hash,p_body,p_canonical);
      RETURN identifier;
    END $$;
    CREATE FUNCTION backtest.request_experiment_batch(p_id uuid,p_definition uuid,p_version integer,
      p_hash text,p_validation uuid,p_validation_hash text,p_actor text,p_key text,p_code
          text,p_trace text) RETURNS uuid LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,research,pg_temp AS $$
    DECLARE d backtest.experiment_definitions;old backtest.experiment_batches;mark bigint;
      history text;license jsonb;trust jsonb;trust_cap bigint;data_cap bigint;captured_at
          timestamptz;
    BEGIN
      IF current_setting('backtest.quant_version',true) IS DISTINCT FROM 'quant-experiment-v1'
        OR current_setting('backtest.exposure_version',true) IS DISTINCT FROM 'quant-exposure-v1'
        OR p_actor IS DISTINCT FROM 'local-operator' OR p_key IS NULL
        OR p_key !~ '^[A-Za-z0-9._:-]{16,128}$' THEN RAISE EXCEPTION 'invalid quant request'; END
          IF;
      IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'shared backtest admission requires read committed'; END IF;
      PERFORM pg_advisory_xact_lock(894217345);
      SELECT * INTO old FROM backtest.experiment_batches WHERE actor=p_actor AND
          idempotency_key=p_key;
      IF FOUND THEN IF old.definition_id IS DISTINCT FROM p_definition
        OR old.validation_id IS DISTINCT FROM p_validation OR NOT EXISTS(
        SELECT FROM backtest.experiment_definitions d0 JOIN backtest.experiment_validations v
          USING(definition_id) WHERE d0.definition_id=p_definition AND d0.fingerprint=p_hash
          AND d0.definition_version=p_version AND v.validation_id=p_validation
          AND v.fingerprint=p_validation_hash) THEN RAISE EXCEPTION
          'backtest idempotency conflict'; END IF;
        RETURN old.batch_id; END IF;
      SELECT * INTO d FROM backtest.experiment_definitions WHERE definition_id=p_definition
        AND fingerprint=p_hash AND definition_version=p_version;
      IF NOT FOUND OR NOT EXISTS(SELECT FROM backtest.experiment_validations
        WHERE definition_id=p_definition AND validation_id=p_validation
          AND fingerprint=p_validation_hash) THEN RAISE EXCEPTION 'quant definition conflict';
          END IF;
      IF backtest.active_slots()+d.candidate_count+2>100 THEN
        RAISE EXCEPTION 'backtest queue full'; END IF;
      SELECT coalesce(max(exposure_id),0),encode(sha256(convert_to(coalesce(
        jsonb_agg(to_jsonb(e) ORDER BY exposure_id)::text,'[]'),'UTF8')),'hex')
        INTO mark,history FROM backtest.test_exposures e;
      SELECT v.version,clock_timestamp(),to_jsonb(l),t.version,
        jsonb_build_object('version',t.version,'policyVersion',t.policy_version,
        'decisions',coalesce((SELECT jsonb_agg(jsonb_build_object('decisionId',r.decision_id,
          'fingerprint',encode(sha256(convert_to(backtest.quant_canonical(r.body),'UTF8')),'hex'))
          ORDER BY r.version) FROM research.trust_decisions r WHERE r.version<=t.version
            AND r.commit_version<=v.version),'[]'::jsonb))
        INTO data_cap,captured_at,license,trust_cap,trust FROM research.version_state v
        CROSS JOIN research.trust_version_state t CROSS JOIN backtest.source_license_status l
        WHERE v.singleton AND t.singleton;
      IF data_cap IS NULL OR license IS NULL OR trust IS NULL
        OR jsonb_array_length(trust->'decisions')>=100000 THEN
        RAISE EXCEPTION 'quant source context unavailable'; END IF;
      INSERT INTO backtest.experiment_batches(batch_id,definition_id,validation_id,actor,
        idempotency_key,
        code_version,trace_id,slots,registry_watermark,registry_hash,snapshot_version,snapshot_at,
        publication_bytes) VALUES(p_id,p_definition,p_validation,p_actor,p_key,p_code,p_trace,
          d.candidate_count+2,mark,history,data_cap,captured_at,
          octet_length(backtest.quant_canonical(license))+octet_length(backtest.quant_canonical(trust))
            +128*jsonb_array_length(trust->'decisions'));
      INSERT INTO backtest.experiment_source_receipts VALUES(p_id,license,trust,
        encode(sha256(convert_to(backtest.quant_canonical(license),'UTF8')),'hex'),
        encode(sha256(convert_to(backtest.quant_canonical(trust),'UTF8')),'hex'));
      INSERT INTO backtest.experiment_trust_bindings SELECT p_id,decision_id
        FROM research.trust_decisions WHERE version<=trust_cap AND commit_version<=data_cap;
      PERFORM backtest.register_exposures('BATCH',p_id,d.body,'ACCEPTED');
      INSERT INTO backtest.experiment_events(batch_id,status,fence) VALUES(p_id,'ACCEPTED',0);
      RETURN p_id;
    END $$;
    """)


def _worker():
    op.execute("""
    CREATE OR REPLACE FUNCTION backtest.oldest_job() RETURNS TABLE(kind text,id uuid)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
      SELECT q.kind,q.id FROM (
        SELECT 'backtest'::text AS kind,run_id AS id,accepted_at FROM backtest.runs
          WHERE quant_batch_id IS NULL AND (status='ACCEPTED' OR(status='RUNNING'
            AND lease_until<=clock_timestamp() AND attempts<3))
        UNION ALL SELECT 'readiness',assessment_id,accepted_at FROM backtest.readiness_jobs
          WHERE status='ACCEPTED' OR(status='RUNNING' AND lease_until<=clock_timestamp() AND
          attempts<3)
        UNION ALL SELECT 'retrospective',assessment_id,accepted_at FROM
          backtest.retrospective_assessments
          WHERE status='ACCEPTED' OR(status='RUNNING' AND lease_until<=clock_timestamp() AND
          attempts<3)
        UNION ALL SELECT 'quant',batch_id,accepted_at FROM backtest.experiment_batches
          WHERE status='ACCEPTED' OR(status='RUNNING' AND lease_until<=clock_timestamp() AND
          attempts<3)
      ) q ORDER BY accepted_at,id,kind COLLATE "C" LIMIT 1 $$;
    CREATE FUNCTION backtest.assert_quant_fence(p_batch uuid) RETURNS void LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    BEGIN
      IF current_setting('backtest.quant_version',true) IS DISTINCT FROM 'quant-experiment-v1'
        OR NOT EXISTS(SELECT FROM backtest.experiment_batches WHERE batch_id=p_batch
          AND status='RUNNING' AND owner::text=current_setting('backtest.owner',true)
          AND fence::text=current_setting('backtest.fence',true) AND lease_until>clock_timestamp()
          AND started_at>clock_timestamp()-interval '295 seconds' FOR UPDATE)
        THEN RAISE EXCEPTION 'quant lease lost'; END IF;
    END $$;
    CREATE OR REPLACE FUNCTION backtest.guard_shared_worker() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE self_id uuid;first_id uuid;first_kind text;self_kind text;
    BEGIN
      IF TG_TABLE_NAME='runs' AND to_jsonb(NEW)->>'quant_batch_id' IS NOT NULL THEN
        PERFORM backtest.assert_quant_fence((to_jsonb(NEW)->>'quant_batch_id')::uuid);RETURN NEW;
          END IF;
      IF NEW.status<>'RUNNING' THEN RETURN NEW; END IF;
      IF TG_OP='UPDATE' AND OLD.status='RUNNING' AND NEW.owner IS NOT DISTINCT FROM OLD.owner
        AND NEW.fence=OLD.fence THEN
        IF NEW.started_at IS DISTINCT FROM OLD.started_at OR NEW.attempts<>OLD.attempts OR
          (NEW.lease_until IS DISTINCT FROM OLD.lease_until AND (NEW.lease_until IS NULL OR
            OLD.lease_until IS NULL OR NEW.lease_until>OLD.lease_until OR
            NEW.lease_until>=clock_timestamp())) THEN
          RAISE EXCEPTION 'immutable shared worker lease'; END IF;
        RETURN NEW;
      END IF;
      IF current_setting('transaction_isolation')<>'read committed' THEN
        RAISE EXCEPTION 'shared backtest claim requires read committed'; END IF;
      PERFORM pg_advisory_xact_lock(894217346);
      self_id:=coalesce((to_jsonb(NEW)->>'run_id')::uuid,
        (to_jsonb(NEW)->>'assessment_id')::uuid,(to_jsonb(NEW)->>'batch_id')::uuid);
      self_kind:=CASE TG_TABLE_NAME WHEN 'runs' THEN 'backtest' WHEN 'readiness_jobs' THEN
        'readiness' WHEN 'retrospective_assessments' THEN 'retrospective' ELSE 'quant' END;
      SELECT kind,id INTO first_kind,first_id FROM backtest.oldest_job();
      IF first_id IS DISTINCT FROM self_id OR first_kind IS DISTINCT FROM self_kind THEN RAISE
          EXCEPTION
          'shared backtest admission order'; END IF;
      IF EXISTS(SELECT FROM backtest.runs WHERE quant_batch_id IS NULL AND status='RUNNING'
        AND lease_until>clock_timestamp() AND (TG_TABLE_NAME<>'runs' OR run_id<>self_id))
        OR EXISTS(SELECT FROM backtest.readiness_jobs WHERE status='RUNNING'
          AND lease_until>clock_timestamp() AND (TG_TABLE_NAME<>'readiness_jobs' OR
          assessment_id<>self_id))
        OR EXISTS(SELECT FROM backtest.retrospective_assessments WHERE status='RUNNING'
          AND lease_until>clock_timestamp() AND (TG_TABLE_NAME<>'retrospective_assessments' OR
          assessment_id<>self_id))
        OR EXISTS(SELECT FROM backtest.experiment_batches WHERE status='RUNNING'
          AND lease_until>clock_timestamp() AND (TG_TABLE_NAME<>'experiment_batches' OR
          batch_id<>self_id))
        THEN RAISE EXCEPTION 'shared backtest worker busy'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER shared_worker BEFORE INSERT OR UPDATE ON backtest.experiment_batches
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_shared_worker();
    CREATE FUNCTION backtest.guard_quant_batch() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'immutable quant batch'; END IF;
      IF TG_OP='INSERT' THEN
        IF NEW.status<>'ACCEPTED' OR NEW.fence<>0 OR NEW.attempts<>0 OR NEW.owner IS NOT NULL
          OR NEW.started_at IS NOT NULL OR NEW.lease_until IS NOT NULL OR NEW.bar_steps<>0
          THEN RAISE EXCEPTION 'invalid quant initial state'; END IF;
        SELECT NEW.publication_bytes+octet_length(d.canonical_text)+octet_length(d.body::text)+
          (SELECT octet_length(v.canonical_text) FROM backtest.experiment_validations v
            WHERE v.validation_id=NEW.validation_id)+
          (SELECT coalesce(sum(octet_length(c.canonical_text)+octet_length(c.body::text)),0)
            FROM backtest.experiment_candidates c WHERE c.definition_id=d.definition_id)
          INTO NEW.publication_bytes FROM backtest.experiment_definitions d
          WHERE d.definition_id=NEW.definition_id;
        RETURN NEW;
      END IF;
      IF (to_jsonb(NEW)-ARRAY['status','owner','fence','attempts','lease_until','started_at',
        'updated_at','error_code','bar_steps','publication_bytes']) IS DISTINCT FROM
        (to_jsonb(OLD)-ARRAY['status','owner','fence','attempts','lease_until','started_at',
        'updated_at','error_code','bar_steps','publication_bytes']) OR
        OLD.status IN ('SUCCEEDED','BLOCKED','FAILED') THEN RAISE EXCEPTION
          'immutable quant batch'; END IF;
      IF current_setting('backtest.quant_version',true) IS DISTINCT FROM 'quant-experiment-v1'
        THEN RAISE EXCEPTION 'quant lease lost'; END IF;
      IF NEW.status='ACCEPTED' OR NEW.bar_steps<OLD.bar_steps
        OR NEW.publication_bytes<OLD.publication_bytes THEN
          RAISE EXCEPTION 'quant lease lost'; END IF;
      IF NEW.status='RUNNING' AND (OLD.status='ACCEPTED' OR NEW.owner IS DISTINCT FROM OLD.owner
        OR NEW.fence<>OLD.fence) THEN
        IF NEW.fence<>OLD.fence+1 OR NEW.attempts<>OLD.attempts+1 OR OLD.attempts>=3
          OR NEW.owner IS NULL OR NEW.started_at IS NULL OR NEW.lease_until IS NULL
          OR NEW.started_at<statement_timestamp()
          OR NEW.started_at>clock_timestamp() OR NEW.lease_until<=clock_timestamp()
          OR NEW.lease_until>clock_timestamp()+interval '300 seconds'
          OR NEW.bar_steps<>OLD.bar_steps OR NEW.publication_bytes<>OLD.publication_bytes
          OR (OLD.status='RUNNING' AND OLD.lease_until>clock_timestamp()) THEN
            RAISE EXCEPTION 'quant lease lost'; END IF;
      ELSE
        IF NOT(NEW.status='FAILED' AND OLD.status='RUNNING' AND OLD.attempts=3
          AND OLD.lease_until<=clock_timestamp() AND NEW.error_code='ATTEMPTS_EXHAUSTED') THEN
          PERFORM backtest.assert_quant_fence(OLD.batch_id); END IF;
        IF NEW.fence<>OLD.fence OR NEW.attempts<>OLD.attempts
          OR NEW.started_at IS DISTINCT FROM OLD.started_at OR
          (NEW.status='RUNNING' AND (NEW.owner IS DISTINCT FROM OLD.owner OR
            NEW.lease_until IS DISTINCT FROM OLD.lease_until)) OR
          (NEW.status<>'RUNNING' AND (NEW.owner IS NOT NULL OR NEW.lease_until IS NOT NULL))
          THEN RAISE EXCEPTION 'quant lease lost'; END IF;
      END IF;
      IF NEW.status='SUCCEEDED' THEN
        PERFORM backtest.assert_quant_complete(NEW.batch_id);
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER a_quant_batch BEFORE INSERT OR UPDATE OR DELETE ON backtest.experiment_batches
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_quant_batch();
    CREATE FUNCTION backtest.quant_batch_event() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE d jsonb;
    BEGIN
      IF NEW.status IS DISTINCT FROM OLD.status OR NEW.fence<>OLD.fence THEN
        INSERT INTO backtest.experiment_events(batch_id,status,fence)
          VALUES(NEW.batch_id,NEW.status,NEW.fence);
        SELECT body INTO d FROM backtest.experiment_definitions WHERE
          definition_id=NEW.definition_id;
        PERFORM backtest.register_exposures('BATCH',NEW.batch_id,d,NEW.status);
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER z_quant_event AFTER UPDATE ON backtest.experiment_batches FOR EACH ROW
      EXECUTE FUNCTION backtest.quant_batch_event();
    """)


def _content():
    op.execute("""
    CREATE FUNCTION backtest.guard_quant_content() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,research,pg_temp AS $$
    DECLARE b backtest.experiment_batches;d backtest.experiment_definitions;
      source jsonb;actual text;f jsonb;phase_window jsonb;seed uuid;seed_count integer;
      prior_revision jsonb;
      p backtest.experiment_phase_bindings;r backtest.runs;s backtest.experiment_selections;
      reliability backtest.retrospective_reports;
      cell jsonb;expected integer;phase_scope text;identity jsonb;data
          jsonb;
      ref jsonb;source_bar research.bar_revisions;planned jsonb;actual_starts jsonb;ids uuid[];
      bar_start_key text;decision_cutoff timestamptz;
    BEGIN
      PERFORM backtest.assert_quant_fence(NEW.batch_id);
      SELECT * INTO STRICT b FROM backtest.experiment_batches WHERE batch_id=NEW.batch_id;
      SELECT * INTO STRICT d FROM backtest.experiment_definitions WHERE
          definition_id=b.definition_id;
      IF TG_TABLE_NAME='experiment_revision_bindings' THEN
        SELECT to_jsonb(rb) INTO prior_revision FROM backtest.experiment_revision_bindings rb
          WHERE rb.batch_id=NEW.batch_id AND rb.kind=NEW.kind AND rb.reference_id=NEW.reference_id;
        IF prior_revision IS NOT NULL THEN
          IF prior_revision IS DISTINCT FROM to_jsonb(NEW) THEN
            RAISE EXCEPTION 'quant snapshot conflict'; END IF;
          RETURN NULL;
        END IF;
        IF NEW.kind='BAR' THEN SELECT to_jsonb(x),x.fingerprint INTO source,actual
          FROM research.bar_revisions x WHERE revision_id=NEW.reference_id;
        ELSIF NEW.kind='CALENDAR' THEN SELECT to_jsonb(x),x.version INTO source,actual
          FROM research.calendar_versions x WHERE session_id=NEW.reference_id;
        ELSIF NEW.kind='QUALITY' THEN SELECT to_jsonb(x) INTO source
          FROM research.quality_observations x WHERE observation_id=NEW.reference_id;
          actual:=encode(sha256(convert_to(source::text,'UTF8')),'hex');
        ELSIF NEW.kind='COVERAGE' THEN SELECT to_jsonb(x),x.fingerprint INTO source,actual
          FROM backtest.corporate_action_coverage x WHERE coverage_id=NEW.reference_id;
        ELSE SELECT to_jsonb(x),x.fingerprint INTO source,actual
          FROM research.evidence_revisions x WHERE evidence_id=NEW.reference_id; END IF;
        IF source IS NULL OR actual IS DISTINCT FROM NEW.expected_fingerprint OR
          (source->>'commit_version')::bigint>b.snapshot_version OR
          (source->>'ingested_at')::timestamptz>b.snapshot_at OR
          EXISTS(SELECT FROM jsonb_each(NEW.expected_identity) e WHERE source->e.key
            IS DISTINCT FROM e.value) THEN RAISE EXCEPTION 'quant snapshot conflict'; END IF;
        IF source ? 'instrument_id' AND source->>'instrument_id' IS DISTINCT FROM
          d.body->>'instrumentId' THEN RAISE EXCEPTION 'quant snapshot conflict'; END IF;
        IF (SELECT count(*) FROM backtest.experiment_revision_bindings WHERE batch_id=NEW.batch_id)
          +(SELECT count(*) FROM backtest.experiment_trust_bindings WHERE batch_id=NEW.batch_id)
          +1>=100000 THEN RAISE EXCEPTION 'quant budget exhausted'; END IF;
        UPDATE backtest.experiment_batches SET publication_bytes=publication_bytes+
          octet_length(to_jsonb(NEW)::text),updated_at=clock_timestamp() WHERE
          batch_id=NEW.batch_id;
        RETURN NEW;
      END IF;
      IF (TG_TABLE_NAME<>'experiment_selections' AND NEW.body->>'definitionHash' IS DISTINCT FROM
          d.fingerprint) OR
        NEW.body->>'strategySelectionPolicyHash' IS DISTINCT FROM d.policy_hash THEN
          RAISE EXCEPTION 'quant definition conflict'; END IF;
      IF TG_TABLE_NAME='experiment_snapshots' THEN
        identity:=NEW.canonical_text::jsonb;data:=identity->1;
        IF NEW.body->>'manifestVersion' IS DISTINCT FROM 'quant-experiment-v1'
          OR (NEW.body->>'snapshotVersion')::bigint IS DISTINCT FROM b.snapshot_version
          OR (NEW.body->>'snapshotAt')::timestamptz IS DISTINCT FROM b.snapshot_at
          OR NEW.body->'strategySelectionPolicy' IS DISTINCT FROM d.body->'strategySelectionPolicy'
          OR NEW.body->'protocol' IS DISTINCT FROM d.body->'protocol'
          OR NEW.body->>'registryHash' IS DISTINCT FROM b.registry_hash
          OR identity->>0 IS DISTINCT FROM 'quant-experiment-v1'
          OR identity->>2 IS DISTINCT FROM d.fingerprint
          OR identity->3 IS DISTINCT FROM d.policy_canonical::jsonb
          OR data->'definition' IS DISTINCT FROM d.canonical_text::jsonb->1
          OR (data->>'snapshot_version')::bigint IS DISTINCT FROM b.snapshot_version
          OR (data->>'snapshot_at')::timestamptz IS DISTINCT FROM b.snapshot_at
          OR data->>'code_version' IS DISTINCT FROM b.code_version
          OR data->>'trust_fingerprint' IS DISTINCT FROM (SELECT trust_fingerprint FROM
            backtest.experiment_source_receipts WHERE batch_id=NEW.batch_id)
          OR data->>'license_receipt_fingerprint' IS DISTINCT FROM (SELECT license_fingerprint FROM
            backtest.experiment_source_receipts WHERE batch_id=NEW.batch_id)
          THEN RAISE EXCEPTION 'quant snapshot conflict'; END IF;
        IF jsonb_typeof(data->'decision_data') IS DISTINCT FROM 'array' OR
          jsonb_typeof(data->'outcome_data') IS DISTINCT FROM 'array' OR
          jsonb_typeof(data->'calendar_revisions') IS DISTINCT FROM 'array' OR
          jsonb_array_length(data->'decision_data')<1 OR jsonb_array_length(data->'outcome_data')<1
          OR jsonb_array_length(data->'calendar_revisions')<1 THEN
            RAISE EXCEPTION 'quant snapshot conflict'; END IF;
        IF d.body->>'researchMode'='STRICT_PIT' AND (
          jsonb_typeof(data->'quality_revisions') IS DISTINCT FROM 'array' OR
          jsonb_typeof(data->'coverage_revisions') IS DISTINCT FROM 'array' OR
          jsonb_array_length(data->'quality_revisions')<1 OR
          jsonb_array_length(data->'coverage_revisions')<1) THEN
            RAISE EXCEPTION 'quant snapshot conflict'; END IF;
        FOREACH phase_scope IN ARRAY ARRAY['decision_data','outcome_data'] LOOP
          FOR ref IN SELECT value FROM jsonb_array_elements(data->phase_scope) LOOP
            SELECT * INTO source_bar FROM research.bar_revisions WHERE
              revision_id=(ref->>'revision_id')::uuid;
            IF NOT FOUND OR source_bar.fingerprint IS DISTINCT FROM ref->>'fingerprint'
              OR source_bar.commit_version>b.snapshot_version OR
          source_bar.ingested_at>b.snapshot_at
              OR jsonb_typeof(ref->'cutoff') IS DISTINCT FROM 'string'
              OR (ref->>'cutoff')::timestamptz>b.snapshot_at
              OR source_bar.instrument_id<>(d.body->>'instrumentId')::uuid
              OR source_bar.provider<>'alpaca' OR source_bar.feed<>'iex' OR
              source_bar.interval<>'15Min' OR source_bar.adjustment<>'raw'
              OR source_bar.bar_end<>source_bar.bar_start+interval '15 minutes'
              OR source_bar.quality<>'VALID'
              THEN RAISE EXCEPTION 'quant snapshot conflict'; END IF;
            bar_start_key:=to_char(source_bar.bar_start AT TIME ZONE 'UTC',
              'YYYY-MM-DD"T"HH24:MI:SS"Z"');
            IF d.body->>'researchMode'='RETROSPECTIVE_EXPLORATORY' AND
              (ref->>'cutoff')::timestamptz IS DISTINCT FROM b.snapshot_at THEN
                RAISE EXCEPTION 'quant snapshot conflict'; END IF;
            IF d.body->>'researchMode'='STRICT_PIT' AND (
              source_bar.ingested_at>(ref->>'cutoff')::timestamptz
              OR source_bar.bar_end>(ref->>'cutoff')::timestamptz
              OR (phase_scope='outcome_data' AND (ref->>'cutoff')::timestamptz IS DISTINCT FROM
                source_bar.bar_end+interval '300 seconds')) THEN
                RAISE EXCEPTION 'quant snapshot conflict'; END IF;
            IF phase_scope='decision_data' AND NOT EXISTS(
              SELECT FROM jsonb_array_elements(data->'phase_bindings') AS step(value)
              WHERE (step.value->'expected_bar_starts' ? bar_start_key AND
                (d.body->>'researchMode'<>'STRICT_PIT' OR (ref->>'cutoff')::timestamptz=
                  source_bar.bar_end+interval '60 seconds'))
                OR (step.value->'expected_seed_starts' ? bar_start_key AND
                  (d.body->>'researchMode'<>'STRICT_PIT' OR (ref->>'cutoff')::timestamptz=
                    (step.value->'expected_bar_starts'->>0)::timestamptz+interval '16 minutes')))
              THEN RAISE EXCEPTION 'quant decision cutoff conflict'; END IF;
          END LOOP;
        END LOOP;
      ELSIF TG_TABLE_NAME='experiment_phase_bindings' THEN
        IF NEW.phase='holdout' THEN phase_window:=d.body->'protocol'->'holdout';
        ELSE phase_window:=d.body->'protocol'->'folds'->NEW.fold_index->NEW.phase; END IF;
        SELECT (body->>'seedBars')::integer INTO seed_count FROM backtest.experiment_validations
          WHERE definition_id=d.definition_id;
        SELECT item,snap.canonical_text::jsonb->1 INTO planned,data
          FROM backtest.experiment_snapshots snap CROSS JOIN LATERAL
          jsonb_array_elements(snap.canonical_text::jsonb->1->'phase_bindings') item
          WHERE snap.batch_id=NEW.batch_id AND item->>'phase'=NEW.phase AND
            (item->>'fold_index')::integer IS NOT DISTINCT FROM NEW.fold_index;
        IF phase_window IS NULL OR NEW.start_at IS DISTINCT FROM
          (phase_window->>'start')::timestamptz
          OR NEW.end_at IS DISTINCT FROM (phase_window->>'end')::timestamptz
          OR NEW.phase_key IS DISTINCT FROM (CASE WHEN NEW.phase='holdout' THEN 'holdout'
            ELSE NEW.fold_index||':'||NEW.phase END) OR cardinality(NEW.seed_ids)<>seed_count
          OR NEW.body->'seedRevisionIds' IS DISTINCT FROM to_jsonb(NEW.seed_ids)
          OR (SELECT count(DISTINCT x) FROM unnest(NEW.seed_ids)x)<>seed_count
          OR planned IS NULL
          THEN RAISE EXCEPTION 'quant seed conflict'; END IF;
        SELECT coalesce(jsonb_agg(to_char(x.bar_start AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS"Z"') ORDER BY ord),'[]'::jsonb) INTO actual_starts
          FROM unnest(NEW.seed_ids) WITH ORDINALITY AS seed_ref(id,ord)
          JOIN research.bar_revisions x ON x.revision_id=seed_ref.id;
        IF actual_starts IS DISTINCT FROM planned->'expected_seed_starts' THEN
          RAISE EXCEPTION 'quant seed conflict'; END IF;
        SELECT array_agg(value::uuid ORDER BY ord) INTO ids FROM jsonb_array_elements_text(
          NEW.body->'outcomeRevisionIds') WITH ORDINALITY AS outcome_ref(value,ord);
        SELECT coalesce(jsonb_agg(to_char(x.bar_start AT TIME ZONE 'UTC',
          'YYYY-MM-DD"T"HH24:MI:SS"Z"') ORDER BY ord),'[]'::jsonb) INTO actual_starts
          FROM unnest(ids) WITH ORDINALITY AS outcome_ref(id,ord)
          JOIN research.bar_revisions x ON x.revision_id=outcome_ref.id
          JOIN backtest.experiment_revision_bindings rb ON rb.bar_id=x.revision_id
            AND rb.batch_id=NEW.batch_id AND rb.kind='BAR';
        IF actual_starts IS DISTINCT FROM planned->'expected_bar_starts'
          OR cardinality(ids) IS DISTINCT FROM jsonb_array_length(planned->'expected_bar_starts')
          OR EXISTS(SELECT FROM unnest(ids) AS used(id) JOIN research.bar_revisions x
            ON x.revision_id=used.id WHERE NOT EXISTS(SELECT FROM
              jsonb_array_elements(data->'outcome_data') AS evidence(value)
              WHERE evidence.value->>'revision_id'=used.id::text AND
                (evidence.value->>'cutoff')::timestamptz=(CASE WHEN
          d.body->>'researchMode'='STRICT_PIT'
                  THEN x.bar_end+interval '300 seconds' ELSE b.snapshot_at END))) THEN
          RAISE EXCEPTION 'quant snapshot conflict'; END IF;
        IF EXISTS(SELECT FROM jsonb_array_elements_text(planned->'expected_bar_starts') AS
          expected_at(value)
          WHERE NOT EXISTS(SELECT FROM jsonb_array_elements(data->'decision_data') AS
          evidence(value)
            JOIN backtest.experiment_revision_bindings rb ON rb.batch_id=NEW.batch_id
              AND rb.kind='BAR' AND rb.reference_id=(evidence.value->>'revision_id')::uuid
            JOIN research.bar_revisions x ON x.revision_id=rb.bar_id
            WHERE x.bar_start=expected_at.value::timestamptz AND
              (evidence.value->>'cutoff')::timestamptz=(CASE WHEN
          d.body->>'researchMode'='STRICT_PIT'
                THEN x.bar_end+interval '60 seconds' ELSE b.snapshot_at END))) THEN
          RAISE EXCEPTION 'quant decision revision conflict'; END IF;
        decision_cutoff:=CASE WHEN d.body->>'researchMode'='STRICT_PIT' THEN
          (planned->'expected_bar_starts'->>0)::timestamptz+interval '16 minutes' ELSE
          b.snapshot_at END;
        FOREACH seed IN ARRAY NEW.seed_ids LOOP
          IF NOT EXISTS(SELECT FROM backtest.experiment_revision_bindings rb
            JOIN research.bar_revisions x ON x.revision_id=rb.bar_id
            WHERE rb.batch_id=NEW.batch_id AND rb.kind='BAR' AND rb.reference_id=seed
            AND x.bar_end<=NEW.start_at AND x.instrument_id=(d.body->>'instrumentId')::uuid
            AND x.feed='iex' AND x.interval='15Min' AND x.adjustment='raw') OR NOT EXISTS(
              SELECT FROM jsonb_array_elements(data->'decision_data') AS evidence(value)
              WHERE evidence.value->>'revision_id'=seed::text AND
                (evidence.value->>'cutoff')::timestamptz=decision_cutoff)
            THEN RAISE EXCEPTION 'quant seed conflict'; END IF;
        END LOOP;
      ELSIF TG_TABLE_NAME='experiment_selections' THEN
        identity:=NEW.canonical_text::jsonb;
        IF identity->>0 IS DISTINCT FROM 'quant-selection-v1'
          OR identity->1 IS DISTINCT FROM d.policy_canonical::jsonb
          OR identity->>2 IS DISTINCT FROM (CASE WHEN NEW.scope='final' THEN 'final'
            ELSE 'fold'||chr(58)||NEW.scope END)
          OR identity->4 IS DISTINCT FROM (NEW.body-ARRAY['selectionHash','frozenAt'])
          OR NEW.body->>'selectionHash' IS DISTINCT FROM NEW.fingerprint
          OR identity->3 IS DISTINCT FROM (SELECT jsonb_agg(l.fingerprint ORDER BY l.fingerprint
          COLLATE "C")
            FROM backtest.experiment_result_links l JOIN backtest.experiment_phase_bindings ph
              USING(batch_id,phase_key) WHERE l.batch_id=NEW.batch_id AND l.role='SEARCH'
              AND ph.phase='validation' AND (NEW.scope='final' OR ph.fold_index::text=NEW.scope))
          THEN RAISE EXCEPTION 'quant selection conflict'; END IF;
        PERFORM backtest.check_quant_selection(NEW.batch_id,NEW.scope,NEW.body);
        IF EXISTS(SELECT FROM backtest.experiment_result_links l
          JOIN backtest.experiment_phase_bindings ph USING(batch_id,phase_key)
          WHERE l.batch_id=NEW.batch_id AND ph.phase IN ('test','holdout'))
          THEN RAISE EXCEPTION 'quant selections already frozen'; END IF;
        expected:=jsonb_array_length(d.body->'protocol'->'folds');
        IF NEW.scope<>'final' AND (NEW.scope !~ '^[0-3]$' OR NEW.scope::integer>=expected)
          THEN RAISE EXCEPTION 'quant selection conflict'; END IF;
        IF NEW.body->>'status' IS DISTINCT FROM NEW.status OR
          NEW.body->>'selectedCandidateId' IS DISTINCT FROM NEW.selected_candidate_id
          OR NEW.body->'strategySelectionPolicy' IS DISTINCT FROM d.body->'strategySelectionPolicy'
          OR NEW.body->>'validationInputHash' IS DISTINCT FROM encode(sha256(convert_to(
            backtest.quant_canonical(identity->3),'UTF8')),'hex')
          OR jsonb_array_length(NEW.body->'candidates') IS DISTINCT FROM d.candidate_count
          THEN RAISE EXCEPTION 'quant selection conflict'; END IF;
        IF NEW.selected_candidate_id IS NOT NULL AND NOT EXISTS(
          SELECT FROM backtest.experiment_candidates WHERE definition_id=d.definition_id
          AND role='SEARCH' AND candidate_id=NEW.selected_candidate_id)
          THEN RAISE EXCEPTION 'quant selection conflict'; END IF;
        FOR cell IN SELECT value FROM jsonb_array_elements(NEW.body->'candidates') LOOP
          IF NOT EXISTS(SELECT FROM backtest.experiment_candidates c WHERE
          c.definition_id=d.definition_id
            AND c.role='SEARCH' AND c.candidate_id=cell->>'candidateId') THEN
              RAISE EXCEPTION 'quant selection gates required'; END IF;
          IF EXISTS(SELECT FROM backtest.experiment_phase_bindings ph WHERE ph.batch_id=NEW.batch_id
            AND ph.phase='validation' AND (NEW.scope='final' OR ph.fold_index=
              CASE WHEN NEW.scope='final' THEN NULL ELSE NEW.scope::integer END)
            AND NOT EXISTS(SELECT FROM backtest.experiment_result_links l WHERE
          l.batch_id=ph.batch_id
              AND l.phase_key=ph.phase_key AND l.candidate_id=cell->>'candidateId' AND
          l.role='SEARCH'
              AND l.status='COMPLETE')) THEN RAISE EXCEPTION 'quant incomplete validation'; END IF;
          IF cell->>'candidateId'=NEW.selected_candidate_id AND cell->>'eligibility'<>'ELIGIBLE'
            THEN RAISE EXCEPTION 'quant selection gates required'; END IF;
        END LOOP;
      ELSIF TG_TABLE_NAME='experiment_result_links' THEN
        identity:=NEW.body-ARRAY['resultHash','runId','reportId','batchId'];
        IF identity ? 'reliability' AND jsonb_typeof(identity->'reliability')='object' THEN
          identity:=jsonb_set(identity,'{reliability}',(identity->'reliability')-ARRAY['runId','reportId']);
        END IF;
        IF NEW.canonical_text::jsonb IS DISTINCT FROM jsonb_build_array('quant-candidate-result-v1',
          NEW.candidate_id,NEW.role,NEW.phase_key,NEW.status,identity) THEN
            RAISE EXCEPTION 'quant result identity conflict'; END IF;
        SELECT * INTO STRICT p FROM backtest.experiment_phase_bindings
          WHERE batch_id=NEW.batch_id AND phase_key=NEW.phase_key;
        IF NOT EXISTS(SELECT FROM backtest.experiment_candidates WHERE definition_id=d.definition_id
          AND candidate_id=NEW.candidate_id AND role=NEW.role)
          THEN RAISE EXCEPTION 'quant candidate conflict'; END IF;
        IF p.phase IN ('test','holdout') THEN
          IF (SELECT count(*) FROM backtest.experiment_selections WHERE batch_id=NEW.batch_id)<>
            jsonb_array_length(d.body->'protocol'->'folds')+1 THEN
              RAISE EXCEPTION 'quant all selections required'; END IF;
          phase_scope:=CASE WHEN p.phase='holdout' THEN 'final' ELSE p.fold_index::text END;
          SELECT * INTO STRICT s FROM backtest.experiment_selections WHERE batch_id=NEW.batch_id
            AND scope=phase_scope;
          IF NEW.role='SEARCH' AND (NEW.status IS DISTINCT FROM CASE
            WHEN s.status='NO_ELIGIBLE_CANDIDATE' THEN 'NOT_EVALUATED_NO_SELECTION'
            WHEN NEW.candidate_id<>s.selected_candidate_id THEN 'NOT_EVALUATED_BY_PROTOCOL'
            ELSE 'COMPLETE' END) THEN RAISE EXCEPTION 'quant selection conflict'; END IF;
        ELSIF NEW.status NOT IN ('COMPLETE','BLOCKED','FAILED') THEN
          RAISE EXCEPTION 'quant incomplete validation'; END IF;
        IF NEW.run_id IS NOT NULL THEN
          SELECT * INTO r FROM backtest.runs WHERE run_id=NEW.run_id;
          SELECT * INTO reliability FROM backtest.retrospective_reports WHERE
            report_id=NEW.reliability_report_id;
          IF r.quant_batch_id IS DISTINCT FROM NEW.batch_id OR r.status<>'SUCCEEDED'
            OR NOT EXISTS(SELECT FROM backtest.results WHERE run_id=NEW.run_id)
            OR NOT EXISTS(SELECT FROM backtest.manifests WHERE run_id=NEW.run_id)
            OR NEW.body->>'resultHash' IS DISTINCT FROM (SELECT fingerprint FROM backtest.results
              WHERE run_id=NEW.run_id)
            OR NEW.body->'selectionValues' IS DISTINCT FROM (SELECT body->'selectionValues'
              FROM backtest.results WHERE run_id=NEW.run_id)
            OR NEW.body->'dataComplete' IS DISTINCT FROM 'true'::jsonb
            OR NEW.body->'dataComplete' IS DISTINCT FROM (SELECT body->'dataComplete'
              FROM backtest.results WHERE run_id=NEW.run_id)
            OR NEW.body->'requiredMetricsValid' IS DISTINCT FROM (SELECT
          body->'requiredMetricsValid'
              FROM backtest.results WHERE run_id=NEW.run_id)
            OR r.request->>'candidateId' IS DISTINCT FROM NEW.candidate_id
            OR r.request->>'role' IS DISTINCT FROM NEW.role
            OR r.request->>'phaseKey' IS DISTINCT FROM NEW.phase_key
            OR reliability.quant_batch_id IS DISTINCT FROM NEW.batch_id
            OR reliability.quant_phase_key IS DISTINCT FROM NEW.phase_key
            OR reliability.run_id IS DISTINCT FROM NEW.run_id
            OR NEW.body->>'reliabilityStatus' IS DISTINCT FROM reliability.body->>'evidenceStatus'
            OR NEW.body->'reliability' IS DISTINCT FROM jsonb_build_object('runId',NEW.run_id,
              'reportId',NEW.reliability_report_id,'reportHash',reliability.quant_report_hash,
              'eligibility',reliability.body->>'overallStatus','rating',reliability.body->>'rating',
              'evidenceStatus',reliability.body->>'evidenceStatus')
            THEN RAISE EXCEPTION 'quant result conflict'; END IF;
          IF current_setting('transaction_isolation')<>'read committed' THEN
            RAISE EXCEPTION 'shared backtest admission requires read committed'; END IF;
          PERFORM pg_advisory_xact_lock(894217345);
          INSERT INTO backtest.test_exposures(source_kind,source_id,phase_key,instrument_id,
            feed,interval,adjustment,research_role,start_at,end_at,status,knowledge_status)
            SELECT source_kind,source_id,phase_key,instrument_id,feed,interval,adjustment,
              research_role,start_at,end_at,b.status,'RESULT_AVAILABLE'
            FROM backtest.test_exposures WHERE source_kind='BATCH' AND source_id=NEW.batch_id
              AND phase_key=NEW.phase_key ORDER BY exposure_id LIMIT 1 ON CONFLICT DO NOTHING;
        END IF;
      ELSIF TG_TABLE_NAME='experiment_reports' THEN
        PERFORM backtest.assert_quant_complete(NEW.batch_id,false);
        IF NEW.body->>'snapshotHash' IS DISTINCT FROM (SELECT fingerprint FROM
          backtest.experiment_snapshots
          WHERE batch_id=NEW.batch_id) OR NEW.body->>'registryHash' IS DISTINCT FROM b.registry_hash
          OR NEW.body->>'reliabilityStatus' IS DISTINCT FROM (CASE d.body->>'researchMode'
            WHEN 'STRICT_PIT' THEN 'STRICT_PIT_EVIDENCE_COMPLETE' ELSE
          'EXPLORATORY_WITH_LIMITATIONS' END)
          OR NOT(NEW.body ?& ARRAY['selectionHashes','resultHashes','reliabilityReferences'])
          OR jsonb_typeof(NEW.body->'reliabilityReferences') IS DISTINCT FROM 'array'
          OR NEW.body->'reliabilityReferences' IS DISTINCT FROM (SELECT jsonb_agg(
            reliability_report_id::text ORDER BY candidate_id,role,phase_key)
              FROM backtest.experiment_result_links WHERE batch_id=NEW.batch_id AND
          status='COMPLETE')
          OR NEW.body->'selectionHashes' IS DISTINCT FROM (SELECT jsonb_agg(fingerprint ORDER BY
          scope)
            FROM backtest.experiment_selections WHERE batch_id=NEW.batch_id)
          OR NEW.body->'resultHashes' IS DISTINCT FROM (SELECT jsonb_agg(fingerprint
            ORDER BY candidate_id,role,phase_key) FROM backtest.experiment_result_links
            WHERE batch_id=NEW.batch_id) THEN RAISE EXCEPTION 'quant report conflict'; END IF;
      END IF;
      UPDATE backtest.experiment_batches SET publication_bytes=publication_bytes+
        octet_length(NEW.canonical_text),updated_at=clock_timestamp() WHERE batch_id=NEW.batch_id;
      RETURN NEW;
    END $$;
    CREATE FUNCTION backtest.assert_quant_complete(p_batch uuid,p_report boolean DEFAULT true)
      RETURNS void LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,backtest,research,pg_temp AS $$
    DECLARE b backtest.experiment_batches;d backtest.experiment_definitions;n integer;
    BEGIN
      SELECT * INTO STRICT b FROM backtest.experiment_batches WHERE batch_id=p_batch;
      SELECT * INTO STRICT d FROM backtest.experiment_definitions WHERE
          definition_id=b.definition_id;
      n:=jsonb_array_length(d.body->'protocol'->'folds');
      IF NOT EXISTS(SELECT FROM backtest.experiment_snapshots WHERE batch_id=p_batch)
        OR (SELECT count(*) FROM backtest.experiment_phase_bindings WHERE batch_id=p_batch)<>3*n+1
        OR (SELECT count(*) FROM backtest.experiment_selections WHERE batch_id=p_batch)<>n+1
        OR EXISTS(SELECT FROM backtest.experiment_candidates c CROSS JOIN
          backtest.experiment_phase_bindings p WHERE c.definition_id=d.definition_id AND
          p.batch_id=p_batch
          AND NOT EXISTS(SELECT FROM backtest.experiment_result_links l WHERE l.batch_id=p_batch
            AND l.candidate_id=c.candidate_id AND l.role=c.role AND l.phase_key=p.phase_key
            AND l.status IN ('COMPLETE','NOT_EVALUATED_BY_PROTOCOL','NOT_EVALUATED_NO_SELECTION')))
        OR (p_report AND NOT EXISTS(SELECT FROM backtest.experiment_reports WHERE batch_id=p_batch))
        OR EXISTS(SELECT FROM backtest.experiment_snapshots s CROSS JOIN LATERAL
          jsonb_array_elements((s.canonical_text::jsonb->1->'decision_data')||
            (s.canonical_text::jsonb->1->'outcome_data')) ref WHERE s.batch_id=p_batch
          AND NOT EXISTS(SELECT FROM backtest.experiment_revision_bindings rb WHERE
          rb.batch_id=p_batch
            AND rb.kind='BAR' AND rb.reference_id=(ref->>'revision_id')::uuid
            AND rb.expected_fingerprint=ref->>'fingerprint'))
        OR NOT EXISTS(SELECT FROM backtest.experiment_revision_bindings WHERE batch_id=p_batch
          AND kind='CALENDAR')
        OR EXISTS(SELECT FROM backtest.experiment_snapshots s CROSS JOIN LATERAL
          jsonb_array_elements_text(s.canonical_text::jsonb->1->'calendar_revisions') ref
          WHERE s.batch_id=p_batch AND NOT EXISTS(SELECT FROM
          backtest.experiment_revision_bindings rb
            LEFT JOIN research.evidence_revisions e ON e.evidence_id=rb.evidence_id
            WHERE rb.batch_id=p_batch AND ((rb.kind='CALENDAR' AND rb.expected_fingerprint=ref)
              OR (rb.kind='EVIDENCE' AND e.kind='CALENDAR' AND rb.reference_id::text=ref))))
        OR EXISTS(SELECT FROM backtest.experiment_snapshots s CROSS JOIN LATERAL
          jsonb_array_elements_text(s.canonical_text::jsonb->1->'quality_revisions') ref
          WHERE s.batch_id=p_batch AND ref<>encode(sha256(convert_to('["valid-bar-quality-v1"]',
            'UTF8')),'hex') AND NOT EXISTS(SELECT FROM backtest.experiment_revision_bindings rb
            LEFT JOIN research.evidence_revisions e ON e.evidence_id=rb.evidence_id
            WHERE rb.batch_id=p_batch AND rb.reference_id::text=ref AND
              (rb.kind='QUALITY' OR (rb.kind='EVIDENCE' AND e.kind='QUALITY'))))
        OR EXISTS(SELECT FROM backtest.experiment_snapshots s CROSS JOIN LATERAL
          jsonb_array_elements_text(s.canonical_text::jsonb->1->'coverage_revisions') ref
          WHERE s.batch_id=p_batch AND NOT EXISTS(SELECT FROM
          backtest.experiment_revision_bindings rb
            LEFT JOIN research.evidence_revisions e ON e.evidence_id=rb.evidence_id
            WHERE rb.batch_id=p_batch AND rb.reference_id::text=ref AND
              (rb.kind='COVERAGE' OR (rb.kind='EVIDENCE' AND e.kind='CORPORATE_ACTION'))))
        THEN RAISE EXCEPTION 'quant complete report required'; END IF;
      IF backtest.current_quant_gate(p_batch) IS NOT NULL
        THEN RAISE EXCEPTION 'quant current gate blocked'; END IF;
    END $$;
    CREATE FUNCTION backtest.guard_quant_child() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE batch uuid;b backtest.experiment_batches;d backtest.experiment_definitions;
    BEGIN
      batch:=NEW.quant_batch_id;
      IF TG_OP='UPDATE' AND OLD.quant_batch_id IS DISTINCT FROM NEW.quant_batch_id THEN
        RAISE EXCEPTION 'immutable quant child'; END IF;
      IF batch IS NULL THEN
        IF TG_OP='UPDATE' AND (to_jsonb(NEW)-ARRAY['status','updated_at','error_code','owner',
          'fence','attempts','lease_until','started_at','checkpoint']) IS DISTINCT FROM
          (to_jsonb(OLD)-ARRAY['status','updated_at','error_code','owner','fence','attempts',
            'lease_until','started_at','checkpoint']) THEN
          RAISE EXCEPTION 'immutable legacy run identity'; END IF;
        RETURN NEW;
      END IF;
      PERFORM backtest.assert_quant_fence(batch);
      SELECT * INTO STRICT b FROM backtest.experiment_batches WHERE batch_id=batch;
      SELECT * INTO STRICT d FROM backtest.experiment_definitions WHERE
          definition_id=b.definition_id;
      IF NOT (NEW.request ?& ARRAY['candidateId','role','phaseKey','definitionHash',
        'strategySelectionPolicyHash']) OR NEW.request->>'definitionHash' IS DISTINCT FROM
          d.fingerprint
        OR NEW.request->>'strategySelectionPolicyHash' IS DISTINCT FROM d.policy_hash
        OR NOT EXISTS(SELECT FROM backtest.experiment_candidates c WHERE
          c.definition_id=d.definition_id
          AND c.candidate_id=NEW.request->>'candidateId' AND c.role=NEW.request->>'role')
        OR (NEW.request->>'phaseKey'<>'holdout' AND NOT EXISTS(SELECT FROM
          generate_series(0,jsonb_array_length(d.body->'protocol'->'folds')-1) i CROSS JOIN
          unnest(ARRAY['training','validation','test']) phase WHERE NEW.request->>'phaseKey'=
            i::text||chr(58)||phase))
        OR NEW.request_hash IS DISTINCT FROM encode(sha256(convert_to(
          backtest.quant_canonical(NEW.request),'UTF8')),'hex')
        THEN RAISE EXCEPTION 'quant child identity conflict'; END IF;
      IF TG_OP='UPDATE' AND (to_jsonb(NEW)-ARRAY['status','updated_at','error_code']) IS DISTINCT
          FROM
        (to_jsonb(OLD)-ARRAY['status','updated_at','error_code']) THEN
          RAISE EXCEPTION 'immutable quant child'; END IF;
      IF NEW.research_mode IS NOT NULL OR NEW.experiment_version IS NOT NULL
        OR NEW.owner IS NOT NULL OR NEW.fence<>0 OR NEW.attempts<>0
        OR NEW.lease_until IS NOT NULL OR NEW.started_at IS NOT NULL
        OR NEW.snapshot_version<>b.snapshot_version OR NEW.snapshot_at<>b.snapshot_at
        OR NEW.code_version<>b.code_version OR NEW.status NOT IN
          ('ACCEPTED','SUCCEEDED','BLOCKED','FAILED')
        THEN RAISE EXCEPTION 'quant child is not claimable'; END IF;
      IF TG_OP='UPDATE' AND OLD.status<>'ACCEPTED' THEN RAISE EXCEPTION 'terminal quant child';
          END IF;
      IF NEW.status='SUCCEEDED' AND (NOT EXISTS(SELECT FROM backtest.results WHERE
          run_id=NEW.run_id)
        OR NOT EXISTS(SELECT FROM backtest.manifests WHERE run_id=NEW.run_id)
        OR NOT EXISTS(SELECT FROM backtest.retrospective_reports WHERE run_id=NEW.run_id
          AND quant_batch_id=batch AND quant_phase_key=NEW.request->>'phaseKey'
          AND body->>'evidenceStatus' IN
          ('STRICT_PIT_EVIDENCE_COMPLETE','EXPLORATORY_WITH_LIMITATIONS')))
        THEN RAISE EXCEPTION 'quant result conflict'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER a_quant_child BEFORE INSERT OR UPDATE ON backtest.runs
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_quant_child();
    CREATE FUNCTION backtest.require_quant_result_link() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    BEGIN
      IF NOT EXISTS(SELECT FROM backtest.experiment_result_links WHERE run_id=NEW.run_id
        AND batch_id=NEW.quant_batch_id AND status='COMPLETE') THEN
          RAISE EXCEPTION 'quant atomic phase publication required'; END IF;
      RETURN NULL;
    END $$;
    REVOKE ALL ON FUNCTION backtest.require_quant_result_link() FROM PUBLIC;
    CREATE CONSTRAINT TRIGGER complete_quant_child AFTER UPDATE ON backtest.runs
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      WHEN (NEW.quant_batch_id IS NOT NULL AND NEW.status='SUCCEEDED')
      EXECUTE FUNCTION backtest.require_quant_result_link();
    CREATE FUNCTION backtest.guard_quant_run_content() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE b uuid;r backtest.runs;p backtest.experiment_phase_bindings;
    BEGIN
      SELECT quant_batch_id INTO b FROM backtest.runs WHERE run_id=NEW.run_id;
      IF b IS NOT NULL THEN
        PERFORM backtest.assert_quant_fence(b);
        IF NOT EXISTS(SELECT FROM backtest.experiment_snapshots WHERE batch_id=b)
          OR NEW.body->>'batchId' IS DISTINCT FROM b::text
          OR NEW.body->>'snapshotHash' IS DISTINCT FROM (SELECT fingerprint
            FROM backtest.experiment_snapshots WHERE batch_id=b)
          THEN RAISE EXCEPTION 'quant snapshot conflict'; END IF;
        SELECT * INTO STRICT r FROM backtest.runs WHERE run_id=NEW.run_id;
        SELECT * INTO p FROM backtest.experiment_phase_bindings WHERE batch_id=b
          AND phase_key=r.request->>'phaseKey';
        IF p.phase_key IS NULL OR EXISTS(SELECT FROM unnest(ARRAY['candidateId','role','phaseKey',
          'definitionHash','strategySelectionPolicyHash']) key WHERE NEW.body->key
            IS DISTINCT FROM r.request->key)
          OR (to_jsonb(NEW) ? 'fingerprint' AND to_jsonb(NEW)->>'fingerprint' IS DISTINCT FROM
            encode(sha256(convert_to(backtest.quant_canonical(NEW.body),'UTF8')),'hex'))
          THEN RAISE EXCEPTION 'quant child identity conflict'; END IF;
        PERFORM backtest.assert_quant_oos(b,p.phase,p.fold_index,r.request->>'candidateId',
          r.request->>'role');
        IF backtest.current_quant_gate(b) IS NOT NULL THEN
          RAISE EXCEPTION 'quant current gate blocked'; END IF;
        UPDATE backtest.experiment_batches SET publication_bytes=publication_bytes+
          octet_length(NEW.body::text),updated_at=clock_timestamp() WHERE batch_id=b;
      END IF;
      RETURN NEW;
    END $$;
    """)
    for table in (
        "experiment_snapshots",
        "experiment_revision_bindings",
        "experiment_phase_bindings",
        "experiment_selections",
        "experiment_result_links",
        "experiment_reports",
    ):
        op.execute(
            f"CREATE TRIGGER fenced_insert BEFORE INSERT ON backtest.{table} FOR EACH ROW "
            "EXECUTE FUNCTION backtest.guard_quant_content()"
        )
    for table in ("manifests", "fold_results", "results"):
        op.execute(
            f"CREATE TRIGGER b_quant_content BEFORE INSERT ON backtest.{table} FOR EACH ROW "
            "EXECUTE FUNCTION backtest.guard_quant_run_content()"
        )


def _reliability():
    # The existing report store remains the only store. Legacy triggers keep
    # their exact algorithm; only an explicitly FK-bound QSL variant bypasses
    # the old assessment fence and enters the parent-batch fence below.
    body = (
        op.get_bind()
        .execute(
            text(
                "SELECT prosrc FROM pg_proc WHERE oid="
                "'backtest.guard_retrospective_content()'::regprocedure"
            )
        )
        .scalar_one()
    )
    marker = "    BEGIN\n"
    if body.count(marker) != 1:
        raise RuntimeError("legacy report content guard drift")
    body = body.replace(
        marker,
        marker
        + """      IF TG_TABLE_NAME='retrospective_reports' AND
        to_jsonb(NEW)->>'quant_batch_id' IS NOT NULL THEN RETURN NEW; END IF;
""",
        1,
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION backtest.guard_retrospective_content() RETURNS trigger "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,research,pg_temp AS "
        "$qsl$" + body + "$qsl$;"
    )
    op.execute("""
    CREATE FUNCTION backtest.assert_quant_oos(p_batch uuid,p_phase text,p_fold integer,
      p_candidate text,p_role text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE expected integer;winner text;
    BEGIN
      IF p_phase NOT IN ('test','holdout') THEN RETURN; END IF;
      SELECT jsonb_array_length(d.body->'protocol'->'folds')+1 INTO STRICT expected FROM
        backtest.experiment_batches b JOIN backtest.experiment_definitions d USING(definition_id)
        WHERE b.batch_id=p_batch;
      IF (SELECT count(*) FROM backtest.experiment_selections WHERE batch_id=p_batch)<>expected THEN
        RAISE EXCEPTION 'quant all selections required'; END IF;
      SELECT selected_candidate_id INTO STRICT winner FROM backtest.experiment_selections
        WHERE batch_id=p_batch AND scope=CASE WHEN p_phase='holdout' THEN 'final' ELSE
          p_fold::text END;
      IF p_role='SEARCH' AND p_candidate IS DISTINCT FROM winner THEN
        RAISE EXCEPTION 'quant selection conflict'; END IF;
    END $$;
    REVOKE ALL ON FUNCTION backtest.assert_quant_oos(uuid,text,integer,text,text) FROM PUBLIC;
    CREATE FUNCTION backtest.guard_quant_reliability() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,research,pg_temp AS $$
    DECLARE b backtest.experiment_batches;d backtest.experiment_definitions;r backtest.runs;
      p backtest.experiment_phase_bindings;s backtest.experiment_snapshots;ref jsonb;bound boolean;
      mode_status text;public_role text;semantic jsonb;
    BEGIN
      IF NEW.quant_batch_id IS NULL THEN RETURN NEW; END IF;
      PERFORM backtest.assert_quant_fence(NEW.quant_batch_id);
      SELECT * INTO STRICT b FROM backtest.experiment_batches WHERE batch_id=NEW.quant_batch_id;
      SELECT * INTO STRICT d FROM backtest.experiment_definitions WHERE
          definition_id=b.definition_id;
      SELECT * INTO STRICT r FROM backtest.runs WHERE run_id=NEW.run_id;
      SELECT * INTO STRICT s FROM backtest.experiment_snapshots WHERE batch_id=b.batch_id;
      SELECT * INTO p FROM backtest.experiment_phase_bindings WHERE batch_id=b.batch_id
        AND phase_key=NEW.quant_phase_key;
      PERFORM backtest.assert_quant_oos(b.batch_id,p.phase,p.fold_index,r.request->>'candidateId',
        r.request->>'role');
      public_role:=CASE r.request->>'role' WHEN 'SEARCH' THEN 'SEARCH_CANDIDATE'
        WHEN 'FLAT_REFERENCE' THEN 'REFERENCE_FLAT' ELSE 'REFERENCE_BUY_HOLD' END;
      mode_status:=CASE d.body->>'researchMode' WHEN 'STRICT_PIT' THEN
          'STRICT_PIT_EVIDENCE_COMPLETE'
        ELSE 'EXPLORATORY_WITH_LIMITATIONS' END;
      IF r.quant_batch_id IS DISTINCT FROM b.batch_id OR p.phase_key IS NULL
        OR r.request->>'phaseKey' IS DISTINCT FROM NEW.quant_phase_key
        OR NEW.phase<>'FINAL' OR NEW.assessment_id IS NOT NULL
        OR NEW.body->>'schemaVersion' IS DISTINCT FROM 'quant-lab-v1'
        OR NEW.body->>'version' IS DISTINCT FROM 'quant-reliability-report-v1'
        OR NEW.body ? 'reportHash' OR NEW.body->>'reportId' IS DISTINCT FROM NEW.report_id::text
        OR NEW.body->>'batchId' IS DISTINCT FROM b.batch_id::text
        OR NEW.body->>'runId' IS DISTINCT FROM r.run_id::text
        OR NEW.body->>'candidateId' IS DISTINCT FROM r.request->>'candidateId'
        OR NEW.body->>'instrumentId' IS DISTINCT FROM d.body->>'instrumentId'
        OR NEW.body->>'role' IS DISTINCT FROM public_role
        OR NEW.body->>'phase' IS DISTINCT FROM p.phase
        OR (NEW.body->>'fold')::integer IS DISTINCT FROM p.fold_index
        OR NEW.body->>'phaseRole' IS DISTINCT FROM (CASE p.phase WHEN 'training' THEN
          'IN_SAMPLE_ANALYSIS' WHEN 'validation' THEN 'PARAMETER_SELECTION' WHEN 'test' THEN
          'WALK_FORWARD_OOS' ELSE 'FINAL_HOLDOUT' END)
        OR NEW.body->>'researchMode' IS DISTINCT FROM d.body->>'researchMode'
        OR NEW.body->>'dataSelectionPolicyVersion' IS DISTINCT FROM
          d.body->>'dataSelectionPolicyVersion'
        OR NEW.body->>'strategySelectionPolicyHash' IS DISTINCT FROM d.policy_hash
        OR (NEW.body->'snapshotReference'->>'snapshotVersion')::bigint IS DISTINCT FROM
          b.snapshot_version
        OR (NEW.body->'snapshotReference'->>'snapshotAt')::timestamptz IS DISTINCT FROM
          b.snapshot_at
        OR NEW.body->'methods'->>'manifestHash' IS DISTINCT FROM s.fingerprint
        OR NEW.body->'methods'->'protocol' IS DISTINCT FROM d.body->'protocol'
        OR NEW.body->'methods'->>'experimentVersion' IS DISTINCT FROM 'quant-experiment-v1'
        OR NEW.body->'methods'->>'simulatorVersion' IS DISTINCT FROM
          s.canonical_text::jsonb->1->>'simulator_version'
        OR NEW.body->'methods'->>'metricsVersion' IS DISTINCT FROM 'quant-metrics-v1'
        OR NEW.body->'methods'->>'codeVersion' IS DISTINCT FROM b.code_version
        OR NEW.body->'methods'->>'qualityPolicyVersion' IS DISTINCT FROM 'quant-reliability-v1'
        OR NEW.body->>'protocolHash' IS DISTINCT FROM
          encode(sha256(convert_to(backtest.quant_canonical(
          jsonb_build_array('quant-research-protocol-v1','NOT_TRAINABLE',d.canonical_text::jsonb->1->'protocol')),
            'UTF8')),'hex')
        OR NEW.body->>'evidenceStatus' IS NULL
        OR NEW.body->>'evidenceStatus' NOT IN (mode_status,'BLOCKED','INCOMPLETE')
        OR NEW.body->>'overallStatus' IS DISTINCT FROM (CASE WHEN NEW.body->>'evidenceStatus'
          IN ('BLOCKED','INCOMPLETE') THEN 'BLOCKED' ELSE 'EXPLORATORY_WITH_LIMITATIONS' END)
        OR NEW.body->>'rating' IS NULL OR NEW.body->>'rating' NOT IN
          ('PASS','LIMITED','UNKNOWN','BLOCKED','NOT_EVALUATED')
        OR NOT(NEW.body->'resultLimitations' ? 'NO_TRADING_AUTHORIZATION')
        OR jsonb_typeof(NEW.body->'resultLimitations') IS DISTINCT FROM 'array'
        THEN RAISE EXCEPTION 'quant reliability binding conflict'; END IF;
      IF d.body->>'researchMode'='RETROSPECTIVE_EXPLORATORY' AND (
        NEW.body->>'historicalAvailabilityStatus' IS DISTINCT FROM 'NOT_PROVEN'
        OR NOT(NEW.body->'resultLimitations' ?& ARRAY['NOT_STRICT_PIT','EX_POST_REVISIONS'])) THEN
          RAISE EXCEPTION 'quant reliability mode conflict'; END IF;
      IF NEW.body->>'evidenceStatus'='STRICT_PIT_EVIDENCE_COMPLETE' AND (
        NEW.body->>'rating' IS DISTINCT FROM 'PASS' OR
        NEW.body->>'historicalAvailabilityStatus' IS DISTINCT FROM 'VERIFIED_FOR_DECISION_CUTOFFS'
        OR NEW.body->'dimensions'->'HISTORICAL_AVAILABILITY'->>'rating' IS DISTINCT FROM 'PASS'
        OR NEW.body->'resultLimitations' ?| ARRAY['NOT_STRICT_PIT','EX_POST_REVISIONS']) THEN
          RAISE EXCEPTION 'quant reliability mode conflict'; END IF;
      IF NEW.canonical_text IS DISTINCT FROM backtest.quant_canonical(NEW.canonical_text::jsonb)
          THEN
        RAISE EXCEPTION 'noncanonical quant identity'; END IF;
      semantic:=jsonb_build_object('report',NEW.body-ARRAY['reportHash','reportId','batchId','runId'],
        'findings',NEW.findings,'revisionReferences',NEW.revision_references);
      IF NEW.quant_report_hash IS DISTINCT FROM
          encode(sha256(convert_to(backtest.quant_canonical(semantic),
        'UTF8')),'hex') THEN RAISE EXCEPTION 'quant reliability hash conflict'; END IF;
      FOR ref IN SELECT value FROM jsonb_array_elements(NEW.revision_references) LOOP
        SELECT EXISTS(SELECT FROM backtest.experiment_revision_bindings rb
          LEFT JOIN research.evidence_revisions e ON e.evidence_id=rb.evidence_id
          WHERE rb.batch_id=b.batch_id AND rb.reference_id::text=ref->>'referenceId'
            AND rb.expected_fingerprint=ref->>'fingerprint' AND ref->>'kind'=CASE rb.kind
              WHEN 'COVERAGE' THEN 'CORPORATE_ACTION' WHEN 'EVIDENCE' THEN e.kind ELSE rb.kind END)
          INTO bound;
        IF bound IS NOT TRUE THEN RAISE EXCEPTION 'quant reliability reference conflict'; END IF;
      END LOOP;
      IF EXISTS(SELECT FROM unnest(p.seed_ids) id WHERE NOT EXISTS(SELECT FROM
        jsonb_array_elements(NEW.revision_references) AS entry(value) WHERE
          entry.value->>'kind'='BAR'
          AND entry.value->>'referenceId'=id::text)) OR EXISTS(SELECT FROM
        jsonb_array_elements_text(p.body->'outcomeRevisionIds') id WHERE NOT EXISTS(SELECT FROM
          jsonb_array_elements(NEW.revision_references) AS entry(value) WHERE
          entry.value->>'kind'='BAR'
            AND entry.value->>'referenceId'=id)) OR NOT EXISTS(SELECT FROM
        jsonb_array_elements(NEW.revision_references) AS entry(value) WHERE
          entry.value->>'kind'='CALENDAR') THEN
          RAISE EXCEPTION 'quant reliability reference conflict'; END IF;
      IF NEW.body->>'evidenceStatus'=mode_status AND backtest.current_quant_gate(b.batch_id) IS
          NOT NULL
        THEN RAISE EXCEPTION 'quant current gate blocked'; END IF;
      UPDATE backtest.experiment_batches SET publication_bytes=publication_bytes+
        octet_length(NEW.canonical_text),updated_at=clock_timestamp() WHERE batch_id=b.batch_id;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION backtest.guard_quant_reliability() FROM PUBLIC;
    CREATE TRIGGER a_quant_reliability BEFORE INSERT ON backtest.retrospective_reports
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_quant_reliability();
    """)


def _current_gate():
    """Reuse the installed 0034 negative-fact algorithm without fake assessments.

    Only its three binding projections change. The immutable historical function
    and all published migrations remain byte-identical. Fail closed on drift.
    """
    body = (
        op.get_bind()
        .execute(
            text(
                "SELECT prosrc FROM pg_proc WHERE oid="
                "'backtest.current_retrospective_gate(uuid)'::regprocedure"
            )
        )
        .scalar_one()
    )
    old_periods = """SELECT body->'folds' INTO periods FROM backtest.retrospective_reports
        WHERE assessment_id=p_assessment AND phase='PREVIEW';"""
    new_periods = """SELECT jsonb_agg(jsonb_build_object('window',
        jsonb_build_object('testEnd',p.end_at),
        'warmupPeriod',jsonb_build_object('start',coalesce((SELECT min(x.bar_start)
          FROM research.bar_revisions x WHERE x.revision_id=ANY(p.seed_ids)),p.start_at))))
        INTO periods FROM backtest.experiment_phase_bindings p WHERE p.batch_id=p_assessment;"""
    old_instrument = """SELECT (request->>'instrumentId')::uuid
              FROM backtest.retrospective_assessments WHERE assessment_id=p_assessment"""
    new_instrument = """SELECT (d.body->>'instrumentId')::uuid
              FROM backtest.experiment_batches b JOIN backtest.experiment_definitions d
                USING(definition_id) WHERE b.batch_id=p_assessment"""
    old_bindings = """backtest.retrospective_revision_bindings
            WHERE assessment_id=p_assessment"""
    new_bindings = """backtest.experiment_revision_bindings
            WHERE batch_id=p_assessment"""
    for old, new in (
        (old_periods, new_periods),
        (old_instrument, new_instrument),
        (old_bindings, new_bindings),
    ):
        if body.count(old) != 1:
            raise RuntimeError("0034 current negative-gate definition drift")
        body = body.replace(old, new)
    op.execute(
        "CREATE FUNCTION backtest.current_quant_gate(p_assessment uuid) RETURNS text "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,research,pg_temp "
        "AS $qsl$" + body + "$qsl$;"
    )
    op.execute("REVOKE ALL ON FUNCTION backtest.current_quant_gate(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION backtest.current_quant_gate(uuid) TO atl_backtest_worker")


def _selection_guards():
    op.execute("""
    CREATE FUNCTION backtest.require_complete_selection_set() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    BEGIN
      IF (SELECT count(*) FROM backtest.experiment_selections WHERE batch_id=NEW.batch_id)
        IS DISTINCT FROM (SELECT jsonb_array_length(d.body->'protocol'->'folds')+1 FROM
          backtest.experiment_batches b JOIN backtest.experiment_definitions d USING(definition_id)
          WHERE b.batch_id=NEW.batch_id) THEN RAISE EXCEPTION
          'quant atomic selection set required'; END IF;
      RETURN NULL;
    END $$;
    REVOKE ALL ON FUNCTION backtest.require_complete_selection_set() FROM PUBLIC;
    CREATE CONSTRAINT TRIGGER complete_quant_selections AFTER INSERT ON
          backtest.experiment_selections
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION
          backtest.require_complete_selection_set();
    CREATE FUNCTION backtest.quant_decimal38(p numeric) RETURNS numeric LANGUAGE plpgsql IMMUTABLE
      SET search_path=pg_catalog,pg_temp AS $$
    DECLARE whole text;fraction text;places integer;unit numeric;rounded numeric;remainder numeric;
    BEGIN
      IF p=0 THEN RETURN 0; END IF;
      whole:=split_part(abs(p)::text,'.',1);
      fraction:=split_part(abs(p)::text,'.',2);
      places:=CASE WHEN whole='0' THEN 38+length(fraction)-length(ltrim(fraction,'0'))
        ELSE 38-length(whole) END;
      rounded:=trunc(p,places);
      unit:=(CASE WHEN places>0 THEN '0.'||repeat('0',places-1)||'1'
        ELSE '1'||repeat('0',-places) END)::numeric;
      remainder:=abs(p-rounded);
      IF remainder>unit*0.5 OR (remainder=unit*0.5 AND mod(abs(rounded)/unit,2)=1) THEN
        RETURN rounded+sign(p)*unit; END IF;
      RETURN rounded;
    END $$;
    REVOKE ALL ON FUNCTION backtest.quant_decimal38(numeric) FROM PUBLIC;
    CREATE FUNCTION backtest.check_quant_selection(p_batch uuid,p_scope text,p_body jsonb)
      RETURNS void LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE d backtest.experiment_definitions;policy jsonb;c jsonb;v jsonb;g jsonb;vals jsonb;
      bound jsonb;expected_folds integer;eligible boolean;pass boolean;expected_status text;
      threshold jsonb;measured jsonb;gate_name text;ids text[];candidate_ids text[];
      fold_ids integer[];winner text;metric text;med numeric;arr numeric[];count_values integer;
    BEGIN
      SELECT x.* INTO STRICT d FROM backtest.experiment_batches b
        JOIN backtest.experiment_definitions x USING(definition_id) WHERE b.batch_id=p_batch;
      policy:=d.body->'strategySelectionPolicy';
      expected_folds:=CASE WHEN p_scope='final' THEN jsonb_array_length(d.body->'protocol'->'folds')
        ELSE 1 END;
      SELECT array_agg(candidate_id ORDER BY candidate_id COLLATE "C") INTO candidate_ids
        FROM backtest.experiment_candidates WHERE definition_id=d.definition_id AND role='SEARCH';
      SELECT array_agg(item->>'candidateId' ORDER BY (item->>'candidateId') COLLATE "C") INTO ids
        FROM jsonb_array_elements(p_body->'candidates') item;
      IF ids IS DISTINCT FROM candidate_ids OR p_body->'candidateIds' IS DISTINCT FROM
          to_jsonb(candidate_ids)
        OR (p_body->>'candidateCount')::integer IS DISTINCT FROM d.candidate_count THEN
          RAISE EXCEPTION 'quant candidate conflict'; END IF;
      FOR c IN SELECT value FROM jsonb_array_elements(p_body->'candidates') LOOP
        eligible:=true;
        IF jsonb_array_length(c->'validationValues') IS DISTINCT FROM expected_folds THEN
          RAISE EXCEPTION 'quant selection gates required'; END IF;
        SELECT array_agg((x->>'fold')::integer ORDER BY (x->>'fold')::integer) INTO fold_ids
          FROM jsonb_array_elements(c->'validationValues') x;
        IF fold_ids IS DISTINCT FROM (CASE WHEN p_scope='final' THEN ARRAY(
          SELECT generate_series(0,expected_folds-1)) ELSE ARRAY[p_scope::integer] END) THEN
            RAISE EXCEPTION 'quant selection gates required'; END IF;
        FOR v IN SELECT value FROM jsonb_array_elements(c->'validationValues') LOOP
          vals:=v->'selectionValues';
          IF jsonb_typeof(vals) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'quant invalid selection values'; END IF;
          FOREACH metric IN ARRAY ARRAY['netReturn','maxDrawdown','turnover','investedBarRatio']
          LOOP
            IF jsonb_typeof(vals->metric) IS DISTINCT FROM 'string' OR
              vals->>metric !~ '^-?(0|[1-9][0-9]*)([.][0-9]+)?$' OR length(vals->>metric)>80
              OR backtest.quant_decimal38((vals->>metric)::numeric) IS DISTINCT FROM
                (vals->>metric)::numeric THEN
                RAISE EXCEPTION 'quant invalid selection values'; END IF;
          END LOOP;
          FOREACH metric IN ARRAY ARRAY['entryCount','completeValidationSessions'] LOOP
            IF jsonb_typeof(vals->metric) IS DISTINCT FROM 'number' OR
              vals->>metric !~ '^[0-9]+$' OR (vals->>metric)::numeric NOT BETWEEN 0 AND 100000 THEN
                RAISE EXCEPTION 'quant invalid selection values'; END IF;
          END LOOP;
          IF (vals->>'netReturn')::numeric<=-1 OR
            (vals->>'maxDrawdown')::numeric NOT BETWEEN 0 AND 1 OR
            (vals->>'investedBarRatio')::numeric NOT BETWEEN 0 AND 1 OR
            (vals->>'turnover')::numeric<0 THEN
              RAISE EXCEPTION 'quant invalid selection values'; END IF;
          SELECT body INTO bound FROM backtest.experiment_result_links WHERE batch_id=p_batch
            AND candidate_id=c->>'candidateId' AND role='SEARCH' AND status='COMPLETE'
            AND phase_key=(v->>'fold')||chr(58)||'validation';
          IF bound IS NULL OR vals IS DISTINCT FROM bound->'selectionValues'
            OR NOT(vals ?& ARRAY['netReturn','maxDrawdown','turnover','investedBarRatio',
              'entryCount',
              'completeValidationSessions']) OR bound->'dataComplete' IS DISTINCT FROM 'true'::jsonb
            OR bound->'requiredMetricsValid' IS DISTINCT FROM 'true'::jsonb
            OR bound->>'reliabilityStatus' IS NULL
            OR bound->>'reliabilityStatus' NOT IN
          ('STRICT_PIT_EVIDENCE_COMPLETE','EXPLORATORY_WITH_LIMITATIONS')
            THEN RAISE EXCEPTION 'quant incomplete validation'; END IF;
          IF jsonb_array_length(v->'gates') IS DISTINCT FROM 7 OR
            (SELECT count(DISTINCT x->>'gate') FROM jsonb_array_elements(v->'gates')x)<>7 THEN
              RAISE EXCEPTION 'quant selection gates required'; END IF;
          FOREACH gate_name IN ARRAY ARRAY['COMPLETE_DATA','RELIABILITY','VALID_REQUIRED_METRICS',
            'MINIMUM_VALIDATION_SESSIONS','MAX_DRAWDOWN','MINIMUM_ENTRY_COUNT',
            'MINIMUM_INVESTED_BAR_RATIO'] LOOP
            SELECT value INTO g FROM jsonb_array_elements(v->'gates') WHERE
          value->>'gate'=gate_name;
            threshold:='null'::jsonb;measured:='null'::jsonb;pass:=true;expected_status:='PASSED';
            IF gate_name='COMPLETE_DATA' THEN threshold:='"1"'::jsonb;measured:='"1"'::jsonb;
            ELSIF gate_name='MINIMUM_VALIDATION_SESSIONS' THEN
              threshold:=policy->'minValidationSessions';measured:=vals->'completeValidationSessions';
              pass:=(measured#>>'{}')::integer>=(threshold#>>'{}')::integer;
            ELSIF gate_name='MAX_DRAWDOWN' THEN
              threshold:=policy->'maxDrawdown';measured:=vals->'maxDrawdown';
              pass:=(measured#>>'{}')::numeric<=(threshold#>>'{}')::numeric;
            ELSIF gate_name='MINIMUM_ENTRY_COUNT' THEN
              threshold:=policy->'minimumEntryCount';
              IF threshold<>'null'::jsonb THEN measured:=vals->'entryCount';
                pass:=(measured#>>'{}')::integer>=(threshold#>>'{}')::integer;
              ELSE expected_status:='DISABLED'; END IF;
            ELSIF gate_name='MINIMUM_INVESTED_BAR_RATIO' THEN
              threshold:=policy->'minimumInvestedBarRatio';
              IF threshold<>'null'::jsonb THEN measured:=vals->'investedBarRatio';
                pass:=(measured#>>'{}')::numeric>=(threshold#>>'{}')::numeric;
              ELSE expected_status:='DISABLED'; END IF;
            END IF;
            IF pass IS NOT TRUE THEN expected_status:='FAILED';eligible:=false; END IF;
            IF g IS NULL OR g->>'status' IS DISTINCT FROM expected_status OR g->'threshold'
              IS DISTINCT FROM threshold OR g->'value' IS DISTINCT FROM measured THEN
                RAISE EXCEPTION 'quant selection gates required'; END IF;
          END LOOP;
        END LOOP;
        IF c->>'eligibility' IS DISTINCT FROM (CASE WHEN eligible THEN 'ELIGIBLE' ELSE
          'INELIGIBLE' END)
          THEN RAISE EXCEPTION 'quant selection gates required'; END IF;
        FOREACH metric IN ARRAY ARRAY['netReturn','maxDrawdown','turnover','investedBarRatio']
          LOOP
            IF jsonb_typeof(c->'medianSelectionValues'->metric) IS DISTINCT FROM 'string' OR
              c->'medianSelectionValues'->>metric !~ '^-?(0|[1-9][0-9]*)([.][0-9]+)?$' THEN
                RAISE EXCEPTION 'quant selection medians conflict'; END IF;
            SELECT array_agg((x->'selectionValues'->>metric)::numeric ORDER BY
              (x->'selectionValues'->>metric)::numeric) INTO arr
              FROM jsonb_array_elements(c->'validationValues') x;
            count_values:=cardinality(arr);
            med:=CASE WHEN count_values%2=1 THEN arr[(count_values+1)/2] ELSE
              backtest.quant_decimal38(backtest.quant_decimal38(
                arr[count_values/2]+arr[count_values/2+1])*0.5) END;
            IF (c->'medianSelectionValues'->>metric)::numeric IS DISTINCT FROM med THEN
              RAISE EXCEPTION 'quant selection medians conflict'; END IF;
          END LOOP;
        IF eligible THEN
          FOREACH metric IN ARRAY ARRAY['primary','secondary','turnover'] LOOP
            IF jsonb_typeof(c->'rankingKey'->metric) IS DISTINCT FROM 'string' OR
              c->'rankingKey'->>metric !~ '^-?(0|[1-9][0-9]*)([.][0-9]+)?$' THEN
                RAISE EXCEPTION 'quant ranking conflict'; END IF;
          END LOOP;
          IF (c->'rankingKey'->>'primary')::numeric IS DISTINCT FROM (CASE
            WHEN policy->>'policyId'='MAX_NET_RETURN_WITH_DRAWDOWN_CONSTRAINT_V1'
              THEN -(c->'medianSelectionValues'->>'netReturn')::numeric
            ELSE (c->'medianSelectionValues'->>'maxDrawdown')::numeric END)
            OR (c->'rankingKey'->>'secondary')::numeric IS DISTINCT FROM (CASE
            WHEN policy->>'policyId'='MAX_NET_RETURN_WITH_DRAWDOWN_CONSTRAINT_V1'
              THEN (c->'medianSelectionValues'->>'maxDrawdown')::numeric
            ELSE -(c->'medianSelectionValues'->>'netReturn')::numeric END)
            OR (c->'rankingKey'->>'turnover')::numeric IS DISTINCT FROM
              (c->'medianSelectionValues'->>'turnover')::numeric
            OR c->'rankingKey'->>'candidateId' IS DISTINCT FROM c->>'candidateId'
            THEN RAISE EXCEPTION 'quant ranking conflict'; END IF;
        ELSIF c->'rankingKey' IS DISTINCT FROM 'null'::jsonb OR
          c->'rank' IS DISTINCT FROM 'null'::jsonb THEN RAISE EXCEPTION 'quant ranking conflict';
        END IF;
      END LOOP;
      SELECT x->>'candidateId' INTO winner FROM jsonb_array_elements(p_body->'candidates') x
        WHERE x->>'eligibility'='ELIGIBLE' ORDER BY (x->'rankingKey'->>'primary')::numeric,
          (x->'rankingKey'->>'secondary')::numeric,(x->'rankingKey'->>'turnover')::numeric,
          (x->>'candidateId') COLLATE "C" LIMIT 1;
      IF p_body->>'selectedCandidateId' IS DISTINCT FROM winner OR p_body->>'status' IS DISTINCT
          FROM
        (CASE WHEN winner IS NULL THEN 'NO_ELIGIBLE_CANDIDATE' ELSE 'SELECTED' END) THEN
          RAISE EXCEPTION 'quant ranking conflict'; END IF;
      IF (p_body->>'eligibleCount')::integer IS DISTINCT FROM (SELECT count(*) FROM
        jsonb_array_elements(p_body->'candidates') x WHERE x->>'eligibility'='ELIGIBLE') OR EXISTS(
        SELECT FROM (SELECT x,row_number() OVER(ORDER BY (x->'rankingKey'->>'primary')::numeric,
          (x->'rankingKey'->>'secondary')::numeric,(x->'rankingKey'->>'turnover')::numeric,
          (x->>'candidateId') COLLATE "C") AS expected_rank
          FROM jsonb_array_elements(p_body->'candidates') x WHERE x->>'eligibility'='ELIGIBLE')
          ranks
        WHERE (x->>'rank')::integer IS DISTINCT FROM expected_rank) THEN
          RAISE EXCEPTION 'quant ranking conflict'; END IF;
    END $$;
    REVOKE ALL ON FUNCTION backtest.check_quant_selection(uuid,text,jsonb) FROM PUBLIC;
    """)


def _permissions():
    op.execute("""
    CREATE FUNCTION backtest.guard_quant_canonical() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    BEGIN
      IF NEW.canonical_text IS DISTINCT FROM backtest.quant_canonical(NEW.canonical_text::jsonb)
        THEN RAISE EXCEPTION 'noncanonical quant identity'; END IF;
      IF TG_TABLE_NAME='experiment_definitions' AND to_jsonb(NEW)->>'policy_canonical'
        IS DISTINCT FROM backtest.quant_canonical((to_jsonb(NEW)->>'policy_canonical')::jsonb)
        THEN RAISE EXCEPTION 'noncanonical quant identity'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION backtest.guard_quant_canonical() FROM PUBLIC;
    """)
    op.execute("""
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.quant_strategy_revisions
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    REVOKE ALL ON backtest.quant_strategy_revisions FROM PUBLIC;
    GRANT SELECT ON backtest.quant_strategy_revisions TO atl_control_api,atl_backtest_worker;
    DO $$ BEGIN IF current_database()='lab_paper_runtime' THEN
      REVOKE ALL ON backtest.quant_strategy_revisions FROM atl_control_api;
      GRANT SELECT ON backtest.quant_strategy_revisions TO atl_paper_control_api;
    END IF; END $$;
    """)
    tables = (
        "experiment_definitions",
        "experiment_validations",
        "experiment_candidates",
        "experiment_snapshots",
        "experiment_revision_bindings",
        "experiment_phase_bindings",
        "experiment_selections",
        "experiment_result_links",
        "experiment_reports",
        "experiment_events",
        "test_exposures",
        "experiment_source_receipts",
        "experiment_trust_bindings",
    )
    for table in tables:
        op.execute(f"""CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.{table}
          FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
          REVOKE ALL ON backtest.{table} FROM PUBLIC;
          GRANT SELECT ON backtest.{table} TO atl_control_api,atl_backtest_worker;
          DO $$ BEGIN IF current_database()='lab_paper_runtime' THEN
            REVOKE ALL ON backtest.{table} FROM atl_control_api;
            GRANT SELECT ON backtest.{table} TO atl_paper_control_api;
          END IF; END $$;""")
        if table not in (
            "experiment_events",
            "test_exposures",
            "experiment_revision_bindings",
            "experiment_source_receipts",
            "experiment_trust_bindings",
        ):
            op.execute(
                f"CREATE TRIGGER a_canonical BEFORE INSERT ON backtest.{table} FOR EACH ROW "
                "EXECUTE FUNCTION backtest.guard_quant_canonical()"
            )
    op.execute("""
    REVOKE INSERT ON backtest.readiness_jobs FROM atl_backtest_worker;
    REVOKE ALL ON backtest.experiment_batches FROM PUBLIC;
    GRANT SELECT ON backtest.experiment_batches TO atl_control_api,atl_backtest_worker;
    GRANT UPDATE(status,owner,fence,attempts,lease_until,started_at,error_code,bar_steps,updated_at)
      ON backtest.experiment_batches TO atl_backtest_worker;
    GRANT INSERT ON backtest.experiment_snapshots,backtest.experiment_revision_bindings,
      backtest.experiment_phase_bindings,backtest.experiment_selections,
      backtest.experiment_result_links,backtest.experiment_reports TO atl_backtest_worker;
    DO $$ BEGIN IF current_database()='lab_paper_runtime' THEN
      REVOKE ALL ON backtest.experiment_batches FROM atl_control_api;
      GRANT SELECT ON backtest.experiment_batches TO atl_paper_control_api;
    END IF; END $$;
    """)
    control = (
        "request_run(uuid,text,text,jsonb,text,text,text)",
        "request_readiness(uuid,text,text,jsonb,text,text,text,text)",
        "request_retrospective_assessment(uuid,text,text,jsonb,text,text,text)",
        "request_retrospective_run(uuid,text,text,jsonb,text,text,text)",
        "request_experiment_definition(uuid,text,jsonb,text,text,text,text,jsonb)",
        "validate_experiment(uuid,jsonb,text,text)",
        "request_experiment_batch(uuid,uuid,integer,text,uuid,text,text,text,text,text)",
    )
    internal = (
        "active_slots()",
        "assert_quant_fence(uuid)",
        "guard_quant_batch()",
        "quant_batch_event()",
        "guard_quant_content()",
        "assert_quant_complete(uuid,boolean)",
        "guard_quant_child()",
        "guard_quant_run_content()",
    )
    for signature in (*control, *internal):
        op.execute(f"REVOKE ALL ON FUNCTION backtest.{signature} FROM PUBLIC")
    for signature in control:
        op.execute(f"""GRANT EXECUTE ON FUNCTION backtest.{signature} TO atl_control_api;
          DO $$ BEGIN IF current_database()='lab_paper_runtime' THEN
            REVOKE EXECUTE ON FUNCTION backtest.{signature} FROM atl_control_api;
            GRANT EXECUTE ON FUNCTION backtest.{signature} TO atl_paper_control_api;
          END IF; END $$;""")
    op.execute("GRANT EXECUTE ON FUNCTION backtest.assert_quant_fence(uuid) TO atl_backtest_worker")


def downgrade():
    raise RuntimeError(
        "Stop and drain all research workers; preserve immutable quant research evidence"
    )
