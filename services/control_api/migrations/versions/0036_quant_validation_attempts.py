"""Bind feasibility replies to their request key without broadening admission."""

from alembic import op
from sqlalchemy import text

revision = "0036_quant_validation_attempts"
down_revision = "0035_quant_lab"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE backtest.experiment_validation_requests (
      definition_id uuid NOT NULL REFERENCES backtest.experiment_definitions,
      key_hash text NOT NULL CHECK(key_hash ~ '^[0-9a-f]{64}$'),
      validation_id uuid NOT NULL,
      validated_at timestamptz NOT NULL,
      valid boolean NOT NULL,
      approval_id uuid GENERATED ALWAYS AS (CASE WHEN valid THEN validation_id END) STORED
        REFERENCES backtest.experiment_validations(validation_id),
      fingerprint text CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      body jsonb NOT NULL,
      PRIMARY KEY(definition_id,key_hash),
      CHECK(valid=(fingerprint IS NULL)));
    CREATE UNIQUE INDEX experiment_invalid_validation_id ON backtest.experiment_validation_requests
      (validation_id) WHERE NOT valid;
    CREATE INDEX experiment_invalid_validation_order ON backtest.experiment_validation_requests
      (definition_id,validated_at DESC,validation_id DESC) WHERE NOT valid;
    CREATE INDEX experiment_validation_approval_replay ON backtest.experiment_validation_requests
      (approval_id,validated_at,key_hash) WHERE valid;
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.experiment_validation_requests
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();

    CREATE FUNCTION backtest.record_experiment_validation_request(p_definition uuid,
      p_key_hash text,p_body jsonb,p_valid boolean) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,backtest,pg_temp AS $fn$
    DECLARE expected_hash text; canonical text; digest text; identifier uuid; recorded_at
      timestamptz; approval backtest.experiment_validations; reply jsonb; history jsonb;
    BEGIN
      IF current_setting('backtest.quant_version',true) IS DISTINCT FROM 'quant-experiment-v1'
        OR current_setting('transaction_isolation')<>'read committed' OR p_valid IS NULL
        THEN RAISE EXCEPTION 'invalid quant validation'; END IF;
      SELECT fingerprint INTO STRICT expected_hash FROM backtest.experiment_definitions
        WHERE definition_id=p_definition;
      IF p_body->>'definitionHash' IS DISTINCT FROM expected_hash
        THEN RAISE EXCEPTION 'quant definition conflict'; END IF;

      PERFORM pg_advisory_xact_lock(894217345);
      SELECT validation_id INTO identifier FROM backtest.experiment_validation_requests
        WHERE definition_id=p_definition AND key_hash=p_key_hash;
      IF FOUND THEN RETURN identifier; END IF;

      history:=p_body->'researchHistory';
      IF jsonb_typeof(p_body->'reasons') IS DISTINCT FROM 'array'
        OR jsonb_typeof(p_body->'validatedAt') IS DISTINCT FROM 'string'
        OR jsonb_typeof(history) IS DISTINCT FROM 'object'
        OR history->>'schemaVersion' IS DISTINCT FROM 'quant-lab-v1'
        OR history->>'registryVersion' IS DISTINCT FROM 'quant-exposure-v1'
        OR coalesce(history->>'registryHash','') !~ '^[0-9a-f]{64}$'
        OR jsonb_typeof(history->'boundAt') IS DISTINCT FROM 'string'
        OR jsonb_typeof(history->'overlaps') IS DISTINCT FROM 'array'
        OR jsonb_typeof(history->'overlapCount') IS DISTINCT FROM 'number'
        OR NOT (history ?& ARRAY['registeredRoundCount','priorFailedRoundCount',
          'priorBlockedRoundCount','supersededRoundCount','repeatedValidationCount',
          'reuseStatus','externalKnowledgeStatus','candidateCounts','referenceCounts',
          'nextOverlapCursor','warmupOverlapCount','limitations'])
        OR jsonb_typeof(p_body->'registryWatermark') IS DISTINCT FROM 'number'
        OR jsonb_typeof(p_body->'calendarVerified') IS DISTINCT FROM 'boolean'
        OR NOT (p_body ?& ARRAY['simulationBarSteps','snapshotRows','publicationBytes','seedBars'])
        THEN RAISE EXCEPTION 'invalid quant validation'; END IF;
      canonical:=backtest.quant_canonical(p_body);
      IF octet_length(canonical)>12582912 OR jsonb_array_length(p_body->'reasons')>32
        OR (jsonb_array_length(p_body->'reasons')=0) IS DISTINCT FROM p_valid
        OR EXISTS(SELECT FROM jsonb_array_elements(p_body->'reasons') reason
          WHERE jsonb_typeof(reason)<>'string' OR reason #>> '{}' NOT IN
            ('MISSING_CALENDAR','BUDGET_EXCEEDED','DATA_UNAVAILABLE','SNAPSHOT_UNAVAILABLE',
             'RELIABILITY_BLOCKED'))
        OR EXISTS(SELECT FROM (VALUES ('simulationBarSteps',100000),('snapshotRows',100000),
          ('publicationBytes',12582912)) AS bound(key,maximum)
          WHERE p_body->bound.key<>'null'::jsonb AND CASE
            WHEN jsonb_typeof(p_body->bound.key)='number'
              AND p_body->>bound.key ~ '^(0|[1-9][0-9]*)$'
            THEN (p_body->>bound.key)::numeric>bound.maximum ELSE true END)
        OR jsonb_array_length(history->'overlaps')>200
        OR history->>'overlapCount' !~ '^(0|[1-9][0-9]*)$'
        OR (history->>'overlapCount')::numeric < jsonb_array_length(history->'overlaps')
        THEN RAISE EXCEPTION 'invalid quant validation'; END IF;
      digest:=encode(sha256(convert_to(canonical,'UTF8')),'hex');
      recorded_at:=(p_body->>'validatedAt')::timestamptz;
      reply:=p_body;
      IF p_valid THEN
        SELECT * INTO approval FROM backtest.experiment_validations
          WHERE definition_id=p_definition;
        IF FOUND THEN
          identifier:=approval.validation_id;
          SELECT r.body INTO reply FROM backtest.experiment_validation_requests r
            WHERE r.approval_id=identifier AND r.valid ORDER BY r.validated_at,r.key_hash LIMIT 1;
          IF NOT FOUND THEN
            reply:=p_body||jsonb_build_object('calendarVerified',approval.body->'calendarVerified',
              'simulationBarSteps',approval.body->'simulationBarSteps',
              'snapshotRows',approval.body->'snapshotRows',
              'publicationBytes',approval.body->'publicationBytes',
              'seedBars',approval.body->'seedBars');
          END IF;
          recorded_at:=(reply->>'validatedAt')::timestamptz;
        ELSE
          identifier:=backtest.validate_experiment(p_definition,p_body,canonical,digest);
        END IF;
      ELSE
        identifier:=gen_random_uuid();
      END IF;
      INSERT INTO backtest.experiment_validation_requests
        (definition_id,key_hash,validation_id,validated_at,valid,fingerprint,body)
        VALUES(p_definition,p_key_hash,identifier,recorded_at,p_valid,
          CASE WHEN NOT p_valid THEN digest END,reply);
      RETURN identifier;
    END $fn$;
    REVOKE ALL ON backtest.experiment_validation_requests FROM PUBLIC;
    REVOKE ALL ON FUNCTION backtest.record_experiment_validation_request(uuid,text,jsonb,boolean)
      FROM PUBLIC;
    DO $$ BEGIN IF current_database()='lab_paper_runtime' THEN
      GRANT SELECT ON backtest.experiment_validation_requests TO atl_paper_control_api;
      GRANT EXECUTE ON FUNCTION backtest.current_quant_gate(uuid) TO atl_paper_control_api;
      GRANT EXECUTE ON FUNCTION
        backtest.record_experiment_validation_request(uuid,text,jsonb,boolean)
        TO atl_paper_control_api;
    ELSE
      GRANT SELECT ON backtest.experiment_validation_requests TO atl_control_api;
      GRANT EXECUTE ON FUNCTION backtest.current_quant_gate(uuid) TO atl_control_api;
      GRANT EXECUTE ON FUNCTION
        backtest.record_experiment_validation_request(uuid,text,jsonb,boolean) TO atl_control_api;
    END IF; END $$;
    """)
    _bind_selection_evidence()


def _bind_selection_evidence():
    op.execute("""
    CREATE FUNCTION backtest.quant_selection_evidence(p_batch uuid,p_phase text) RETURNS text
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,research,pg_temp AS $$
    DECLARE binding backtest.experiment_phase_bindings; manifest jsonb; decisions jsonb;
    BEGIN
      PERFORM backtest.assert_quant_fence(p_batch);
      SELECT * INTO STRICT binding FROM backtest.experiment_phase_bindings
        WHERE batch_id=p_batch AND phase_key=p_phase AND phase='validation';
      SELECT canonical_text::jsonb->1 INTO STRICT manifest FROM backtest.experiment_snapshots
        WHERE batch_id=p_batch;
      SELECT jsonb_agg(jsonb_build_array(bar.revision_id,bar.fingerprint) ORDER BY
        bar.bar_start,bar.revision_id) INTO decisions
      FROM jsonb_array_elements(manifest->'decision_data') item
      JOIN research.bar_revisions bar ON bar.revision_id=(item->>'revision_id')::uuid
      WHERE bar.bar_start>=binding.start_at AND bar.bar_start<binding.end_at
        AND (manifest->'definition'->>'research_mode'<>'STRICT_PIT' OR
          (item->>'cutoff')::timestamptz=bar.bar_end+interval '60 seconds');
      IF decisions IS NULL THEN RAISE EXCEPTION 'quant selection evidence unavailable'; END IF;
      RETURN encode(sha256(convert_to(backtest.quant_canonical(jsonb_build_array(
        'quant-validation-evidence-v1',binding.fingerprint,manifest->>'code_version',decisions)),
        'UTF8')),'hex');
    END $$;
    REVOKE ALL ON FUNCTION backtest.quant_selection_evidence(uuid,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION backtest.quant_selection_evidence(uuid,text) TO atl_backtest_worker;
    """)
    connection = op.get_bind()
    source = connection.execute(
        text("SELECT pg_get_functiondef('backtest.guard_quant_content()'::regprocedure)")
    ).scalar_one()
    identity = "(identity->'reliability')-ARRAY['runId','reportId']"
    projected = (
        identity
        + """-CASE WHEN NEW.role='SEARCH' AND NEW.status='COMPLETE'
          AND right(NEW.phase_key,11)=chr(58)||'validation' THEN ARRAY['reportHash']
          ELSE ARRAY[]::text[] END"""
    )
    phase_lookup = (
        "WHERE batch_id=NEW.batch_id AND phase_key=NEW.phase_key;\n        IF NOT EXISTS("
    )
    phase_check = """WHERE batch_id=NEW.batch_id AND phase_key=NEW.phase_key;
        IF p.phase='validation' AND NEW.role='SEARCH' AND NEW.status='COMPLETE'
          AND NEW.body->>'selectionEvidenceHash' IS DISTINCT FROM
            backtest.quant_selection_evidence(NEW.batch_id,NEW.phase_key) THEN
          RAISE EXCEPTION 'quant selection evidence conflict'; END IF;
        IF NOT EXISTS("""
    selected = "ELSE 'COMPLETE' END) THEN RAISE EXCEPTION 'quant selection conflict';"
    selected_with_failure = """ELSE CASE WHEN NEW.status IN ('FAILED','BLOCKED') THEN NEW.status
              ELSE 'COMPLETE' END END) THEN RAISE EXCEPTION 'quant selection conflict';"""
    for old, new in (
        (identity, projected),
        (phase_lookup, phase_check),
        (selected, selected_with_failure),
    ):
        if source.count(old) != 1:
            raise RuntimeError("0035 quant result guard definition drift")
        source = source.replace(old, new, 1)
    connection.exec_driver_sql(source)


def downgrade():
    raise RuntimeError("Preserve immutable quant validation evidence")
