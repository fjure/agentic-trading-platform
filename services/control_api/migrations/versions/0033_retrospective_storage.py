"""RRB-03: immutable retrospective snapshots and shared fenced admission."""

from alembic import op

revision = "0033_retrospective_storage"
down_revision = "0032_collection_control"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE backtest.runs ADD COLUMN research_mode text,
      ADD COLUMN selection_policy_version text, ADD COLUMN experiment_version text,
      ADD COLUMN assessment_id uuid;
    ALTER TABLE backtest.runs ADD CONSTRAINT retrospective_run_variant CHECK(
      (research_mode IS NULL AND selection_policy_version IS NULL AND experiment_version IS NULL
        AND assessment_id IS NULL) OR
      (research_mode IS NOT DISTINCT FROM 'RETROSPECTIVE_EXPLORATORY'
        AND selection_policy_version IS NOT DISTINCT FROM 'retrospective-v1'
        AND experiment_version IS NOT DISTINCT FROM 'retrospective-experiment-v1' AND
          assessment_id IS NOT NULL));
    CREATE VIEW backtest.source_license_status AS SELECT 'alpaca'::text AS provider,
      CASE license_status WHEN 'BLOCKED' THEN 'DENIED'
        WHEN 'CONFIRMED' THEN 'DECLARED_CONFIRMED' ELSE 'UNKNOWN' END AS status,
      observed_at,policy_version FROM research.source_status WHERE singleton;
    REVOKE ALL ON backtest.source_license_status FROM PUBLIC;
    GRANT SELECT ON backtest.source_license_status TO atl_backtest_worker;
    CREATE TABLE backtest.retrospective_assessments (
      assessment_id uuid PRIMARY KEY,actor text NOT NULL CHECK(actor='local-operator'),
      idempotency_key text NOT NULL,request jsonb NOT NULL,
      request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
      policy_version text NOT NULL CHECK(policy_version='retrospective-v1'),
      code_version text NOT NULL CHECK(code_version ~ '^[0-9a-f]{40}$'),
      snapshot_version bigint NOT NULL CHECK(snapshot_version>=0),snapshot_at timestamptz NOT NULL,
      trust_version bigint NOT NULL CHECK(trust_version>=0),trust_policy_version text NOT NULL,
      status text NOT NULL DEFAULT 'ACCEPTED'
        CHECK(status IN ('ACCEPTED','RUNNING','SUCCEEDED','BLOCKED','FAILED')),
      accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      trace_id text NOT NULL CHECK(trace_id ~ '^[0-9a-f]{32}$'),error_code text,
      assessment_hash text CHECK(assessment_hash ~ '^[0-9a-f]{64}$'),
      owner uuid,fence bigint NOT NULL DEFAULT 0 CHECK(fence>=0),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
      lease_until timestamptz,started_at timestamptz,
      UNIQUE(actor,idempotency_key));
    ALTER TABLE backtest.runs ADD FOREIGN KEY(assessment_id)
      REFERENCES backtest.retrospective_assessments;
    CREATE INDEX retrospective_queue ON
      backtest.retrospective_assessments(status,accepted_at,assessment_id);
    CREATE TABLE backtest.license_receipts (
      assessment_id uuid PRIMARY KEY REFERENCES backtest.retrospective_assessments,
      provider text NOT NULL CHECK(provider='alpaca'),status text NOT NULL
        CHECK(status IN ('UNKNOWN','DECLARED_CONFIRMED','DENIED')),
      source_observed_at timestamptz NOT NULL,policy_version text,
      recorded_at timestamptz NOT NULL,
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'));
    CREATE TABLE backtest.retrospective_datasets (
      assessment_id uuid PRIMARY KEY REFERENCES backtest.retrospective_assessments,
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),canonical_text text NOT NULL,
      CHECK(body=canonical_text::jsonb AND octet_length(canonical_text)<=12582912),
      CHECK(fingerprint=encode(sha256(convert_to(canonical_text,'UTF8')),'hex')));
    CREATE TABLE backtest.retrospective_revision_bindings (
      assessment_id uuid NOT NULL REFERENCES backtest.retrospective_datasets,
      kind text NOT NULL CHECK(kind IN ('BAR','CALENDAR','QUALITY','EVIDENCE')),
      reference_id uuid NOT NULL,expected_fingerprint text NOT NULL
        CHECK(expected_fingerprint ~ '^[0-9a-f]{64}$'),expected_identity jsonb NOT NULL,
      bar_id uuid REFERENCES research.bar_revisions,
      calendar_id uuid REFERENCES research.calendar_versions,
      quality_id uuid REFERENCES research.quality_observations,
      evidence_id uuid REFERENCES research.evidence_revisions,
      CHECK(num_nonnulls(bar_id,calendar_id,quality_id,evidence_id)=1),
      CHECK((kind='BAR' AND bar_id=reference_id AND calendar_id IS NULL AND quality_id IS NULL
        AND evidence_id IS NULL)
        OR(kind='CALENDAR' AND calendar_id=reference_id AND bar_id IS NULL AND quality_id IS
          NULL AND evidence_id IS NULL)
        OR(kind='QUALITY' AND quality_id=reference_id AND bar_id IS NULL AND calendar_id IS NULL
          AND evidence_id IS NULL)
        OR(kind='EVIDENCE' AND evidence_id=reference_id AND bar_id IS NULL AND calendar_id IS
          NULL AND quality_id IS NULL)),
      PRIMARY KEY(assessment_id,kind,reference_id));
    CREATE TABLE backtest.retrospective_reports (
      report_id uuid PRIMARY KEY,assessment_id uuid NOT NULL REFERENCES
        backtest.retrospective_datasets,
      run_id uuid UNIQUE REFERENCES backtest.runs,
      phase text NOT NULL CHECK(phase IN ('PREVIEW','FINAL')),
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),
      findings jsonb NOT NULL CHECK(jsonb_typeof(findings)='array'),
      revision_references jsonb NOT NULL CHECK(jsonb_typeof(revision_references)='array'),
      canonical_text text NOT NULL,
      published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      CHECK((phase='PREVIEW')=(run_id IS NULL)),
      CHECK(canonical_text::jsonb=jsonb_build_object('report',body,'findings',findings,
        'revisionReferences',revision_references) AND octet_length(canonical_text)<=12582912),
      CHECK(body ?& ARRAY['findingsCount','revisionReferenceCount']),
      CHECK((jsonb_array_length(findings)=(body->>'findingsCount')::integer AND
        jsonb_array_length(revision_references)=(body->>'revisionReferenceCount')::integer)
          IS TRUE),
      CHECK(jsonb_array_length(findings)<=100000 AND
        jsonb_array_length(revision_references)<=100000),
      CHECK(fingerprint=encode(sha256(convert_to(canonical_text,'UTF8')),'hex')));
    CREATE UNIQUE INDEX retrospective_preview_once ON backtest.retrospective_reports(assessment_id)
      WHERE phase='PREVIEW';
    CREATE TABLE backtest.retrospective_audit (
      event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      assessment_id uuid NOT NULL REFERENCES backtest.retrospective_assessments,
      event text NOT NULL CHECK(event IN ('ACCEPTED','RUNNING','SUCCEEDED','BLOCKED','FAILED')),
      fence bigint NOT NULL,trace_id text NOT NULL,
      observed_at timestamptz NOT NULL DEFAULT clock_timestamp());
    """)
    for table in (
        "license_receipts",
        "retrospective_datasets",
        "retrospective_revision_bindings",
        "retrospective_reports",
        "retrospective_audit",
    ):
        op.execute(f"""CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.{table}
          FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
          REVOKE ALL ON backtest.{table} FROM PUBLIC;
          GRANT SELECT ON backtest.{table} TO atl_backtest_worker,atl_control_api;""")
    op.execute("""
    REVOKE ALL ON backtest.retrospective_assessments FROM PUBLIC;
    GRANT SELECT ON backtest.retrospective_assessments TO atl_control_api,atl_backtest_worker;
    GRANT UPDATE ON backtest.retrospective_assessments TO atl_backtest_worker;
    GRANT INSERT ON backtest.retrospective_datasets,backtest.retrospective_revision_bindings,
      backtest.retrospective_reports,backtest.retrospective_audit TO atl_backtest_worker;
    GRANT USAGE ON SEQUENCE backtest.retrospective_audit_event_id_seq TO atl_backtest_worker;

    CREATE FUNCTION backtest.oldest_job() RETURNS TABLE(kind text,id uuid)
      LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
      SELECT q.kind,q.id FROM (
        SELECT 'backtest'::text AS kind,run_id AS id,accepted_at FROM backtest.runs
          WHERE status='ACCEPTED' OR(status='RUNNING' AND lease_until<=clock_timestamp() AND
            attempts<3)
        UNION ALL SELECT 'readiness',assessment_id,accepted_at FROM backtest.readiness_jobs
          WHERE status='ACCEPTED' OR(status='RUNNING' AND lease_until<=clock_timestamp() AND
            attempts<3)
        UNION ALL SELECT 'retrospective',assessment_id,accepted_at FROM
          backtest.retrospective_assessments
          WHERE status='ACCEPTED' OR(status='RUNNING' AND lease_until<=clock_timestamp() AND
            attempts<3)
      ) q ORDER BY accepted_at,id LIMIT 1 $$;
    REVOKE ALL ON FUNCTION backtest.oldest_job() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION backtest.oldest_job() TO atl_backtest_worker;
    CREATE OR REPLACE FUNCTION backtest.guard_shared_worker() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE self_id uuid; first_id uuid;
    BEGIN
      IF NEW.status<>'RUNNING' THEN RETURN NEW; END IF;
      IF TG_OP='UPDATE' AND OLD.status='RUNNING' AND NEW.owner IS NOT DISTINCT FROM OLD.owner
        AND NEW.fence=OLD.fence THEN RETURN NEW; END IF;
      PERFORM pg_advisory_xact_lock(894217346);
      IF TG_TABLE_NAME='runs' THEN self_id:=NEW.run_id; ELSE self_id:=NEW.assessment_id; END IF;
      SELECT id INTO first_id FROM backtest.oldest_job();
      IF first_id IS DISTINCT FROM self_id THEN RAISE EXCEPTION
        'shared backtest admission order'; END IF;
      IF EXISTS(SELECT FROM backtest.runs WHERE status='RUNNING' AND lease_until>clock_timestamp()
          AND(TG_TABLE_NAME<>'runs' OR run_id<>self_id))
        OR EXISTS(SELECT FROM backtest.readiness_jobs WHERE status='RUNNING'
          AND lease_until>clock_timestamp() AND(TG_TABLE_NAME<>'readiness_jobs' OR
            assessment_id<>self_id))
        OR EXISTS(SELECT FROM backtest.retrospective_assessments WHERE status='RUNNING'
          AND lease_until>clock_timestamp() AND(TG_TABLE_NAME<>'retrospective_assessments' OR
            assessment_id<>self_id))
        THEN RAISE EXCEPTION 'shared backtest worker busy'; END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER shared_worker BEFORE INSERT OR UPDATE ON backtest.retrospective_assessments
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_shared_worker();
    CREATE FUNCTION backtest.guard_retrospective_job() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE retro boolean; bound backtest.retrospective_assessments;
    BEGIN
      retro:=TG_TABLE_NAME='retrospective_assessments';
      IF TG_TABLE_NAME='runs' THEN
        retro:=NEW.research_mode IS NOT NULL OR
          NEW.request->>'researchMode'='RETROSPECTIVE_EXPLORATORY';
        IF TG_OP='UPDATE' THEN
          retro:=coalesce(retro,false) OR OLD.research_mode IS NOT NULL OR
            OLD.request->>'researchMode'='RETROSPECTIVE_EXPLORATORY';
        END IF;
        IF NEW.request->>'researchMode' IS NOT NULL AND NEW.request->>'researchMode' NOT IN
          ('RETROSPECTIVE_EXPLORATORY','STRICT_PIT')
          THEN RAISE EXCEPTION 'invalid research mode'; END IF;
        IF retro THEN
          SELECT * INTO bound FROM backtest.retrospective_assessments WHERE
            assessment_id=NEW.assessment_id;
          IF NOT FOUND OR bound.status<>'SUCCEEDED'
            OR (NEW.request-ARRAY['assessmentId','assessmentHash']) IS DISTINCT FROM bound.request
            OR NEW.request->>'assessmentHash' IS DISTINCT FROM bound.assessment_hash
            OR NEW.snapshot_version<>bound.snapshot_version OR NEW.snapshot_at<>bound.snapshot_at
            OR NEW.code_version<>bound.code_version OR NEW.research_mode IS DISTINCT FROM
              'RETROSPECTIVE_EXPLORATORY'
            OR NEW.request->>'assessmentId' IS DISTINCT FROM NEW.assessment_id::text
            THEN RAISE EXCEPTION 'retrospective snapshot conflict'; END IF;
        END IF;
      END IF;
      IF NOT coalesce(retro,false) THEN RETURN NEW; END IF;
      IF current_setting('backtest.policy_version',true) IS DISTINCT FROM 'retrospective-v1'
        THEN RAISE EXCEPTION 'retrospective policy context required'; END IF;
      IF TG_OP='INSERT' AND (NEW.status<>'ACCEPTED' OR NEW.owner IS NOT NULL
        OR NEW.fence<>0 OR NEW.attempts<>0 OR NEW.lease_until IS NOT NULL
        OR NEW.started_at IS NOT NULL OR NEW.error_code IS NOT NULL
        OR coalesce((to_jsonb(NEW)->>'checkpoint')::integer,0)<>0
        OR to_jsonb(NEW)->>'assessment_hash' IS NOT NULL)
        THEN RAISE EXCEPTION 'invalid retrospective initial state'; END IF;
      IF NEW.status='SUCCEEDED' AND NOT EXISTS(
        SELECT FROM backtest.source_license_status WHERE status<>'DENIED')
        THEN RAISE EXCEPTION 'retrospective license unavailable'; END IF;
      IF TG_OP='UPDATE' THEN
        IF
          (to_jsonb(NEW)-ARRAY['status','updated_at','error_code','owner','fence','attempts','lease_until','started_at','checkpoint','assessment_hash'])
          IS DISTINCT FROM
          (to_jsonb(OLD)-ARRAY['status','updated_at','error_code','owner','fence','attempts','lease_until','started_at','checkpoint','assessment_hash'])
          THEN RAISE EXCEPTION 'immutable retrospective identity'; END IF;
        IF OLD.status='ACCEPTED' AND NEW.status<>'RUNNING' THEN
          RAISE EXCEPTION 'retrospective lease lost'; END IF;
        IF OLD.status IN ('SUCCEEDED','BLOCKED','FAILED') THEN RAISE EXCEPTION
          'terminal retrospective job'; END IF;
        IF NEW.status='RUNNING' AND (OLD.status<>'RUNNING' OR
          NEW.owner IS DISTINCT FROM OLD.owner OR NEW.fence<>OLD.fence) THEN
          IF NEW.fence<>OLD.fence+1 OR NEW.attempts<>OLD.attempts+1 OR NEW.owner IS NULL
            OR NEW.started_at IS NULL OR NEW.started_at>clock_timestamp()
            OR NEW.started_at<statement_timestamp() OR NEW.lease_until IS NULL
            OR NEW.lease_until<=clock_timestamp()
            OR NEW.lease_until>clock_timestamp()+interval '300 seconds'
            OR OLD.attempts>=3
            OR (OLD.status='RUNNING' AND OLD.lease_until>clock_timestamp())
            THEN RAISE EXCEPTION 'retrospective lease lost'; END IF;
        ELSIF OLD.status='RUNNING' THEN
          IF NEW.attempts IS DISTINCT FROM OLD.attempts OR NEW.fence<>OLD.fence
            OR NEW.started_at IS DISTINCT FROM OLD.started_at
            OR NEW.status NOT IN ('RUNNING','SUCCEEDED','BLOCKED','FAILED')
            OR (NEW.status='RUNNING' AND (NEW.owner IS DISTINCT FROM OLD.owner OR
              NEW.lease_until IS DISTINCT FROM OLD.lease_until OR
              NEW.error_code IS DISTINCT FROM OLD.error_code))
            OR (NEW.status<>'RUNNING' AND (NEW.owner IS NOT NULL OR NEW.lease_until IS NOT NULL))
            THEN RAISE EXCEPTION 'retrospective lease lost'; END IF;
          IF NOT(NEW.status='FAILED' AND OLD.lease_until<=clock_timestamp() AND OLD.attempts>=3)
            THEN
            PERFORM backtest.assert_retrospective_fence(
              (to_jsonb(OLD)->>'assessment_id')::uuid,(to_jsonb(OLD)->>'run_id')::uuid);
          END IF;
        END IF;
        IF NEW.status IN ('SUCCEEDED','BLOCKED') AND NOT EXISTS(
          SELECT FROM backtest.retrospective_reports WHERE
          ((TG_TABLE_NAME='runs' AND run_id=(to_jsonb(NEW)->>'run_id')::uuid AND phase='FINAL') OR
          (TG_TABLE_NAME='retrospective_assessments' AND
            assessment_id=(to_jsonb(NEW)->>'assessment_id')::uuid AND phase='PREVIEW'))
          AND body->>'overallStatus'=CASE WHEN NEW.status='BLOCKED' THEN 'BLOCKED' ELSE
            'EXPLORATORY_WITH_LIMITATIONS' END)
          THEN RAISE EXCEPTION 'complete retrospective report required'; END IF;
        IF TG_TABLE_NAME='runs' AND NEW.status='SUCCEEDED' AND
          (NOT EXISTS(SELECT FROM backtest.manifests WHERE run_id=(to_jsonb(NEW)->>'run_id')::uuid)
           OR NOT EXISTS(SELECT FROM backtest.results WHERE
             run_id=(to_jsonb(NEW)->>'run_id')::uuid))
          THEN RAISE EXCEPTION 'complete retrospective result required'; END IF;
        IF TG_TABLE_NAME='retrospective_assessments' THEN
          IF NEW.status IN ('SUCCEEDED','BLOCKED') THEN
            SELECT encode(sha256(convert_to(jsonb_build_array(NEW.request_hash,NEW.snapshot_version,
              NEW.snapshot_at,d.fingerprint,r.fingerprint)::text,'UTF8')),'hex') INTO
                NEW.assessment_hash
              FROM backtest.retrospective_datasets d JOIN backtest.retrospective_reports r
                USING(assessment_id)
              WHERE d.assessment_id=NEW.assessment_id AND r.phase='PREVIEW';
          ELSIF NEW.assessment_hash IS DISTINCT FROM OLD.assessment_hash THEN
            RAISE EXCEPTION 'immutable retrospective assessment hash'; END IF;
        END IF;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION backtest.guard_retrospective_job() FROM PUBLIC;
    CREATE TRIGGER a_retrospective_policy BEFORE INSERT OR UPDATE ON backtest.runs
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_retrospective_job();
    CREATE TRIGGER a_retrospective_policy BEFORE INSERT OR UPDATE ON
      backtest.retrospective_assessments
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_retrospective_job();

    CREATE FUNCTION backtest.assert_retrospective_fence(p_assessment uuid,p_run uuid DEFAULT NULL)
      RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp
        AS $$
    DECLARE valid boolean;
    BEGIN
      IF current_setting('backtest.policy_version',true) IS DISTINCT FROM 'retrospective-v1'
        THEN RAISE EXCEPTION 'retrospective policy context required'; END IF;
      IF p_run IS NULL THEN
        SELECT true INTO valid FROM backtest.retrospective_assessments WHERE
          assessment_id=p_assessment
          AND status='RUNNING' AND owner::text=current_setting('backtest.owner',true)
          AND fence::text=current_setting('backtest.fence',true) AND lease_until>clock_timestamp()
          AND started_at>clock_timestamp()-interval '300 seconds' FOR UPDATE;
      ELSE
        SELECT true INTO valid FROM backtest.runs WHERE run_id=p_run AND assessment_id=p_assessment
          AND research_mode='RETROSPECTIVE_EXPLORATORY' AND status='RUNNING'
          AND owner::text=current_setting('backtest.owner',true)
          AND fence::text=current_setting('backtest.fence',true) AND lease_until>clock_timestamp()
          AND started_at>clock_timestamp()-interval '300 seconds' FOR UPDATE;
      END IF;
      IF valid IS DISTINCT FROM true THEN RAISE EXCEPTION 'retrospective lease lost'; END IF;
    END $$;
    REVOKE ALL ON FUNCTION backtest.assert_retrospective_fence(uuid,uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION backtest.assert_retrospective_fence(uuid,uuid) TO atl_backtest_worker;
    CREATE FUNCTION backtest.guard_retrospective_content() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,research,pg_temp AS $$
    DECLARE a backtest.retrospective_assessments; source jsonb; actual text; dataset jsonb;
    BEGIN
      PERFORM backtest.assert_retrospective_fence(NEW.assessment_id,
        CASE WHEN TG_TABLE_NAME='retrospective_reports' THEN (to_jsonb(NEW)->>'run_id')::uuid
          ELSE NULL END);
      SELECT * INTO a FROM backtest.retrospective_assessments WHERE assessment_id=NEW.assessment_id;
      IF TG_TABLE_NAME='retrospective_datasets' THEN
        IF NEW.body->>'researchMode' IS DISTINCT FROM 'RETROSPECTIVE_EXPLORATORY'
          OR NEW.body->>'selectionPolicyVersion' IS DISTINCT FROM 'retrospective-v1'
          OR NEW.body->>'protocolHash' IS DISTINCT FROM a.request_hash
          OR NEW.body->>'codeVersion' IS DISTINCT FROM a.code_version
          OR (NEW.body->>'trustVersion')::bigint IS DISTINCT FROM a.trust_version
          OR NEW.body->>'trustPolicyVersion' IS DISTINCT FROM a.trust_policy_version
          OR NEW.body->>'licenseReceiptFingerprint' IS DISTINCT FROM
            (SELECT fingerprint FROM backtest.license_receipts WHERE assessment_id=a.assessment_id)
          OR (NEW.body->'snapshotReference'->>'snapshotVersion')::bigint IS DISTINCT FROM
            a.snapshot_version
          OR (NEW.body->'snapshotReference'->>'snapshotAt')::timestamptz IS DISTINCT FROM
            a.snapshot_at
          OR jsonb_typeof(NEW.body->'bindings') IS DISTINCT FROM 'array'
          OR jsonb_array_length(NEW.body->'bindings')>100000
          THEN RAISE EXCEPTION 'retrospective snapshot conflict'; END IF;
      END IF;
      IF TG_TABLE_NAME='retrospective_revision_bindings' THEN
        SELECT body INTO dataset FROM backtest.retrospective_datasets WHERE
          assessment_id=NEW.assessment_id;
        IF NOT dataset->'bindings' @> jsonb_build_array(jsonb_build_object('kind',NEW.kind,
          'referenceId',NEW.reference_id::text,'fingerprint',NEW.expected_fingerprint,'identity',NEW.expected_identity))
          THEN RAISE EXCEPTION 'retrospective revision conflict'; END IF;
        IF NEW.kind='BAR' THEN SELECT to_jsonb(r),r.fingerprint INTO source,actual FROM
          research.bar_revisions r WHERE revision_id=NEW.reference_id;
        ELSIF NEW.kind='CALENDAR' THEN SELECT to_jsonb(r),r.version INTO source,actual FROM
          research.calendar_versions r WHERE session_id=NEW.reference_id;
        ELSIF NEW.kind='QUALITY' THEN SELECT
          to_jsonb(r),encode(sha256(convert_to(to_jsonb(r)::text,'UTF8')),'hex') INTO
            source,actual FROM research.quality_observations r WHERE
              observation_id=NEW.reference_id;
        ELSE SELECT to_jsonb(r),r.fingerprint INTO source,actual FROM
          research.evidence_revisions r WHERE evidence_id=NEW.reference_id; END IF;
        IF source IS NULL OR actual IS DISTINCT FROM NEW.expected_fingerprint
          OR (source->>'commit_version')::bigint>a.snapshot_version
          OR (source->>'ingested_at')::timestamptz>a.snapshot_at
          OR NOT source @> NEW.expected_identity THEN RAISE EXCEPTION
            'retrospective revision conflict'; END IF;
        IF (SELECT count(*) FROM backtest.retrospective_revision_bindings WHERE
          assessment_id=NEW.assessment_id)>=100000
          THEN RAISE EXCEPTION 'retrospective revision budget'; END IF;
      END IF;
      IF TG_TABLE_NAME='retrospective_reports' THEN
        SELECT body INTO dataset FROM backtest.retrospective_datasets WHERE
          assessment_id=NEW.assessment_id;
        IF NEW.body->>'reportId' IS DISTINCT FROM NEW.report_id::text
          OR NEW.body->>'assessmentId' IS DISTINCT FROM NEW.assessment_id::text
          OR NEW.body->>'runId' IS DISTINCT FROM NEW.run_id::text
          OR NEW.body->>'researchMode' IS DISTINCT FROM 'RETROSPECTIVE_EXPLORATORY'
          OR NEW.body->>'selectionPolicyVersion' IS DISTINCT FROM 'retrospective-v1'
          OR NEW.body->>'version' IS DISTINCT FROM 'reliability-report-v1'
          OR NEW.body->>'phase' IS DISTINCT FROM NEW.phase
          OR NEW.body->>'protocolHash' IS DISTINCT FROM a.request_hash
          OR NEW.body->>'overallStatus' IS NULL
          OR NOT (NEW.body ?& ARRAY['reportHash','experimentVersion','instrumentId','symbol',
            'provider','feed','interval','adjustment','period','folds','dimensions',
            'corporateActionStatus','licenseStatus','historicalAvailabilityStatus',
            'methods','resultLimitations','findingsCount','revisionReferenceCount'])
          OR (NEW.body->'snapshotReference'->>'snapshotVersion')::bigint IS DISTINCT FROM
            a.snapshot_version
          OR (NEW.body->'snapshotReference'->>'snapshotAt')::timestamptz IS DISTINCT FROM
            a.snapshot_at
          OR dataset IS NULL OR jsonb_array_length(dataset->'bindings') IS DISTINCT FROM
            (SELECT count(*)::integer FROM backtest.retrospective_revision_bindings WHERE
              assessment_id=NEW.assessment_id)
          THEN RAISE EXCEPTION 'retrospective snapshot conflict'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION backtest.guard_retrospective_content() FROM PUBLIC;
    CREATE TRIGGER fenced_insert BEFORE INSERT ON backtest.retrospective_datasets
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_retrospective_content();
    CREATE TRIGGER fenced_insert BEFORE INSERT ON backtest.retrospective_revision_bindings
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_retrospective_content();
    CREATE TRIGGER fenced_insert BEFORE INSERT ON backtest.retrospective_reports
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_retrospective_content();
    CREATE FUNCTION backtest.guard_retrospective_result() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE a uuid; dataset_hash text;
    BEGIN
      SELECT assessment_id INTO a FROM backtest.runs WHERE run_id=NEW.run_id;
      IF a IS NOT NULL THEN
        PERFORM backtest.assert_retrospective_fence(a,NEW.run_id);
        IF TG_TABLE_NAME='manifests' THEN
          SELECT fingerprint INTO dataset_hash FROM backtest.retrospective_datasets WHERE
            assessment_id=a;
          IF NEW.body->>'researchMode' IS DISTINCT FROM 'RETROSPECTIVE_EXPLORATORY'
            OR NEW.body->>'selectionPolicyVersion' IS DISTINCT FROM 'retrospective-v1'
            OR NEW.body->>'experimentVersion' IS DISTINCT FROM 'retrospective-experiment-v1'
            OR NEW.body->>'assessmentId' IS DISTINCT FROM a::text
            OR NEW.body->>'datasetFingerprint' IS DISTINCT FROM dataset_hash
            THEN RAISE EXCEPTION 'retrospective snapshot conflict'; END IF;
        END IF;
        IF TG_TABLE_NAME='results' AND NOT EXISTS(SELECT FROM backtest.retrospective_reports
          WHERE run_id=NEW.run_id AND phase='FINAL') THEN RAISE EXCEPTION
            'complete retrospective report required'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION backtest.guard_retrospective_result() FROM PUBLIC;
    CREATE TRIGGER retrospective_fence BEFORE INSERT ON backtest.results
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_retrospective_result();
    CREATE TRIGGER retrospective_fence BEFORE INSERT ON backtest.manifests
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_retrospective_result();
    CREATE TRIGGER retrospective_fence BEFORE INSERT ON backtest.fold_results
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_retrospective_result();
    """)
    _requests()


def _requests():
    op.execute("""
    CREATE FUNCTION backtest.check_shared_queue() RETURNS void LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$ BEGIN
      PERFORM pg_advisory_xact_lock(894217345);
      IF (SELECT count(*) FROM (
        SELECT run_id FROM backtest.runs WHERE status IN ('ACCEPTED','RUNNING')
        UNION ALL SELECT assessment_id FROM backtest.readiness_jobs WHERE status IN
          ('ACCEPTED','RUNNING')
        UNION ALL SELECT assessment_id FROM backtest.retrospective_assessments WHERE status IN
          ('ACCEPTED','RUNNING')) q)>=100
        THEN RAISE EXCEPTION 'backtest queue full'; END IF;
    END $$;
    REVOKE ALL ON FUNCTION backtest.check_shared_queue() FROM PUBLIC;
    ALTER FUNCTION backtest.request_run(uuid,text,text,jsonb,text,text,text) RENAME TO
      legacy_request_run;
    REVOKE ALL ON FUNCTION backtest.legacy_request_run(uuid,text,text,jsonb,text,text,text) FROM
      PUBLIC,atl_control_api;
    ALTER FUNCTION backtest.request_readiness(uuid,text,text,jsonb,text,text,text,text) RENAME
      TO legacy_request_readiness;
    REVOKE ALL ON FUNCTION
      backtest.legacy_request_readiness(uuid,text,text,jsonb,text,text,text,text) FROM
        PUBLIC,atl_control_api;
    CREATE FUNCTION backtest.request_run(p_id uuid,p_actor text,p_key text,p_request
      jsonb,p_hash text,p_code text,p_trace text)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp
        AS $$ BEGIN
      IF p_request->>'researchMode' IS NOT NULL AND p_request->>'researchMode'<>'STRICT_PIT'
        THEN RAISE EXCEPTION 'invalid backtest request'; END IF;
      PERFORM pg_advisory_xact_lock(894217345);
      IF NOT EXISTS(SELECT FROM backtest.runs WHERE actor=p_actor AND idempotency_key=p_key)
        THEN PERFORM backtest.check_shared_queue(); END IF;
      RETURN backtest.legacy_request_run(p_id,p_actor,p_key,p_request,p_hash,p_code,p_trace);
    END $$;
    CREATE FUNCTION backtest.request_readiness(p_id uuid,p_actor text,p_key text,p_request
      jsonb,p_hash text,p_code text,p_policy text,p_trace text)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp
        AS $$ BEGIN
      PERFORM pg_advisory_xact_lock(894217345);
      IF NOT EXISTS(SELECT FROM backtest.readiness_jobs WHERE actor=p_actor AND
        idempotency_key=p_key)
        THEN PERFORM backtest.check_shared_queue(); END IF;
      RETURN
        backtest.legacy_request_readiness(p_id,p_actor,p_key,p_request,p_hash,p_code,p_policy,p_trace);
    END $$;
    CREATE FUNCTION backtest.request_retrospective_assessment(p_id uuid,p_actor text,p_key text,
      p_request jsonb,p_hash text,p_code text,p_trace text) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,research,pg_temp AS $$
    DECLARE previous backtest.retrospective_assessments; receipt jsonb; observed timestamptz;
      cap bigint; trust_cap bigint; trust_policy text;
    BEGIN
      IF p_actor IS DISTINCT FROM 'local-operator' OR p_key IS NULL OR p_key !~
        '^[A-Za-z0-9._:-]{16,128}$'
        OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$' OR p_code IS NULL OR p_code !~
          '^[0-9a-f]{40}$'
        OR p_trace IS NULL OR p_trace !~ '^[0-9a-f]{32}$' OR jsonb_typeof(p_request) IS DISTINCT
          FROM 'object'
        OR p_request->>'researchMode' IS DISTINCT FROM 'RETROSPECTIVE_EXPLORATORY'
        OR p_request->>'selectionPolicyVersion' IS DISTINCT FROM 'retrospective-v1'
        OR octet_length(p_request::text)>12582912 THEN RAISE EXCEPTION
          'invalid retrospective request'; END IF;
      PERFORM pg_advisory_xact_lock(894217345);
      SELECT * INTO previous FROM backtest.retrospective_assessments WHERE actor=p_actor AND
        idempotency_key=p_key;
      IF FOUND THEN
        IF previous.request_hash<>p_hash OR previous.request<>p_request THEN RAISE EXCEPTION
          'backtest idempotency conflict'; END IF;
        RETURN previous.assessment_id;
      END IF;
      PERFORM backtest.check_shared_queue();
      PERFORM set_config('backtest.policy_version','retrospective-v1',true);
      SELECT v.version,t.version,t.policy_version,to_jsonb(s),clock_timestamp()
        INTO cap,trust_cap,trust_policy,receipt,observed
        FROM research.version_state v CROSS JOIN research.trust_version_state t
        CROSS JOIN backtest.source_license_status s WHERE v.singleton AND t.singleton;
      IF receipt IS NULL THEN RAISE EXCEPTION 'retrospective state unavailable'; END IF;
      INSERT INTO
        backtest.retrospective_assessments(assessment_id,actor,idempotency_key,request,request_hash,
        policy_version,code_version,snapshot_version,snapshot_at,trust_version,trust_policy_version,trace_id)
        VALUES(p_id,p_actor,p_key,p_request,p_hash,'retrospective-v1',p_code,cap,observed,trust_cap,trust_policy,p_trace);
      IF NOT FOUND THEN RAISE EXCEPTION 'retrospective state unavailable'; END IF;
      IF receipt IS NULL THEN RAISE EXCEPTION 'retrospective license unavailable'; END IF;
      INSERT INTO backtest.license_receipts VALUES(p_id,receipt->>'provider',receipt->>'status',
        (receipt->>'observed_at')::timestamptz,receipt->>'policy_version',observed,
        encode(sha256(convert_to(receipt::text,'UTF8')),'hex'));
      INSERT INTO backtest.retrospective_audit(assessment_id,event,fence,trace_id)
        VALUES(p_id,'ACCEPTED',0,p_trace);
      RETURN p_id;
    END $$;
    CREATE FUNCTION backtest.request_retrospective_run(p_id uuid,p_actor text,p_key text,
      p_request jsonb,p_hash text,p_code text,p_trace text) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE a backtest.retrospective_assessments; previous backtest.runs;
    BEGIN
      IF p_actor IS DISTINCT FROM 'local-operator' OR p_key IS NULL OR p_key !~
        '^[A-Za-z0-9._:-]{16,128}$'
        OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$' OR p_trace IS NULL OR p_trace !~
          '^[0-9a-f]{32}$'
        OR p_request->>'researchMode' IS DISTINCT FROM 'RETROSPECTIVE_EXPLORATORY'
        OR p_request->>'selectionPolicyVersion' IS DISTINCT FROM 'retrospective-v1'
        THEN RAISE EXCEPTION 'invalid retrospective request'; END IF;
      PERFORM pg_advisory_xact_lock(894217345);
      SELECT * INTO previous FROM backtest.runs WHERE actor=p_actor AND idempotency_key=p_key;
      IF FOUND THEN
        IF previous.request_hash<>p_hash OR previous.request<>p_request THEN RAISE EXCEPTION
          'backtest idempotency conflict'; END IF;
        RETURN previous.run_id;
      END IF;
      SELECT * INTO a FROM backtest.retrospective_assessments WHERE
        assessment_id=(p_request->>'assessmentId')::uuid;
      IF NOT FOUND OR a.status<>'SUCCEEDED' OR a.code_version IS DISTINCT FROM p_code OR
        (p_request-ARRAY['assessmentId','assessmentHash']) IS DISTINCT FROM a.request OR
        a.assessment_hash IS DISTINCT FROM p_request->>'assessmentHash'
        THEN RAISE EXCEPTION 'retrospective snapshot conflict'; END IF;
      PERFORM backtest.check_shared_queue();
      PERFORM set_config('backtest.policy_version','retrospective-v1',true);
      INSERT INTO
        backtest.runs(run_id,actor,idempotency_key,request,request_hash,snapshot_version,snapshot_at,
        code_version,trace_id,research_mode,selection_policy_version,experiment_version,assessment_id)
        VALUES(p_id,p_actor,p_key,p_request,p_hash,a.snapshot_version,a.snapshot_at,p_code,p_trace,
          'RETROSPECTIVE_EXPLORATORY','retrospective-v1','retrospective-experiment-v1',a.assessment_id);
      INSERT INTO backtest.audit(run_id,event,fence,trace_id) VALUES(p_id,'ACCEPTED',0,p_trace);
      RETURN p_id;
    END $$;
    """)
    signatures = (
        "request_run(uuid,text,text,jsonb,text,text,text)",
        "request_readiness(uuid,text,text,jsonb,text,text,text,text)",
        "request_retrospective_assessment(uuid,text,text,jsonb,text,text,text)",
        "request_retrospective_run(uuid,text,text,jsonb,text,text,text)",
    )
    for signature in signatures:
        op.execute(
            f"REVOKE ALL ON FUNCTION backtest.{signature} FROM PUBLIC; "
            f"GRANT EXECUTE ON FUNCTION backtest.{signature} TO atl_control_api"
        )
    op.execute("""
    DO $$ BEGIN IF current_database()='lab_paper_runtime' THEN
      REVOKE EXECUTE ON FUNCTION backtest.legacy_request_run(uuid,text,text,jsonb,text,text,text),
        backtest.legacy_request_readiness(uuid,text,text,jsonb,text,text,text,text) FROM
          atl_paper_control_api;
      REVOKE SELECT ON backtest.retrospective_assessments,backtest.license_receipts,
        backtest.retrospective_datasets,backtest.retrospective_revision_bindings,
        backtest.retrospective_reports,backtest.retrospective_audit FROM atl_control_api;
      GRANT SELECT ON backtest.retrospective_assessments,backtest.license_receipts,
        backtest.retrospective_datasets,backtest.retrospective_revision_bindings,
        backtest.retrospective_reports,backtest.retrospective_audit TO atl_paper_control_api;
      REVOKE EXECUTE ON FUNCTION backtest.request_run(uuid,text,text,jsonb,text,text,text),
        backtest.request_readiness(uuid,text,text,jsonb,text,text,text,text),
        backtest.request_retrospective_assessment(uuid,text,text,jsonb,text,text,text),
        backtest.request_retrospective_run(uuid,text,text,jsonb,text,text,text) FROM
          atl_control_api;
      GRANT EXECUTE ON FUNCTION backtest.request_run(uuid,text,text,jsonb,text,text,text),
        backtest.request_readiness(uuid,text,text,jsonb,text,text,text,text),
        backtest.request_retrospective_assessment(uuid,text,text,jsonb,text,text,text),
        backtest.request_retrospective_run(uuid,text,text,jsonb,text,text,text) TO
          atl_paper_control_api;
    END IF; END $$;
    """)


def downgrade():
    raise RuntimeError(
        "Stop and drain retrospective workers; preserve immutable snapshots and reports"
    )
