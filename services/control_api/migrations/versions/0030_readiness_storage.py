"""RDR-03: append-only Evidenzports und isolierte Diagnose-/Sammlungspersistenz."""

from alembic import op

revision = "0030_readiness_storage"
down_revision = "0029_backtest_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    DO $$ BEGIN
      IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='atl_evidence_importer') THEN
        CREATE ROLE atl_evidence_importer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
      END IF;
    END $$;
    CREATE TABLE research.trust_version_state (
      singleton boolean PRIMARY KEY CHECK(singleton),
      version bigint NOT NULL CHECK(version BETWEEN 0 AND 9007199254740991),
      policy_version text NOT NULL);
    INSERT INTO research.trust_version_state VALUES(true,0,'unconfigured-v1');
    CREATE TABLE research.trust_decisions (
      decision_id uuid PRIMARY KEY, source_id text NOT NULL, key_id text NOT NULL,
      witness_id text, authority_type text NOT NULL CHECK(authority_type IN ('SOURCE','WITNESS')),
      effective_at timestamptz NOT NULL, valid_from timestamptz NOT NULL,
      valid_to timestamptz NOT NULL CHECK(valid_to>valid_from),
      revoked boolean NOT NULL, compromised_at timestamptz, compromise_time_known boolean NOT NULL,
      predecessor_decision_id uuid REFERENCES research.trust_decisions,
      version bigint NOT NULL UNIQUE, body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),
      commit_version bigint NOT NULL, recorded_at timestamptz NOT NULL);
    CREATE INDEX readiness_trust_identity ON research.trust_decisions(source_id,key_id,version);
    CREATE TABLE research.evidence_revisions (
      evidence_id uuid PRIMARY KEY, source_id text NOT NULL, evidence_key text NOT NULL,
      kind text NOT NULL CHECK(kind IN ('CALENDAR','QUALITY','CORPORATE_ACTION')),
      instrument_id uuid NOT NULL REFERENCES research.instruments,
      start_at timestamptz NOT NULL,end_at timestamptz NOT NULL CHECK(end_at>start_at),
      source_observed_at timestamptz NOT NULL,published_at timestamptz,
      revision integer NOT NULL CHECK(revision>0),
      predecessor_id uuid REFERENCES research.evidence_revisions,
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      statement jsonb NOT NULL CHECK(jsonb_typeof(statement)='object'),
      trust jsonb NOT NULL CHECK(jsonb_typeof(trust)='object'),
      provenance jsonb NOT NULL CHECK(jsonb_typeof(provenance)='object'),
      trust_version bigint NOT NULL,
      commit_version bigint NOT NULL,ingested_at timestamptz NOT NULL,
      CHECK(source_observed_at<=ingested_at),
      CHECK(published_at IS NULL OR published_at<=source_observed_at),
      CHECK((revision=1)=(predecessor_id IS NULL)),
      UNIQUE(source_id,evidence_key,revision));
    CREATE INDEX readiness_evidence_pit ON research.evidence_revisions
      (instrument_id,kind,start_at,end_at,commit_version);
    CREATE TABLE research.import_receipts (
      receipt_id uuid PRIMARY KEY,bundle_key text NOT NULL UNIQUE,
      fingerprint text NOT NULL UNIQUE CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
      request_body jsonb NOT NULL,entry_ids uuid[] NOT NULL,
      trust_version bigint NOT NULL,commit_version bigint NOT NULL,
      ingested_at timestamptz NOT NULL,transaction_id text NOT NULL);
    CREATE TABLE research.import_rejections (
      rejection_id uuid PRIMARY KEY,code text NOT NULL CHECK(code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
      recorded_at timestamptz NOT NULL);
    CREATE TABLE research.collection_plans (
      instrument_id uuid PRIMARY KEY REFERENCES research.instruments,
      enabled boolean NOT NULL DEFAULT false,version bigint NOT NULL DEFAULT 0 CHECK(version>=0),
      generation bigint NOT NULL DEFAULT 0 CHECK(generation>=0),
      status text NOT NULL DEFAULT 'DISABLED'
        CHECK(status IN ('DISABLED','ACTIVE','BLOCKED','PAUSED')),
      reason text NOT NULL DEFAULT 'GLOBAL_DISABLED',next_due_at timestamptz,
      updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),scheduled_through_at timestamptz);
    CREATE TABLE research.plan_revisions (
      revision_id uuid PRIMARY KEY,instrument_id uuid NOT NULL REFERENCES research.collection_plans,
      version bigint NOT NULL,generation bigint NOT NULL,enabled boolean NOT NULL,
      status text NOT NULL CHECK(status IN ('DISABLED','ACTIVE','BLOCKED','PAUSED')),
      reason text NOT NULL,next_due_at timestamptz,idempotency_key text NOT NULL,
      request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
      recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(instrument_id,version),UNIQUE(instrument_id,idempotency_key));
    CREATE TABLE research.due_jobs (
      due_id uuid PRIMARY KEY,instrument_id uuid NOT NULL REFERENCES research.collection_plans,
      generation bigint NOT NULL,purpose text NOT NULL
        CHECK(purpose IN ('CALENDAR','INITIAL_BAR','REOBSERVE_BAR','RECOVERY')),
      due_at timestamptz NOT NULL,start_at timestamptz NOT NULL,
      end_at timestamptz NOT NULL CHECK(end_at>start_at),
      job_id uuid REFERENCES research.ingestion_jobs,fence bigint NOT NULL DEFAULT 0,
      owner uuid,lease_until timestamptz,reason text,
      state text NOT NULL DEFAULT 'PENDING'
        CHECK(state IN ('PENDING','STARTED','COMPLETED','UNKNOWN','BLOCKED')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(instrument_id,generation,purpose,due_at));
    CREATE TABLE research.observation_receipts (
      receipt_id uuid PRIMARY KEY,due_id uuid NOT NULL REFERENCES research.due_jobs,
      dispatch_id uuid REFERENCES research.observation_receipts,
      instrument_id uuid NOT NULL REFERENCES research.instruments,generation bigint NOT NULL,
      purpose text NOT NULL CHECK(purpose IN ('CALENDAR','INITIAL_BAR','REOBSERVE_BAR','RECOVERY')),
      due_at timestamptz NOT NULL,job_id uuid REFERENCES research.ingestion_jobs,
      fence bigint NOT NULL,
      state text NOT NULL CHECK(state IN ('STARTED','COMPLETED','UNKNOWN','BLOCKED')),
      event_at timestamptz,bar_close_at timestamptz,published_at timestamptz,
      first_observed_at timestamptz,body jsonb NOT NULL DEFAULT '{}'
        CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=1048576),
      commit_version bigint NOT NULL,ingested_at timestamptz NOT NULL,
      transaction_id text NOT NULL DEFAULT pg_current_xact_id()::text,
      CHECK(event_at IS NULL OR bar_close_at IS NULL OR event_at<bar_close_at));
    CREATE UNIQUE INDEX readiness_dispatch_once ON research.observation_receipts(due_id,fence)
      WHERE state='STARTED';
    CREATE TABLE research.commit_confirmations (
      receipt_id uuid PRIMARY KEY,receipt_kind text NOT NULL
        CHECK(receipt_kind IN ('IMPORT','OBSERVATION')),
      confirmed_at timestamptz NOT NULL,commit_version bigint NOT NULL);
    CREATE TABLE backtest.readiness_jobs (
      assessment_id uuid PRIMARY KEY,actor text NOT NULL,idempotency_key text NOT NULL,
      request jsonb NOT NULL,request_hash text NOT NULL CHECK(request_hash ~ '^[0-9a-f]{64}$'),
      policy_version text NOT NULL,code_version text NOT NULL,
      snapshot_version bigint NOT NULL,snapshot_at timestamptz NOT NULL,
      trust_version bigint NOT NULL,trust_policy_version text NOT NULL,
      status text NOT NULL DEFAULT 'ACCEPTED'
        CHECK(status IN ('ACCEPTED','RUNNING','SUCCEEDED','FAILED')),
      eligibility text NOT NULL DEFAULT 'NOT_EVALUATED'
        CHECK(eligibility IN ('ELIGIBLE','BLOCKED','UNKNOWN','NOT_EVALUATED')),
      accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      trace_id text NOT NULL CHECK(trace_id ~ '^[0-9a-f]{32}$'),error_code text,
      owner uuid,fence bigint NOT NULL DEFAULT 0,
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
      lease_until timestamptz,started_at timestamptz,
      UNIQUE(actor,idempotency_key));
    CREATE INDEX readiness_queue ON backtest.readiness_jobs(status,accepted_at,assessment_id);
    CREATE TABLE backtest.readiness_reports (
      assessment_id uuid PRIMARY KEY REFERENCES backtest.readiness_jobs,
      fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),body jsonb NOT NULL
        CHECK(jsonb_typeof(body)='object' AND octet_length(body::text)<=12582912),
      published_at timestamptz NOT NULL DEFAULT clock_timestamp());
    CREATE TABLE backtest.readiness_findings (
      assessment_id uuid NOT NULL REFERENCES backtest.readiness_reports,
      finding_index integer NOT NULL CHECK(finding_index BETWEEN 0 AND 9999),
      category text NOT NULL,body jsonb NOT NULL CHECK(jsonb_typeof(body)='object'),
      PRIMARY KEY(assessment_id,finding_index));
    CREATE FUNCTION research.append_trust_decision(p_record jsonb,p_expected bigint)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,research,pg_temp AS $$
    DECLARE prior research.trust_decisions; state research.trust_version_state;
      decision uuid; research_version bigint;
    BEGIN
      PERFORM set_config('lock_timeout','1s',true);
      IF jsonb_typeof(p_record) IS DISTINCT FROM 'object'
        OR octet_length(p_record::text)>65536
        OR EXISTS(SELECT FROM jsonb_object_keys(p_record) k WHERE k NOT IN
          ('decisionId','sourceId','keyId','witnessId','authorityType','effectiveAt','validFrom',
          'validTo','revoked','compromisedAt','compromiseTimeKnown','policyVersion','reason','scope',
          'publicKey','algorithm','methods','actorRef','documentFingerprint','testOnly',
          'predecessorDecisionId')) THEN RAISE EXCEPTION 'invalid trust decision'; END IF;
      SELECT * INTO state FROM research.trust_version_state WHERE singleton FOR UPDATE;
      decision:=(p_record->>'decisionId')::uuid;
      SELECT * INTO prior FROM research.trust_decisions WHERE decision_id=decision;
      IF FOUND THEN
        IF prior.body<>p_record THEN RAISE EXCEPTION 'trust decision identity conflict'; END IF;
        RETURN decision;
      END IF;
      IF state.version IS DISTINCT FROM p_expected THEN
        RAISE EXCEPTION 'trust version conflict'; END IF;
      SELECT * INTO prior FROM research.trust_decisions
        WHERE source_id=p_record->>'sourceId' AND key_id=p_record->>'keyId'
        ORDER BY version DESC LIMIT 1;
      IF (prior.decision_id IS DISTINCT FROM (p_record->>'predecessorDecisionId')::uuid) THEN
        RAISE EXCEPTION 'trust predecessor conflict'; END IF;
      IF prior.decision_id IS NOT NULL AND (
        prior.body->>'publicKey' IS DISTINCT FROM p_record->>'publicKey'
        OR prior.body->>'algorithm' IS DISTINCT FROM p_record->>'algorithm'
        OR prior.authority_type IS DISTINCT FROM p_record->>'authorityType'
        OR prior.witness_id IS DISTINCT FROM p_record->>'witnessId') THEN
        RAISE EXCEPTION 'trust key identity conflict'; END IF;
      research_version:=research.reserve_version();
      UPDATE research.trust_version_state SET version=version+1,
        policy_version=p_record->>'policyVersion' WHERE singleton;
      INSERT INTO research.trust_decisions
        (decision_id,source_id,key_id,witness_id,authority_type,effective_at,valid_from,valid_to,
        revoked,compromised_at,compromise_time_known,predecessor_decision_id,version,body,
        commit_version,recorded_at)
      VALUES(decision,p_record->>'sourceId',p_record->>'keyId',p_record->>'witnessId',
        p_record->>'authorityType',(p_record->>'effectiveAt')::timestamptz,
        (p_record->>'validFrom')::timestamptz,(p_record->>'validTo')::timestamptz,
        (p_record->>'revoked')::boolean,(p_record->>'compromisedAt')::timestamptz,
        (p_record->>'compromiseTimeKnown')::boolean,(p_record->>'predecessorDecisionId')::uuid,
        state.version+1,p_record,research_version,clock_timestamp());
      RETURN decision;
    END $$;
    CREATE FUNCTION research.append_evidence_bundle(p_receipt uuid,p_key text,p_fingerprint text,
      p_entries jsonb,p_expected bigint) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,research,pg_temp AS $$
    DECLARE prior research.import_receipts; item jsonb; head research.evidence_revisions;
      trust_version bigint; research_version bigint; ingestion timestamptz; ids uuid[]:='{}';
    BEGIN
      PERFORM set_config('lock_timeout','1s',true);
      SELECT version INTO trust_version FROM research.trust_version_state
        WHERE singleton FOR UPDATE;
      SELECT * INTO prior FROM research.import_receipts WHERE bundle_key=p_key;
      IF FOUND THEN
        IF prior.fingerprint<>p_fingerprint OR prior.request_body<>p_entries THEN
          RAISE EXCEPTION 'evidence idempotency conflict'; END IF;
        RETURN prior.receipt_id;
      END IF;
      SELECT * INTO prior FROM research.import_receipts WHERE fingerprint=p_fingerprint;
      IF FOUND THEN
        IF prior.request_body<>p_entries THEN
          RAISE EXCEPTION 'evidence fingerprint conflict'; END IF;
        RETURN prior.receipt_id;
      END IF;
      IF p_expected IS DISTINCT FROM trust_version THEN
        RAISE EXCEPTION 'trust version conflict'; END IF;
      IF p_key IS NULL OR p_key !~ '^[A-Za-z0-9_-]{1,128}$'
        OR p_fingerprint IS NULL OR p_fingerprint !~ '^[0-9a-f]{64}$'
        OR jsonb_typeof(p_entries) IS DISTINCT FROM 'array'
        OR jsonb_array_length(p_entries) NOT BETWEEN 1 AND 1000
        OR octet_length(p_entries::text)>12582912 THEN
        RAISE EXCEPTION 'invalid evidence bundle'; END IF;
      research_version:=research.reserve_version();ingestion:=clock_timestamp();
      FOR item IN SELECT value FROM jsonb_array_elements(p_entries) LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'object' OR EXISTS(
          SELECT FROM jsonb_object_keys(item) k WHERE k NOT IN
          ('evidenceId','sourceId','evidenceKey','kind','instrumentId','startAt','endAt',
          'sourceObservedAt','publishedAt','revision','predecessorId','fingerprint',
          'statement','trust','provenance')) THEN RAISE EXCEPTION 'invalid evidence entry'; END IF;
        SELECT * INTO head FROM research.evidence_revisions
          WHERE source_id=item->>'sourceId' AND evidence_key=item->>'evidenceKey'
          ORDER BY revision DESC LIMIT 1;
        IF (item->>'revision')::integer<>COALESCE(head.revision,0)+1
          OR head.evidence_id IS DISTINCT FROM (item->>'predecessorId')::uuid
          OR (head.evidence_id IS NOT NULL AND (head.kind<>item->>'kind'
            OR head.instrument_id<>(item->>'instrumentId')::uuid)) THEN
          RAISE EXCEPTION 'evidence predecessor conflict'; END IF;
        INSERT INTO research.evidence_revisions
          (evidence_id,source_id,evidence_key,kind,instrument_id,start_at,end_at,source_observed_at,
          published_at,revision,predecessor_id,fingerprint,statement,trust,provenance,
          trust_version,commit_version,ingested_at)
        VALUES((item->>'evidenceId')::uuid,item->>'sourceId',item->>'evidenceKey',item->>'kind',
          (item->>'instrumentId')::uuid,(item->>'startAt')::timestamptz,
          (item->>'endAt')::timestamptz,(item->>'sourceObservedAt')::timestamptz,
          (item->>'publishedAt')::timestamptz,(item->>'revision')::integer,
          (item->>'predecessorId')::uuid,item->>'fingerprint',item->'statement',item->'trust',
          item->'provenance',trust_version,research_version,ingestion);
        ids:=array_append(ids,(item->>'evidenceId')::uuid);
      END LOOP;
      INSERT INTO research.import_receipts VALUES(p_receipt,p_key,p_fingerprint,p_entries,ids,
        trust_version,research_version,ingestion,pg_current_xact_id()::text);
      RETURN p_receipt;
    END $$;
    CREATE FUNCTION research.confirm_receipt(p_id uuid,p_kind text) RETURNS timestamptz
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research,pg_temp AS $$
    DECLARE original_transaction text; result timestamptz;
    BEGIN
      IF p_kind='IMPORT' THEN
        IF NOT pg_has_role(CASE current_setting('role') WHEN 'none' THEN session_user
          ELSE current_setting('role') END,'atl_evidence_importer','MEMBER') THEN
          RAISE EXCEPTION 'import confirmation forbidden'; END IF;
        SELECT transaction_id INTO original_transaction FROM research.import_receipts
          WHERE receipt_id=p_id;
      ELSIF p_kind='OBSERVATION' THEN
        IF (CASE current_setting('role') WHEN 'none' THEN session_user
          ELSE current_setting('role') END) NOT IN ('atl_execution','atl_paper_execution') THEN
          RAISE EXCEPTION 'observation confirmation forbidden'; END IF;
        SELECT transaction_id INTO original_transaction FROM research.observation_receipts
          WHERE receipt_id=p_id;
      ELSE RAISE EXCEPTION 'invalid receipt kind'; END IF;
      IF original_transaction IS NULL OR original_transaction=pg_current_xact_id()::text THEN
        RAISE EXCEPTION 'receipt not previously committed'; END IF;
      INSERT INTO research.commit_confirmations(receipt_id,receipt_kind,confirmed_at,commit_version)
        VALUES(p_id,p_kind,clock_timestamp(),research.reserve_version())
        ON CONFLICT(receipt_id) DO NOTHING;
      SELECT confirmed_at INTO result FROM research.commit_confirmations WHERE receipt_id=p_id;
      RETURN result;
    END $$;
    CREATE FUNCTION research.record_import_rejection(p_id uuid,p_code text) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,research,pg_temp AS $$
    BEGIN
      INSERT INTO research.import_rejections VALUES(p_id,p_code,clock_timestamp());
      RETURN p_id;
    END $$;
    DO $$ DECLARE t text; BEGIN
      FOREACH t IN ARRAY ARRAY['trust_decisions','evidence_revisions','import_receipts',
        'plan_revisions','observation_receipts','commit_confirmations','import_rejections'] LOOP
        EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON research.%I '
          'FOR EACH ROW EXECUTE FUNCTION research.reject_mutation()',t);
      END LOOP;
      FOREACH t IN ARRAY ARRAY['readiness_reports','readiness_findings'] LOOP
        EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.%I '
          'FOR EACH ROW EXECUTE FUNCTION research.reject_mutation()',t);
      END LOOP;
    END $$;
    CREATE FUNCTION research.stamp_observation() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,research,pg_temp AS $$
    BEGIN
      NEW.commit_version:=research.reserve_version();NEW.ingested_at:=clock_timestamp();
      NEW.transaction_id:=pg_current_xact_id()::text;RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION research.stamp_observation() FROM PUBLIC;
    CREATE TRIGGER a_stamp BEFORE INSERT ON research.observation_receipts
      FOR EACH ROW EXECUTE FUNCTION research.stamp_observation();
    REVOKE ALL ON research.trust_version_state,research.trust_decisions,
      research.evidence_revisions,research.import_receipts,research.import_rejections,
      research.collection_plans,
      research.plan_revisions,research.due_jobs,research.observation_receipts,
      research.commit_confirmations,backtest.readiness_jobs,backtest.readiness_reports,
      backtest.readiness_findings FROM PUBLIC;
    REVOKE ALL ON FUNCTION research.append_trust_decision(jsonb,bigint),
      research.append_evidence_bundle(uuid,text,text,jsonb,bigint),
      research.confirm_receipt(uuid,text),research.record_import_rejection(uuid,text) FROM PUBLIC;
    GRANT USAGE ON SCHEMA research TO atl_evidence_importer;
    GRANT SELECT ON research.instruments,research.trust_version_state,research.trust_decisions,
      research.evidence_revisions,research.import_receipts,research.import_rejections,
      research.commit_confirmations,
      research.version_state TO atl_evidence_importer;
    GRANT EXECUTE ON FUNCTION research.append_trust_decision(jsonb,bigint),
      research.append_evidence_bundle(uuid,text,text,jsonb,bigint),
      research.confirm_receipt(uuid,text),research.record_import_rejection(uuid,text)
      TO atl_evidence_importer;
    GRANT SELECT ON research.trust_version_state,research.trust_decisions,
      research.evidence_revisions,research.import_receipts,research.commit_confirmations
      TO atl_backtest_worker,atl_control_api,atl_execution;
    GRANT SELECT ON research.collection_plans,research.plan_revisions,research.due_jobs,
      research.observation_receipts TO atl_control_api,atl_execution,atl_backtest_worker;
    GRANT INSERT,UPDATE ON research.collection_plans,research.due_jobs TO atl_execution;
    GRANT INSERT ON research.plan_revisions,research.observation_receipts TO atl_execution;
    GRANT EXECUTE ON FUNCTION research.confirm_receipt(uuid,text) TO atl_execution;
    GRANT SELECT ON backtest.readiness_jobs,backtest.readiness_reports,
      backtest.readiness_findings TO atl_control_api,atl_backtest_worker;
    GRANT INSERT,UPDATE ON backtest.readiness_jobs TO atl_backtest_worker;
    GRANT INSERT ON backtest.readiness_reports,backtest.readiness_findings TO atl_backtest_worker;
    DO $$ BEGIN
      IF current_database()='lab_paper_runtime' THEN
        GRANT SELECT ON research.trust_version_state,research.trust_decisions,
          research.evidence_revisions,research.import_receipts,research.commit_confirmations,
          research.collection_plans,research.plan_revisions,research.due_jobs,
          research.observation_receipts TO atl_paper_control_api,atl_paper_execution;
        GRANT INSERT,UPDATE ON research.collection_plans,research.due_jobs TO atl_paper_execution;
        GRANT INSERT ON research.plan_revisions,research.observation_receipts
          TO atl_paper_execution;
        GRANT EXECUTE ON FUNCTION research.confirm_receipt(uuid,text) TO atl_paper_execution;
        GRANT SELECT ON backtest.readiness_jobs,backtest.readiness_reports,
          backtest.readiness_findings TO atl_paper_control_api;
        REVOKE ALL ON research.collection_plans,research.plan_revisions,research.due_jobs,
          research.observation_receipts FROM atl_control_api,atl_execution;
      END IF;
    END $$;
    """)


def downgrade():
    raise RuntimeError("Unveränderliche Evidenz, Berichte und Receipts müssen erhalten bleiben")
