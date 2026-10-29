"""RRB-06: current execution refusal separate from immutable snapshot diagnostics."""

from alembic import op

revision = "0034_retrospective_gate"
down_revision = "0033_retrospective_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE INDEX retrospective_calendar_family
      ON research.calendar_versions(source,session_date,commit_version);
    CREATE FUNCTION backtest.has_retrospective_correction(p_assessment uuid) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,backtest,research,pg_temp AS $$
    DECLARE binding record; old record; accepted backtest.retrospective_assessments;
      cap bigint; total integer:=0; now_at timestamptz:=clock_timestamp();
    BEGIN
      SELECT * INTO accepted FROM backtest.retrospective_assessments
        WHERE assessment_id=p_assessment;
      SELECT version INTO cap FROM research.version_state WHERE singleton;
      IF accepted.assessment_id IS NULL OR cap IS NULL THEN
        RAISE EXCEPTION 'retrospective correction unavailable'; END IF;
      FOR binding IN SELECT kind,reference_id FROM backtest.retrospective_revision_bindings
        WHERE assessment_id=p_assessment ORDER BY kind,reference_id LIMIT 100001 LOOP
        total:=total+1;
        IF total>100000 OR clock_timestamp()-now_at>=interval '1500 milliseconds'
          THEN RAISE EXCEPTION 'retrospective correction unavailable'; END IF;
        IF binding.kind='BAR' THEN
          SELECT * INTO old FROM research.bar_revisions WHERE revision_id=binding.reference_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'retrospective correction unavailable'; END IF;
          IF EXISTS(SELECT FROM research.bar_revisions newer
            WHERE newer.instrument_id=old.instrument_id AND newer.provider=old.provider
              AND newer.feed=old.feed AND newer.interval=old.interval
              AND newer.bar_start=old.bar_start AND newer.bar_end=old.bar_end
              AND newer.adjustment=old.adjustment AND newer.local_revision>old.local_revision
              AND newer.commit_version<=cap AND newer.ingested_at<=now_at
              AND (newer.commit_version>accepted.snapshot_version
                OR newer.ingested_at>accepted.snapshot_at)) THEN RETURN true; END IF;
        ELSIF binding.kind='CALENDAR' THEN
          SELECT * INTO old FROM research.calendar_versions WHERE session_id=binding.reference_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'retrospective correction unavailable'; END IF;
          IF EXISTS(SELECT FROM research.calendar_versions newer WHERE newer.source=old.source
            AND newer.session_date=old.session_date AND
              (newer.commit_version>old.commit_version OR newer.ingested_at>old.ingested_at)
            AND (newer.version IS DISTINCT FROM old.version
              OR newer.opens_at IS DISTINCT FROM old.opens_at
              OR newer.closes_at IS DISTINCT FROM old.closes_at)
            AND newer.commit_version<=cap AND newer.ingested_at<=now_at
            AND (newer.commit_version>accepted.snapshot_version
              OR newer.ingested_at>accepted.snapshot_at)) THEN RETURN true; END IF;
        ELSIF binding.kind='EVIDENCE' THEN
          SELECT * INTO old FROM research.evidence_revisions WHERE evidence_id=binding.reference_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'retrospective correction unavailable'; END IF;
          IF EXISTS(SELECT FROM research.evidence_revisions newer
            WHERE newer.source_id=old.source_id
            AND newer.evidence_key=old.evidence_key AND newer.kind=old.kind
            AND newer.revision>old.revision AND newer.commit_version<=cap
            AND newer.ingested_at<=now_at
            AND (newer.commit_version>accepted.snapshot_version
              OR newer.ingested_at>accepted.snapshot_at)) THEN RETURN true; END IF;
        ELSIF binding.kind='QUALITY' THEN
          SELECT * INTO old FROM research.quality_observations
            WHERE observation_id=binding.reference_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'retrospective correction unavailable'; END IF;
          IF EXISTS(SELECT FROM research.quality_observations newer
            WHERE newer.instrument_id=old.instrument_id AND newer.interval=old.interval
              AND newer.start_at=old.start_at AND newer.end_at=old.end_at
              AND (newer.commit_version>old.commit_version OR newer.ingested_at>old.ingested_at)
              AND newer.quality IS DISTINCT FROM old.quality
              AND newer.commit_version<=cap AND newer.ingested_at<=now_at
              AND (newer.commit_version>accepted.snapshot_version
                OR newer.ingested_at>accepted.snapshot_at)) THEN RETURN true; END IF;
        ELSE RAISE EXCEPTION 'retrospective correction unavailable'; END IF;
      END LOOP;
      IF clock_timestamp()-now_at>=interval '1500 milliseconds'
        THEN RAISE EXCEPTION 'retrospective correction unavailable'; END IF;
      RETURN false;
    END $$;
    REVOKE ALL ON FUNCTION backtest.has_retrospective_correction(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION backtest.has_retrospective_correction(uuid)
      TO atl_backtest_worker,atl_control_api;
    """)
    op.execute("""
    CREATE FUNCTION backtest.current_retrospective_gate(p_assessment uuid) RETURNS text
      LANGUAGE plpgsql SECURITY DEFINER
      SET search_path=pg_catalog,backtest,research,pg_temp AS $$
    DECLARE e record; authority jsonb; identity jsonb;
      identities jsonb; content jsonb; proof jsonb; result jsonb; scope jsonb; reference jsonb;
      payload jsonb; status text; dimension text; pinned text; key_bytes bytea;
      verified boolean; seen integer; total integer:=0; cap bigint; data_cap bigint;
      now_at timestamptz:=clock_timestamp(); periods jsonb; action jsonb; effective timestamptz;
      conclusion text; entries jsonb; amount numeric; structural boolean:=false;
      actions jsonb:='[]'::jsonb; claims jsonb:='[]'::jsonb; candidate_trusted boolean;
    BEGIN
      SELECT s.status INTO status FROM backtest.source_license_status s;
      IF status IS NULL THEN RETURN 'TRUST_UNAVAILABLE'; END IF;
      IF status='DENIED' THEN RETURN 'LICENSE_DENIED'; END IF;
      SELECT version INTO cap FROM research.trust_version_state WHERE singleton;
      IF cap IS NULL OR cap>100000 THEN RETURN 'TRUST_UNAVAILABLE'; END IF;
      SELECT version INTO data_cap FROM research.version_state WHERE singleton;
      IF data_cap IS NULL THEN RETURN 'TRUST_UNAVAILABLE'; END IF;
      SELECT body->'folds' INTO periods FROM backtest.retrospective_reports
        WHERE assessment_id=p_assessment AND phase='PREVIEW';
      FOR e IN WITH bound AS MATERIALIZED (
          SELECT reference_id AS id FROM backtest.retrospective_revision_bindings
            WHERE assessment_id=p_assessment AND kind='EVIDENCE' LIMIT 2049),
        later AS MATERIALIZED (
          SELECT hit.id FROM jsonb_array_elements(coalesce(periods,'[]'::jsonb)) fold
          CROSS JOIN LATERAL (SELECT source.evidence_id AS id
            FROM research.evidence_revisions source
            WHERE source.instrument_id=(SELECT (request->>'instrumentId')::uuid
              FROM backtest.retrospective_assessments WHERE assessment_id=p_assessment)
              AND source.kind='CORPORATE_ACTION' AND source.commit_version<=data_cap
              AND source.ingested_at<=now_at
              AND source.start_at<(fold->'window'->>'testEnd')::timestamptz
              AND source.end_at>(fold->'warmupPeriod'->>'start')::timestamptz
            LIMIT 2049) hit LIMIT 2049),
        candidates AS MATERIALIZED (
          SELECT id,true AS is_bound FROM bound UNION ALL
          SELECT DISTINCT later.id,false FROM later WHERE NOT EXISTS(
            SELECT FROM bound WHERE bound.id=later.id) LIMIT 2049)
        SELECT source.*,candidates.is_bound,(SELECT count(*) FROM later)>=2049 AS candidate_overflow
          FROM candidates JOIN research.evidence_revisions source
          ON source.evidence_id=candidates.id LOOP
        total:=total+1;
        IF e.candidate_overflow OR total>2048
          OR clock_timestamp()-now_at>=interval '1500 milliseconds'
          THEN RETURN 'TRUST_UNAVAILABLE'; END IF;
        candidate_trusted:=(SELECT count(*) FROM jsonb_object_keys(e.trust))=5 AND NOT EXISTS(
          SELECT FROM jsonb_each(e.trust) item
            WHERE item.value->>'status' IS DISTINCT FROM 'VERIFIED'
            AND NOT(item.key='temporalAuthenticity' AND item.value->>'status'='UNKNOWN'));
        IF NOT e.is_bound AND NOT candidate_trusted THEN CONTINUE; END IF;
        IF e.is_bound AND EXISTS(SELECT FROM jsonb_each(e.trust) item
          WHERE item.value->>'status'='REJECTED') THEN RETURN 'TRUST_REVOKED'; END IF;
        content:=coalesce(e.provenance->'content','{}'::jsonb);
        proof:=e.provenance->'temporalProof'->'payload';
        identities:=jsonb_build_array(jsonb_build_object('type','SOURCE','key',content->>'keyId'));
        IF proof IS NOT NULL THEN identities:=identities||jsonb_build_array(jsonb_build_object(
          'type','WITNESS','key',proof->>'keyId','witness',proof->>'witnessId')); END IF;
        FOR identity IN SELECT value FROM jsonb_array_elements(identities) LOOP
          IF clock_timestamp()-now_at>=interval '1500 milliseconds'
            THEN RETURN 'TRUST_UNAVAILABLE'; END IF;
          dimension:=CASE WHEN identity->>'type'='SOURCE'
            THEN 'sourceAuthenticity' ELSE 'temporalAuthenticity' END;
          result:=e.trust->dimension;
          verified:=result->>'status'='VERIFIED';
          SELECT decision.body INTO authority FROM research.trust_decisions decision
            WHERE decision.version<=cap AND decision.body->>'sourceId'=e.source_id
              AND decision.source_id=e.source_id AND decision.key_id=identity->>'key'
              AND decision.body->>'keyId'=identity->>'key'
              AND decision.body->>'authorityType'=identity->>'type'
              AND decision.body->>'witnessId' IS NOT DISTINCT FROM identity->>'witness'
              AND (decision.body->>'effectiveAt')::timestamptz<=now_at
            ORDER BY decision.version DESC LIMIT 1;
          IF authority IS NULL THEN
            IF verified THEN RETURN 'TRUST_REVOKED'; END IF;
            CONTINUE;
          END IF;
          scope:=authority->'scope';
          IF (authority->>'revoked')::boolean IS DISTINCT FROM false
            OR (authority->>'compromiseTimeKnown')::boolean IS DISTINCT FROM true
            OR authority->>'compromisedAt' IS NOT NULL
            OR NOT ((authority->>'validFrom')::timestamptz<=now_at
              AND now_at<(authority->>'validTo')::timestamptz)
            THEN RETURN 'TRUST_REVOKED'; END IF;
          payload:=CASE WHEN identity->>'type'='SOURCE' THEN content ELSE proof END;
          IF verified AND (payload->>'signedAt' IS NULL
            OR NOT ((authority->>'validFrom')::timestamptz<=(payload->>'signedAt')::timestamptz
              AND (payload->>'signedAt')::timestamptz<(authority->>'validTo')::timestamptz)
            OR (payload->>'signedAt')::timestamptz>now_at) THEN RETURN 'TRUST_REVOKED'; END IF;
          key_bytes:=decode(authority->>'publicKey','base64');
          pinned:=CASE WHEN octet_length(key_bytes)=32
            AND encode(key_bytes,'base64')=authority->>'publicKey'
            THEN encode(sha256(key_bytes),'hex') ELSE NULL END;
          seen:=0;
          FOR reference IN SELECT value FROM
            jsonb_array_elements(coalesce(result->'evidenceRefs','[]'::jsonb)) LOOP
            total:=total+1;
            IF total>2048 OR clock_timestamp()-now_at>=interval '1500 milliseconds'
              THEN RETURN 'TRUST_UNAVAILABLE'; END IF;
            IF reference->>'kind'='KEY' AND reference->>'id'=identity->>'key' THEN
              seen:=seen+1;
              IF reference->>'fingerprint' IS DISTINCT FROM pinned
                THEN RETURN 'TRUST_REVOKED'; END IF;
            END IF;
          END LOOP;
          IF verified AND (seen=0 OR pinned IS NULL) THEN RETURN 'TRUST_REVOKED'; END IF;
          IF NOT coalesce(scope->'instrumentIds' ? e.instrument_id::text,false)
            OR NOT coalesce(scope->'kinds' ? e.kind,false)
            OR (scope ? 'startAt' AND NOT ((scope->>'startAt')::timestamptz<=e.start_at
              AND e.start_at<e.end_at AND e.end_at<=(scope->>'endAt')::timestamptz))
            OR (identity->>'type'='WITNESS' AND proof IS NOT NULL AND NOT
              coalesce(scope->'assertions' ? (proof->>'assertion'),false))
            THEN RETURN 'TRUST_REVOKED'; END IF;
        END LOOP;
        IF e.kind<>'CORPORATE_ACTION' OR NOT candidate_trusted THEN CONTINUE; END IF;
        conclusion:=e.statement->>'conclusion'; entries:=e.statement->'actions';
        IF jsonb_typeof(entries) IS DISTINCT FROM 'array' OR conclusion IS NULL
          OR conclusion NOT IN ('NO_ACTIONS','ACTIONS_PRESENT','UNKNOWN')
          OR (jsonb_array_length(entries)>0) IS DISTINCT FROM (conclusion='ACTIONS_PRESENT')
          THEN RETURN 'CORPORATE_ACTION_CONFLICT'; END IF;
        IF conclusion='NO_ACTIONS' THEN claims:=claims||jsonb_build_array(jsonb_build_object(
          'start',e.start_at,'end',e.end_at)); END IF;
        FOR action IN SELECT value FROM jsonb_array_elements(entries) LOOP
          total:=total+1;
          IF total>2048 OR clock_timestamp()-now_at>=interval '1500 milliseconds'
            THEN RETURN 'TRUST_UNAVAILABLE'; END IF;
          effective:=(action->>'effectiveAt')::timestamptz;
          IF effective IS NULL OR NOT(e.start_at<=effective AND effective<e.end_at)
            THEN RETURN 'CORPORATE_ACTION_CONFLICT'; END IF;
          IF NOT EXISTS(SELECT FROM jsonb_array_elements(coalesce(periods,'[]'::jsonb)) fold
            WHERE (fold->'warmupPeriod'->>'start')::timestamptz<=effective
              AND effective<(fold->'window'->>'testEnd')::timestamptz) THEN CONTINUE; END IF;
          amount:=coalesce(action->>'amount',action->>'ratio','1')::numeric;
          IF amount<0 OR amount IN ('NaN'::numeric,'Infinity'::numeric,'-Infinity'::numeric)
            OR (action->>'type'='SPLIT' AND amount<=0) OR action->>'type' IS NULL
            THEN RETURN 'CORPORATE_ACTION_CONFLICT'; END IF;
          actions:=actions||jsonb_build_array(jsonb_build_object('at',effective,
            'kind',action->>'type','amount',amount));
          IF action->>'type'<>'CASH_DIVIDEND' OR action->>'currency' IS DISTINCT FROM 'USD'
            THEN structural:=true; END IF;
        END LOOP;
      END LOOP;
      IF clock_timestamp()-now_at>=interval '1500 milliseconds'
        THEN RETURN 'TRUST_UNAVAILABLE'; END IF;
      IF EXISTS(WITH events AS (
          SELECT (claim->>'start')::timestamptz AS at,1 AS kind,
            (claim->>'end')::timestamptz AS edge FROM jsonb_array_elements(claims) claim
          UNION ALL SELECT (action_row.value->>'at')::timestamptz,2,NULL::timestamptz
            FROM jsonb_array_elements(actions) AS action_row(value)), scanned AS (
          SELECT at,kind,max(edge) OVER(ORDER BY at,kind ROWS UNBOUNDED PRECEDING) AS edge
            FROM events) SELECT FROM scanned WHERE kind=2 AND at<edge)
        OR EXISTS(SELECT FROM jsonb_array_elements(actions) AS action_row(value)
          GROUP BY (action_row.value->>'at')::timestamptz,action_row.value->>'kind'
          HAVING count(DISTINCT action_row.value->'amount')>1)
        THEN RETURN 'CORPORATE_ACTION_CONFLICT'; END IF;
      IF structural THEN RETURN 'STRUCTURAL_CORPORATE_ACTION'; END IF;
      RETURN NULL;
    EXCEPTION WHEN OTHERS THEN RETURN 'TRUST_UNAVAILABLE';
    END $$;
    REVOKE ALL ON FUNCTION backtest.current_retrospective_gate(uuid) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION backtest.current_retrospective_gate(uuid)
      TO atl_backtest_worker,atl_control_api;
    """)
    op.execute("""
    CREATE TABLE backtest.retrospective_execution_gates (
      run_id uuid PRIMARY KEY REFERENCES backtest.runs,
      code text NOT NULL CHECK(code IN ('LICENSE_DENIED','TRUST_REVOKED','TRUST_UNAVAILABLE',
        'STRUCTURAL_CORPORATE_ACTION','CORPORATE_ACTION_CONFLICT')),
      checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      fence bigint NOT NULL CHECK(fence>0));
    REVOKE ALL ON backtest.retrospective_execution_gates FROM PUBLIC;
    GRANT SELECT ON backtest.retrospective_execution_gates TO atl_backtest_worker,atl_control_api;
    GRANT INSERT ON backtest.retrospective_execution_gates TO atl_backtest_worker;
    GRANT SELECT ON backtest.source_license_status TO atl_control_api;
    CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON backtest.retrospective_execution_gates
      FOR EACH ROW EXECUTE FUNCTION research.reject_mutation();
    CREATE FUNCTION backtest.guard_retrospective_gate() RETURNS trigger LANGUAGE plpgsql
      SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE r backtest.runs; preview backtest.retrospective_reports;
      final_report backtest.retrospective_reports;
    BEGIN
      NEW.checked_at:=clock_timestamp();
      SELECT * INTO r FROM backtest.runs WHERE run_id=NEW.run_id;
      PERFORM backtest.assert_retrospective_fence(r.assessment_id,r.run_id);
      SELECT * INTO preview FROM backtest.retrospective_reports
        WHERE assessment_id=r.assessment_id AND phase='PREVIEW';
      SELECT * INTO final_report FROM backtest.retrospective_reports WHERE run_id=r.run_id;
      IF r.research_mode IS DISTINCT FROM 'RETROSPECTIVE_EXPLORATORY'
        OR NEW.fence<>r.fence OR final_report.report_id IS NULL OR preview.report_id IS NULL
        OR (final_report.body-ARRAY['reportId','runId','phase']) IS DISTINCT FROM
           (preview.body-ARRAY['reportId','runId','phase'])
        OR final_report.findings IS DISTINCT FROM preview.findings
        OR final_report.revision_references IS DISTINCT FROM preview.revision_references
        OR EXISTS(SELECT FROM backtest.results WHERE run_id=r.run_id)
        THEN RAISE EXCEPTION 'retrospective snapshot conflict'; END IF;
      IF NEW.code='LICENSE_DENIED' AND NOT EXISTS(
          SELECT FROM backtest.source_license_status WHERE status='DENIED')
        THEN RAISE EXCEPTION 'retrospective snapshot conflict'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION backtest.guard_retrospective_gate() FROM PUBLIC;
    CREATE TRIGGER fenced_insert BEFORE INSERT ON backtest.retrospective_execution_gates
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_retrospective_gate();
    CREATE FUNCTION backtest.guard_current_retrospective_result() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,backtest,pg_temp AS $$
    DECLARE a uuid;
    BEGIN
      SELECT assessment_id INTO a FROM backtest.runs WHERE run_id=NEW.run_id;
      IF a IS NOT NULL AND (EXISTS(SELECT FROM backtest.retrospective_execution_gates
          WHERE run_id=NEW.run_id) OR backtest.current_retrospective_gate(a) IS NOT NULL)
        THEN RAISE EXCEPTION 'retrospective current gate blocked'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION backtest.guard_current_retrospective_result() FROM PUBLIC;
    CREATE TRIGGER a_current_gate BEFORE INSERT ON backtest.results
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_current_retrospective_result();
    CREATE TRIGGER a_current_gate BEFORE INSERT ON backtest.manifests
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_current_retrospective_result();
    CREATE TRIGGER a_current_gate BEFORE INSERT ON backtest.fold_results
      FOR EACH ROW EXECUTE FUNCTION backtest.guard_current_retrospective_result();
    DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='atl_paper_control_api') THEN
      GRANT SELECT ON backtest.retrospective_execution_gates,backtest.source_license_status
        TO atl_paper_control_api;
      GRANT EXECUTE ON FUNCTION backtest.current_retrospective_gate(uuid) TO atl_paper_control_api;
      GRANT EXECUTE ON FUNCTION backtest.has_retrospective_correction(uuid)
        TO atl_paper_control_api;
    END IF; END $$;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION backtest.guard_retrospective_job() RETURNS trigger LANGUAGE plpgsql
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
      IF NEW.status='SUCCEEDED' AND
        (backtest.current_retrospective_gate((to_jsonb(NEW)->>'assessment_id')::uuid) IS NOT NULL
        OR (TG_TABLE_NAME='runs' AND EXISTS(SELECT FROM backtest.retrospective_execution_gates
          WHERE run_id=(to_jsonb(NEW)->>'run_id')::uuid)))
        THEN RAISE EXCEPTION 'retrospective current gate blocked'; END IF;
      IF TG_TABLE_NAME='runs' AND NEW.status='BLOCKED' AND EXISTS(
        SELECT FROM backtest.retrospective_execution_gates
          WHERE run_id=(to_jsonb(NEW)->>'run_id')::uuid) AND EXISTS(
        SELECT FROM backtest.results WHERE run_id=(to_jsonb(NEW)->>'run_id')::uuid)
        THEN RAISE EXCEPTION 'retrospective current gate blocked'; END IF;
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
          AND (body->>'overallStatus'=CASE WHEN NEW.status='BLOCKED' THEN 'BLOCKED' ELSE
            'EXPLORATORY_WITH_LIMITATIONS' END OR
            (TG_TABLE_NAME='runs' AND NEW.status='BLOCKED' AND EXISTS(
              SELECT FROM backtest.retrospective_execution_gates g WHERE
                g.run_id=(to_jsonb(NEW)->>'run_id')::uuid AND g.code=NEW.error_code
                AND g.fence=NEW.fence))))
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
    """)


def downgrade():
    raise RuntimeError("Retrospective evidence is retained; use the documented stop/drain rollback")
